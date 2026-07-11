"""Internal search pipeline service.

This service wires the backend modules into a real retrieval pipeline.
It is used by the Real Search FastAPI lifecycle endpoints.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Protocol

from pydantic import BaseModel, Field, model_validator

from scholar_agent.agents.judgement import JudgementAgent
from scholar_agent.agents.query_evolution import evolve_queries
from scholar_agent.agents.query_understanding import analyze_query
from scholar_agent.agents.refchain import ReferenceFetcher, expand_refchain
from scholar_agent.agents.reranker import rerank_papers
from scholar_agent.agents.retriever import (
    RetrievalOutput,
    SourceStats,
    retrieve_papers,
)
from scholar_agent.connectors.openalex import fetch_openalex_references_detailed
from scholar_agent.core.dedup import deduplicate_papers
from scholar_agent.core.diagnostics_schemas import (
    ConnectorDiagnostics,
    merge_connector_diagnostics,
)
from scholar_agent.core.paper_schemas import Paper
from scholar_agent.core.search_schemas import (
    BudgetStatus,
    EvolvedSubquery,
    JudgementResult,
    QueryAnalysis,
    QueryConstraint,
    QueryEvolutionRecord,
    RankedPaper,
    RefChainOptions,
    RefChainOutput,
    RunProfile,
    SearchPlan,
    SearchBudget,
    SearchSubquery,
    SUPPORTED_SEARCH_SOURCES,
    normalize_search_sources,
)
from scholar_agent.core.synthesis_schemas import SynthesisOutput
from scholar_agent.llm.provider import (
    LLMProviderError,
    OpenAICompatibleLLMClient,
    is_llm_enabled,
)
from scholar_agent.services.search_budget import BudgetedLLMClient, SearchBudgetRuntime

ENABLE_LLM_QUERY_UNDERSTANDING_ENV = "SCHOLAR_AGENT_ENABLE_LLM_QUERY_UNDERSTANDING"
ENABLE_LLM_JUDGEMENT_ENV = "SCHOLAR_AGENT_ENABLE_LLM_JUDGEMENT"


class RetrieverFn(Protocol):
    def __call__(
        self,
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        ...


class _RetrievalTaskResult(BaseModel):
    index: int
    output: RetrievalOutput


class SearchServiceOutput(BaseModel):
    search_plan: SearchPlan
    retrieval_outputs: list[RetrievalOutput] = Field(default_factory=list)
    query_evolution_records: list[QueryEvolutionRecord] = Field(default_factory=list)
    refchain_output: RefChainOutput | None = None
    synthesis_output: SynthesisOutput | None = None
    raw_count: int = 0
    deduplicated_count: int = 0
    judgements: list[JudgementResult] = Field(default_factory=list)
    ranked_papers: list[RankedPaper] = Field(default_factory=list)
    all_ranked_papers: list[RankedPaper] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_stats: list[SourceStats] = Field(default_factory=list)
    latency_seconds: float = 0.0
    stage_latencies: dict[str, float] = Field(default_factory=dict)
    llm_call_count: int = 0
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_total_tokens: int = 0
    search_diagnostics: ConnectorDiagnostics = Field(
        default_factory=ConnectorDiagnostics
    )
    reference_diagnostics: ConnectorDiagnostics = Field(
        default_factory=ConnectorDiagnostics
    )
    budget_status: BudgetStatus = Field(default_factory=BudgetStatus)

    @model_validator(mode="after")
    def derive_connector_diagnostics(self) -> "SearchServiceOutput":
        search = merge_connector_diagnostics(
            stat.diagnostics for stat in self.source_stats if stat.source != "refchain"
        )
        reference = merge_connector_diagnostics(
            stat.diagnostics for stat in self.source_stats if stat.source == "refchain"
        )
        if (
            self.search_diagnostics == ConnectorDiagnostics()
            and search != ConnectorDiagnostics()
        ):
            self.search_diagnostics = search
        if (
            self.reference_diagnostics == ConnectorDiagnostics()
            and reference != ConnectorDiagnostics()
        ):
            self.reference_diagnostics = reference
        return self


class SearchService:
    """Run the internal real search pipeline."""

    def __init__(
        self,
        retriever: RetrieverFn = retrieve_papers,
        reference_fetcher: ReferenceFetcher = fetch_openalex_references_detailed,
        max_workers: int = 4,
        llm_client: Any | None = None,
    ) -> None:
        self._retriever = retriever
        self._reference_fetcher = reference_fetcher
        self._max_workers = max(1, max_workers)
        self._llm_client = llm_client

    def run_search(
        self,
        query: str,
        top_k: int = 20,
        run_profile: RunProfile = "balanced",
        enable_refchain: bool = False,
        enable_query_evolution: bool = False,
        enable_synthesis: bool = True,
        current_year: int | None = None,
        enable_llm_query_understanding: bool | None = None,
        enable_llm_judgement: bool | None = None,
        sources_override: list[str] | None = None,
        explicit_constraints: QueryConstraint | None = None,
        budget: SearchBudget | None = None,
    ) -> SearchServiceOutput:
        runtime = SearchBudgetRuntime(budget)
        start = runtime.started_at
        stage_latencies: dict[str, float] = {}
        use_llm_query_understanding = (
            _env_flag(ENABLE_LLM_QUERY_UNDERSTANDING_ENV, default=False)
            if enable_llm_query_understanding is None
            else enable_llm_query_understanding
        )
        use_llm_judgement = (
            _env_flag(ENABLE_LLM_JUDGEMENT_ENV, default=False)
            if enable_llm_judgement is None
            else enable_llm_judgement
        )
        resolved_llm_client = self._resolve_llm_client(
            use_llm_query_understanding or use_llm_judgement
        )
        llm_client = (
            BudgetedLLMClient(resolved_llm_client, runtime)
            if resolved_llm_client is not None
            else None
        )
        stage_start = time.perf_counter()
        search_plan = analyze_query(
            query,
            top_k=top_k,
            run_profile=run_profile,
            enable_refchain=enable_refchain,
            enable_query_evolution=enable_query_evolution,
            current_year=current_year,
            use_llm=use_llm_query_understanding,
            llm_client=llm_client,
            explicit_constraints=explicit_constraints,
        )
        search_plan = _apply_sources_override(search_plan, sources_override)
        _add_stage_latency(
            stage_latencies,
            "query_understanding",
            time.perf_counter() - stage_start,
        )

        warnings: list[str] = list(search_plan.warnings)
        retrieval_outputs: list[RetrievalOutput] = []
        query_evolution_records: list[QueryEvolutionRecord] = []
        refchain_output: RefChainOutput | None = None
        raw_papers: list[Paper] = []
        source_stats: list[SourceStats] = []
        deduplicated: list[Paper] = []
        judgements: list[JudgementResult] = []
        all_ranked_papers: list[RankedPaper] = []
        ranked_papers: list[RankedPaper] = []

        if runtime.latency_stop_reason() is None:
            stage_start = time.perf_counter()
            initial_subqueries = search_plan.subqueries
            if initial_subqueries:
                retrieval_outputs = self._retrieve_subqueries(
                    search_plan,
                    subqueries=initial_subqueries,
                )
                runtime.record_search_round()
            _add_stage_latency(
                stage_latencies,
                "retrieval",
                time.perf_counter() - stage_start,
            )

            raw_papers, source_stats, retrieval_warnings = _collect_retrieval_outputs(
                retrieval_outputs
            )
            warnings.extend(retrieval_warnings)
            deduplicated = _apply_candidate_budget(
                deduplicate_papers(raw_papers),
                runtime,
                stage="initial_retrieval",
                source_order=search_plan.selected_sources,
            )
            stage_start = time.perf_counter()
            judgement_use_llm = (
                use_llm_judgement and runtime.latency_stop_reason() is None
            )
            judgements, _ = self._judge_papers(
                search_plan,
                deduplicated,
                use_llm=judgement_use_llm,
                llm_client=llm_client,
            )
            _add_stage_latency(
                stage_latencies,
                "judgement",
                time.perf_counter() - stage_start,
            )
            runtime.latency_stop_reason()
            stage_start = time.perf_counter()
            all_ranked_papers, ranked_papers = _rerank_all_and_top(
                search_plan.query_analysis,
                judgements,
                top_k,
            )
            _add_stage_latency(
                stage_latencies,
                "reranking",
                time.perf_counter() - stage_start,
            )

        if enable_query_evolution:
            evolution_stop_reason = _query_evolution_budget_stop_reason(
                runtime,
                len(deduplicated),
            )
            if evolution_stop_reason is not None:
                query_evolution_records.append(
                    QueryEvolutionRecord(
                        round_index=2,
                        skipped_reasons=[evolution_stop_reason],
                        warnings=[evolution_stop_reason],
                    )
                )
            else:
                used_queries = {subquery.query for subquery in search_plan.subqueries}
                stage_start = time.perf_counter()
                evolution_record = evolve_queries(
                    search_plan.query_analysis,
                    search_plan,
                    judgements,
                    ranked_papers,
                    used_queries,
                )
                evolution_record.round_index = 2
                query_evolution_records.append(evolution_record)
                warnings.extend(evolution_record.warnings)

                evolved_queries = _filter_new_evolved_queries(
                    evolution_record.generated_queries,
                    used_queries,
                )
                if len(evolved_queries) < len(evolution_record.generated_queries):
                    warnings.append("duplicate_evolved_query_skipped")
                _add_stage_latency(
                    stage_latencies,
                    "query_evolution",
                    time.perf_counter() - stage_start,
                )
                retrieval_stop_reason = _query_evolution_budget_stop_reason(
                    runtime,
                    len(deduplicated),
                    check_rounds=False,
                )
                if retrieval_stop_reason is not None:
                    evolution_record.skipped_reasons = _dedupe_warnings(
                        [*evolution_record.skipped_reasons, retrieval_stop_reason]
                    )
                    evolution_record.warnings = _dedupe_warnings(
                        [*evolution_record.warnings, retrieval_stop_reason]
                    )
                elif evolved_queries:
                    stage_start = time.perf_counter()
                    evolved_outputs = self._retrieve_evolved_queries(
                        search_plan,
                        evolved_queries,
                    )
                    runtime.record_search_round()
                    _add_stage_latency(
                        stage_latencies,
                        "retrieval",
                        time.perf_counter() - stage_start,
                    )
                    retrieval_outputs.extend(evolved_outputs)
                    (
                        evolved_papers,
                        evolved_source_stats,
                        evolved_warnings,
                    ) = _collect_retrieval_outputs(evolved_outputs)
                    raw_papers.extend(evolved_papers)
                    source_stats.extend(evolved_source_stats)
                    warnings.extend(evolved_warnings)

                    deduplicated = _apply_candidate_budget(
                        deduplicate_papers(raw_papers),
                        runtime,
                        stage="query_evolution",
                        source_order=search_plan.selected_sources,
                    )
                    stage_start = time.perf_counter()
                    judgement_use_llm = (
                        use_llm_judgement
                        and runtime.latency_stop_reason() is None
                    )
                    judgements, _ = self._judge_papers(
                        search_plan,
                        deduplicated,
                        use_llm=judgement_use_llm,
                        llm_client=llm_client,
                    )
                    _add_stage_latency(
                        stage_latencies,
                        "judgement",
                        time.perf_counter() - stage_start,
                    )
                    runtime.latency_stop_reason()
                    stage_start = time.perf_counter()
                    all_ranked_papers, ranked_papers = _rerank_all_and_top(
                        search_plan.query_analysis,
                        judgements,
                        top_k,
                    )
                    _add_stage_latency(
                        stage_latencies,
                        "reranking",
                        time.perf_counter() - stage_start,
                    )

        if enable_refchain:
            stage_start = time.perf_counter()
            runtime.latency_stop_reason()
            remaining_candidates = max(
                0,
                runtime.budget.max_candidate_papers - len(deduplicated),
            )
            refchain_output = expand_refchain(
                search_plan.query_analysis,
                ranked_papers,
                self._reference_fetcher,
                options=RefChainOptions(
                    max_total_references=min(50, remaining_candidates),
                ),
                budget_check=lambda: (
                    runtime.latency_stop_reason()
                    or runtime.candidate_stop_reason(len(deduplicated))
                ),
            )
            raw_papers.extend(refchain_output.references)
            warnings.extend(refchain_output.warnings)
            source_stats.append(
                SourceStats(
                    source="refchain",
                    query="refchain",
                    returned_count=len(refchain_output.references),
                    latency_seconds=refchain_output.latency_seconds,
                    error_message=(
                        f"refchain_connector_errors:"
                        f"{refchain_output.diagnostics.error_count}"
                        if refchain_output.diagnostics.error_count
                        else None
                    ),
                    diagnostics=refchain_output.diagnostics,
                )
            )
            _add_stage_latency(
                stage_latencies,
                "refchain",
                time.perf_counter() - stage_start,
            )
            if refchain_output.references:
                deduplicated = _apply_candidate_budget(
                    deduplicate_papers(raw_papers),
                    runtime,
                    stage="refchain",
                    source_order=search_plan.selected_sources,
                )
                stage_start = time.perf_counter()
                judgement_use_llm = (
                    use_llm_judgement and runtime.latency_stop_reason() is None
                )
                judgements, _ = self._judge_papers(
                    search_plan,
                    deduplicated,
                    use_llm=judgement_use_llm,
                    llm_client=llm_client,
                )
                _add_stage_latency(
                    stage_latencies,
                    "judgement",
                    time.perf_counter() - stage_start,
                )
                runtime.latency_stop_reason()
                stage_start = time.perf_counter()
                all_ranked_papers, ranked_papers = _rerank_all_and_top(
                    search_plan.query_analysis,
                    judgements,
                    top_k,
                )
                _add_stage_latency(
                    stage_latencies,
                    "reranking",
                    time.perf_counter() - stage_start,
                )

        warnings.extend(_judgement_warnings(judgements))
        refchain_raw_count = (
            refchain_output.record.raw_reference_count
            if refchain_output is not None
            else 0
        )
        output = SearchServiceOutput(
            search_plan=search_plan,
            retrieval_outputs=retrieval_outputs,
            query_evolution_records=query_evolution_records,
            refchain_output=refchain_output,
            raw_count=sum(output.raw_count for output in retrieval_outputs)
            + refchain_raw_count,
            deduplicated_count=len(deduplicated),
            judgements=judgements,
            ranked_papers=ranked_papers,
            all_ranked_papers=all_ranked_papers,
            warnings=[],
            source_stats=source_stats,
            latency_seconds=0.0,
            stage_latencies=stage_latencies,
            llm_call_count=runtime.used_llm_calls,
            llm_prompt_tokens=runtime.prompt_tokens,
            llm_completion_tokens=runtime.completion_tokens,
            llm_total_tokens=runtime.total_tokens,
            search_diagnostics=merge_connector_diagnostics(
                stat.diagnostics
                for stat in source_stats
                if stat.source != "refchain"
            ),
            reference_diagnostics=(
                refchain_output.diagnostics
                if refchain_output is not None
                else ConnectorDiagnostics()
            ),
        )
        output.warnings = _dedupe_warnings(
            [*warnings, *runtime.stop_reasons, *runtime.diagnostics]
        )
        if enable_synthesis and runtime.latency_stop_reason() is None:
            from scholar_agent.agents.synthesis import synthesize_answer

            stage_start = time.perf_counter()
            output.synthesis_output = synthesize_answer(output)
            _add_stage_latency(
                output.stage_latencies,
                "synthesis",
                time.perf_counter() - stage_start,
            )
        output.latency_seconds = time.perf_counter() - start
        output.budget_status = runtime.status()
        output.warnings = _dedupe_warnings(
            [
                *warnings,
                *runtime.stop_reasons,
                *runtime.diagnostics,
            ]
        )
        return output

    def _retrieve_subqueries(
        self,
        search_plan: SearchPlan,
        *,
        subqueries: list[SearchSubquery] | None = None,
    ) -> list[RetrievalOutput]:
        return self._retrieve_query_batch(
            search_plan.subqueries if subqueries is None else subqueries,
            selected_sources=search_plan.selected_sources,
            limit_per_source=search_plan.limit_per_source,
            failure_prefix="subquery_failed",
            failure_source="subquery",
        )

    def _judge_papers(
        self,
        search_plan: SearchPlan,
        papers: list[Paper],
        *,
        use_llm: bool,
        llm_client: Any | None,
    ) -> tuple[list[JudgementResult], int]:
        agent = JudgementAgent(llm_client=llm_client)
        judgements = agent.judge(
            search_plan.query_analysis,
            papers,
            use_llm=use_llm,
        )
        return judgements, agent.llm_call_count

    def _retrieve_evolved_queries(
        self,
        search_plan: SearchPlan,
        evolved_queries: list[EvolvedSubquery],
    ) -> list[RetrievalOutput]:
        subqueries = [
            SearchSubquery(
                query=query.query,
                source_hints=_limit_sources_to_selected(
                    query.source_hints,
                    search_plan.selected_sources,
                ),
                priority=query.priority,
                purpose=query.purpose,
            )
            for query in evolved_queries
        ]
        return self._retrieve_query_batch(
            subqueries,
            selected_sources=search_plan.selected_sources,
            limit_per_source=search_plan.limit_per_source,
            failure_prefix="evolved_query_failed",
            failure_source="evolved_query",
        )

    def _retrieve_query_batch(
        self,
        subqueries: list[SearchSubquery],
        *,
        selected_sources: list[str],
        limit_per_source: int,
        failure_prefix: str,
        failure_source: str,
    ) -> list[RetrievalOutput]:
        if not subqueries:
            return []

        worker_count = min(self._max_workers, len(subqueries))
        results: list[RetrievalOutput | None] = [None] * len(subqueries)

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(
                    self._retrieve_one_subquery,
                    index,
                    subqueries[index],
                    selected_sources,
                    limit_per_source,
                    failure_prefix,
                    failure_source,
                )
                for index in range(len(subqueries))
            ]
            for future in as_completed(futures):
                result = future.result()
                results[result.index] = result.output

        return [output for output in results if output is not None]

    def _retrieve_one_subquery(
        self,
        index: int,
        subquery: SearchSubquery,
        selected_sources: list[str],
        limit_per_source: int,
        failure_prefix: str,
        failure_source: str,
    ) -> _RetrievalTaskResult:
        sources = subquery.source_hints or selected_sources
        start = time.perf_counter()
        try:
            output = self._retriever(
                subquery.query,
                limit_per_source=limit_per_source,
                sources=sources,
            )
        except Exception as exc:  # noqa: BLE001 - isolate one subquery failure
            message = f"{failure_prefix}:{index}:{exc}"
            latency_seconds = time.perf_counter() - start
            output = RetrievalOutput(
                query=subquery.query,
                requested_sources=list(sources),
                raw_count=0,
                deduplicated_count=0,
                papers=[],
                source_stats=[
                    SourceStats(
                        source=failure_source,
                        query=subquery.query,
                        returned_count=0,
                        latency_seconds=latency_seconds,
                        error_message=message,
                    )
                ],
                warnings=[message],
                latency_seconds=latency_seconds,
            )
        return _RetrievalTaskResult(index=index, output=output)

    def _resolve_llm_client(self, enabled: bool) -> Any | None:
        if self._llm_client is not None:
            return self._llm_client
        if not enabled or not is_llm_enabled():
            return None
        try:
            return OpenAICompatibleLLMClient.from_env()
        except LLMProviderError:
            return None


def run_search(
    query: str,
    top_k: int = 20,
    run_profile: RunProfile = "balanced",
    enable_refchain: bool = False,
    enable_query_evolution: bool = False,
    enable_synthesis: bool = True,
    current_year: int | None = None,
    enable_llm_query_understanding: bool | None = None,
    enable_llm_judgement: bool | None = None,
    sources_override: list[str] | None = None,
    explicit_constraints: QueryConstraint | None = None,
    budget: SearchBudget | None = None,
) -> SearchServiceOutput:
    """Run the default internal search pipeline."""

    return SearchService().run_search(
        query,
        top_k=top_k,
        run_profile=run_profile,
        enable_refchain=enable_refchain,
        enable_query_evolution=enable_query_evolution,
        enable_synthesis=enable_synthesis,
        current_year=current_year,
        enable_llm_query_understanding=enable_llm_query_understanding,
        enable_llm_judgement=enable_llm_judgement,
        sources_override=sources_override,
        explicit_constraints=explicit_constraints,
        budget=budget,
    )


def _rerank_all_and_top(
    query_analysis: QueryAnalysis,
    judgements: list[JudgementResult],
    top_k: int,
) -> tuple[list[RankedPaper], list[RankedPaper]]:
    all_ranked_papers = rerank_papers(
        query_analysis,
        judgements,
        top_k=len(judgements),
    )
    if top_k <= 0:
        return all_ranked_papers, []
    return all_ranked_papers, all_ranked_papers[:top_k]


def _query_evolution_budget_stop_reason(
    runtime: SearchBudgetRuntime,
    candidate_count: int,
    *,
    check_rounds: bool = True,
) -> str | None:
    if (
        check_rounds
        and runtime.completed_search_rounds >= runtime.budget.max_search_rounds
    ):
        return runtime.stop("budget_stop:max_search_rounds")
    return (
        runtime.candidate_stop_reason(candidate_count)
        or runtime.latency_stop_reason()
    )


def _apply_candidate_budget(
    papers: list[Paper],
    runtime: SearchBudgetRuntime,
    *,
    stage: str,
    source_order: list[str],
) -> list[Paper]:
    limit = runtime.budget.max_candidate_papers
    if len(papers) <= limit:
        return papers

    truncated = _stable_source_coverage_truncate(
        papers,
        limit=limit,
        source_order=source_order,
    )
    runtime.record_candidate_truncation(
        stage=stage,
        before_count=len(papers),
        after_count=len(truncated),
    )
    return truncated


def _stable_source_coverage_truncate(
    papers: list[Paper],
    *,
    limit: int,
    source_order: list[str],
) -> list[Paper]:
    """Round-robin stable source buckets only when truncation is necessary."""

    ordered_sources = _dedupe_warnings(
        [*source_order, *SUPPORTED_SEARCH_SOURCES, "other"]
    )
    buckets: dict[str, list[Paper]] = {source: [] for source in ordered_sources}
    source_positions = {source: index for index, source in enumerate(ordered_sources)}
    for paper in papers:
        normalized_sources = {
            str(source).strip().casefold().replace("-", "_").replace(" ", "_")
            for source in paper.sources
        }
        bucket = min(
            (source for source in ordered_sources if source in normalized_sources),
            key=lambda source: source_positions[source],
            default="other",
        )
        buckets[bucket].append(paper)

    selected: list[Paper] = []
    offsets = {source: 0 for source in ordered_sources}
    while len(selected) < limit:
        added = False
        for source in ordered_sources:
            offset = offsets[source]
            if offset >= len(buckets[source]):
                continue
            selected.append(buckets[source][offset])
            offsets[source] += 1
            added = True
            if len(selected) >= limit:
                break
        if not added:
            break
    return selected


def _env_flag(env_name: str, *, default: bool) -> bool:
    raw_value = os.getenv(env_name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _add_stage_latency(
    stage_latencies: dict[str, float],
    stage: str,
    latency_seconds: float,
) -> None:
    stage_latencies[stage] = stage_latencies.get(stage, 0.0) + max(
        0.0,
        latency_seconds,
    )


def _apply_sources_override(
    search_plan: SearchPlan,
    sources_override: list[str] | None,
) -> SearchPlan:
    if sources_override is None:
        return search_plan

    selected_sources = _normalize_sources_override(sources_override)
    subqueries = [
        subquery.model_copy(update={"source_hints": list(selected_sources)})
        for subquery in search_plan.subqueries
    ]
    return search_plan.model_copy(
        update={
            "selected_sources": selected_sources,
            "subqueries": subqueries,
        }
    )


def _normalize_sources_override(raw_sources: list[str]) -> list[str]:
    selected_sources = normalize_search_sources(raw_sources)
    if not selected_sources:
        raise ValueError("source_preferences must contain at least one supported source")
    return selected_sources


def _limit_sources_to_selected(
    source_hints: list[str],
    selected_sources: list[str],
) -> list[str]:
    selected = set(selected_sources)
    return [source for source in source_hints if source in selected]


def _judgement_warnings(judgements: list[JudgementResult]) -> list[str]:
    warnings: list[str] = []
    for judgement in judgements:
        warnings.extend(judgement.warnings)
    return warnings


def _collect_retrieval_outputs(
    outputs: list[RetrievalOutput],
) -> tuple[list[Paper], list[SourceStats], list[str]]:
    papers: list[Paper] = []
    source_stats: list[SourceStats] = []
    warnings: list[str] = []
    for output in outputs:
        papers.extend(output.papers)
        source_stats.extend(output.source_stats)
        warnings.extend(output.warnings)
    return papers, source_stats, warnings


def _filter_new_evolved_queries(
    evolved_queries: list[EvolvedSubquery],
    used_queries: set[str],
) -> list[EvolvedSubquery]:
    seen = {_query_key(query) for query in used_queries}
    filtered: list[EvolvedSubquery] = []
    for query in evolved_queries:
        key = _query_key(query.query)
        if not key or key in seen:
            continue
        filtered.append(query)
        seen.add(key)
    return filtered


def _query_key(query: str) -> str:
    return " ".join(query.casefold().split())


def _dedupe_warnings(warnings: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for warning in warnings:
        item = warning.strip()
        if not item or item in seen:
            continue
        deduped.append(item)
        seen.add(item)
    return deduped

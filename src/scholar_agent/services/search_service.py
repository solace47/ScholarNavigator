"""Internal search pipeline service.

This service wires the backend modules into a real retrieval pipeline.
It is used by the Real Search FastAPI lifecycle endpoints.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Protocol

from pydantic import BaseModel, Field

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
from scholar_agent.connectors.openalex import fetch_openalex_references
from scholar_agent.core.dedup import deduplicate_papers
from scholar_agent.core.paper_schemas import Paper
from scholar_agent.core.search_schemas import (
    EvolvedSubquery,
    JudgementResult,
    QueryEvolutionRecord,
    RankedPaper,
    RefChainOutput,
    RunProfile,
    SearchPlan,
    SearchSubquery,
    SUPPORTED_SEARCH_SOURCES,
)
from scholar_agent.core.synthesis_schemas import SynthesisOutput

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
    warnings: list[str] = Field(default_factory=list)
    source_stats: list[SourceStats] = Field(default_factory=list)
    latency_seconds: float = 0.0
    llm_call_count: int = 0


class SearchService:
    """Run the internal real search pipeline."""

    def __init__(
        self,
        retriever: RetrieverFn = retrieve_papers,
        reference_fetcher: ReferenceFetcher = fetch_openalex_references,
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
    ) -> SearchServiceOutput:
        start = time.perf_counter()
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
        search_plan = analyze_query(
            query,
            top_k=top_k,
            run_profile=run_profile,
            enable_refchain=enable_refchain,
            enable_query_evolution=enable_query_evolution,
            current_year=current_year,
            use_llm=use_llm_query_understanding,
            llm_client=self._llm_client,
        )
        search_plan = _apply_sources_override(search_plan, sources_override)
        llm_call_count = _query_understanding_llm_call_count(
            search_plan,
            use_llm_query_understanding,
        )

        retrieval_outputs = self._retrieve_subqueries(search_plan)
        warnings: list[str] = list(search_plan.warnings)
        query_evolution_records: list[QueryEvolutionRecord] = []
        refchain_output: RefChainOutput | None = None

        raw_papers, source_stats, retrieval_warnings = _collect_retrieval_outputs(
            retrieval_outputs
        )
        warnings.extend(retrieval_warnings)
        deduplicated = deduplicate_papers(raw_papers)
        judgements, judgement_llm_calls = self._judge_papers(
            search_plan,
            deduplicated,
            use_llm=use_llm_judgement,
        )
        llm_call_count += judgement_llm_calls
        ranked_papers = rerank_papers(
            search_plan.query_analysis,
            judgements,
            top_k=top_k,
        )

        if enable_query_evolution:
            used_queries = {subquery.query for subquery in search_plan.subqueries}
            evolution_record = evolve_queries(
                search_plan.query_analysis,
                search_plan,
                judgements,
                ranked_papers,
                used_queries,
            )
            query_evolution_records.append(evolution_record)
            warnings.extend(evolution_record.warnings)

            evolved_queries = _filter_new_evolved_queries(
                evolution_record.generated_queries,
                used_queries,
            )
            if len(evolved_queries) < len(evolution_record.generated_queries):
                warnings.append("duplicate_evolved_query_skipped")

            if evolved_queries:
                evolved_outputs = self._retrieve_evolved_queries(
                    search_plan,
                    evolved_queries,
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

                deduplicated = deduplicate_papers(raw_papers)
                judgements, judgement_llm_calls = self._judge_papers(
                    search_plan,
                    deduplicated,
                    use_llm=use_llm_judgement,
                )
                llm_call_count += judgement_llm_calls
                ranked_papers = rerank_papers(
                    search_plan.query_analysis,
                    judgements,
                    top_k=top_k,
                )

        if enable_refchain:
            refchain_output = expand_refchain(
                search_plan.query_analysis,
                ranked_papers,
                self._reference_fetcher,
            )
            raw_papers.extend(refchain_output.references)
            warnings.extend(refchain_output.warnings)
            source_stats.append(
                SourceStats(
                    source="refchain",
                    returned_count=len(refchain_output.references),
                    latency_seconds=refchain_output.latency_seconds,
                    error_message=";".join(refchain_output.warnings) or None,
                )
            )
            if refchain_output.references:
                deduplicated = deduplicate_papers(raw_papers)
                judgements, judgement_llm_calls = self._judge_papers(
                    search_plan,
                    deduplicated,
                    use_llm=use_llm_judgement,
                )
                llm_call_count += judgement_llm_calls
                ranked_papers = rerank_papers(
                    search_plan.query_analysis,
                    judgements,
                    top_k=top_k,
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
            warnings=_dedupe_warnings(warnings),
            source_stats=source_stats,
            latency_seconds=time.perf_counter() - start,
            llm_call_count=llm_call_count,
        )
        if enable_synthesis:
            from scholar_agent.agents.synthesis import synthesize_answer

            output.synthesis_output = synthesize_answer(output)
        return output

    def _retrieve_subqueries(self, search_plan: SearchPlan) -> list[RetrievalOutput]:
        return self._retrieve_query_batch(
            search_plan.subqueries,
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
    ) -> tuple[list[JudgementResult], int]:
        agent = JudgementAgent(llm_client=self._llm_client)
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
                        returned_count=0,
                        latency_seconds=latency_seconds,
                        error_message=message,
                    )
                ],
                warnings=[message],
                latency_seconds=latency_seconds,
            )
        return _RetrievalTaskResult(index=index, output=output)


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
    )


def _env_flag(env_name: str, *, default: bool) -> bool:
    raw_value = os.getenv(env_name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _apply_sources_override(
    search_plan: SearchPlan,
    sources_override: list[str] | None,
) -> SearchPlan:
    if sources_override is None:
        return search_plan

    selected_sources, warnings = _normalize_sources_override(sources_override)
    subqueries = [
        subquery.model_copy(update={"source_hints": list(selected_sources)})
        for subquery in search_plan.subqueries
    ]
    return search_plan.model_copy(
        update={
            "selected_sources": selected_sources,
            "subqueries": subqueries,
            "warnings": _dedupe_warnings([*search_plan.warnings, *warnings]),
        }
    )


def _normalize_sources_override(raw_sources: list[str]) -> tuple[list[str], list[str]]:
    selected_sources: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for source in raw_sources:
        normalized = str(source).strip().lower().replace("-", "_").replace(" ", "_")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if normalized in SUPPORTED_SEARCH_SOURCES:
            selected_sources.append(normalized)
        elif normalized in {"semantic_scholar", "semanticscholar", "pubmed"}:
            canonical = "semantic_scholar" if "semantic" in normalized else "pubmed"
            warnings.append(f"source_preference_not_implemented:{canonical}")
        else:
            warnings.append(f"source_preference_unsupported:{normalized}")
    if not selected_sources:
        warnings.append("source_preferences_no_supported_sources")
    return selected_sources, warnings


def _limit_sources_to_selected(
    source_hints: list[str],
    selected_sources: list[str],
) -> list[str]:
    selected = set(selected_sources)
    return [source for source in source_hints if source in selected]


def _query_understanding_llm_call_count(
    search_plan: SearchPlan,
    enabled: bool,
) -> int:
    if not enabled:
        return 0
    warnings = search_plan.warnings
    if "llm_query_understanding_disabled" in warnings:
        return 0
    if any(
        warning == "llm_query_understanding_used"
        or warning.startswith("llm_query_understanding_failed:")
        for warning in warnings
    ):
        return 1
    return 0


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

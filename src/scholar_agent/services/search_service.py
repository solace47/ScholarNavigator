"""Internal search pipeline service.

This service wires the no-LLM backend modules into a real retrieval pipeline.
It is intentionally not connected to the FastAPI mock API yet.
"""

from __future__ import annotations

import time
from typing import Protocol

from pydantic import BaseModel, Field

from scholar_agent.agents.judgement import judge_papers
from scholar_agent.agents.query_understanding import analyze_query
from scholar_agent.agents.reranker import rerank_papers
from scholar_agent.agents.retriever import (
    RetrievalOutput,
    SourceStats,
    retrieve_papers,
)
from scholar_agent.core.dedup import deduplicate_papers
from scholar_agent.core.paper_schemas import Paper
from scholar_agent.core.search_schemas import (
    JudgementResult,
    RankedPaper,
    RunProfile,
    SearchPlan,
)


class RetrieverFn(Protocol):
    def __call__(
        self,
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        ...


class SearchServiceOutput(BaseModel):
    search_plan: SearchPlan
    retrieval_outputs: list[RetrievalOutput] = Field(default_factory=list)
    raw_count: int = 0
    deduplicated_count: int = 0
    judgements: list[JudgementResult] = Field(default_factory=list)
    ranked_papers: list[RankedPaper] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_stats: list[SourceStats] = Field(default_factory=list)
    latency_seconds: float = 0.0


class SearchService:
    """Run the internal no-LLM search pipeline."""

    def __init__(self, retriever: RetrieverFn = retrieve_papers) -> None:
        self._retriever = retriever

    def run_search(
        self,
        query: str,
        top_k: int = 20,
        run_profile: RunProfile = "balanced",
        enable_refchain: bool = False,
        enable_query_evolution: bool = False,
        current_year: int | None = None,
    ) -> SearchServiceOutput:
        start = time.perf_counter()
        search_plan = analyze_query(
            query,
            top_k=top_k,
            run_profile=run_profile,
            enable_refchain=enable_refchain,
            enable_query_evolution=enable_query_evolution,
            current_year=current_year,
        )

        retrieval_outputs: list[RetrievalOutput] = []
        raw_papers: list[Paper] = []
        source_stats: list[SourceStats] = []
        warnings: list[str] = list(search_plan.warnings)

        for subquery in search_plan.subqueries:
            output = self._retriever(
                subquery.query,
                limit_per_source=search_plan.limit_per_source,
                sources=subquery.source_hints or search_plan.selected_sources,
            )
            retrieval_outputs.append(output)
            raw_papers.extend(output.papers)
            source_stats.extend(output.source_stats)
            warnings.extend(output.warnings)

        deduplicated = deduplicate_papers(raw_papers)
        judgements = judge_papers(search_plan.query_analysis, deduplicated)
        ranked_papers = rerank_papers(
            search_plan.query_analysis,
            judgements,
            top_k=top_k,
        )
        warnings.extend(_judgement_warnings(judgements))

        return SearchServiceOutput(
            search_plan=search_plan,
            retrieval_outputs=retrieval_outputs,
            raw_count=sum(output.raw_count for output in retrieval_outputs),
            deduplicated_count=len(deduplicated),
            judgements=judgements,
            ranked_papers=ranked_papers,
            warnings=_dedupe_warnings(warnings),
            source_stats=source_stats,
            latency_seconds=time.perf_counter() - start,
        )


def run_search(
    query: str,
    top_k: int = 20,
    run_profile: RunProfile = "balanced",
    enable_refchain: bool = False,
    enable_query_evolution: bool = False,
    current_year: int | None = None,
) -> SearchServiceOutput:
    """Run the default internal search pipeline."""

    return SearchService().run_search(
        query,
        top_k=top_k,
        run_profile=run_profile,
        enable_refchain=enable_refchain,
        enable_query_evolution=enable_query_evolution,
        current_year=current_year,
    )


def _judgement_warnings(judgements: list[JudgementResult]) -> list[str]:
    warnings: list[str] = []
    for judgement in judgements:
        warnings.extend(judgement.warnings)
    return warnings


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


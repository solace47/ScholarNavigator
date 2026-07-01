from __future__ import annotations

from collections.abc import Callable

import pytest

from scholar_agent.agents.retriever import RetrievalOutput, SourceStats
from scholar_agent.agents.query_understanding import analyze_query
from scholar_agent.core.evaluation_schemas import EvalGoldPaper, EvalQuery
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers
from scholar_agent.evaluation.offline_evaluator import evaluate_search_service_offline


def make_paper(
    title: str,
    *,
    doi: str,
    openalex_id: str | None = None,
    citation_count: int = 10,
) -> Paper:
    return Paper(
        title=title,
        authors=["Alice"],
        year=2025,
        venue="ACL",
        abstract=(
            "This paper studies LLM reranking methods for scientific literature "
            "retrieval with robust evidence and ranking."
        ),
        identifiers=PaperIdentifiers(doi=doi, openalex_id=openalex_id),
        sources=["fixture"],
        citation_count=citation_count,
    )


BASELINE_PAPER = make_paper(
    "LLM Reranking for Scientific Literature Retrieval",
    doi="10.123/baseline",
    openalex_id="WBASE",
    citation_count=100,
)
EVOLVED_PAPER = make_paper(
    "Recent Advances in LLM Reranking for Scientific Literature Retrieval",
    doi="10.123/evolved",
    openalex_id="WEVOLVED",
    citation_count=80,
)
REFCHAIN_PAPER = make_paper(
    "Reference Evidence for LLM Reranking in Literature Retrieval",
    doi="10.123/refchain",
    openalex_id="WREF",
    citation_count=60,
)


def make_output(
    query: str,
    papers: list[Paper],
    *,
    warning: str | None = None,
    error_message: str | None = None,
) -> RetrievalOutput:
    warnings = [warning] if warning else []
    return RetrievalOutput(
        query=query,
        requested_sources=["openalex", "arxiv"],
        raw_count=len(papers),
        deduplicated_count=len(papers),
        papers=papers,
        source_stats=[
            SourceStats(
                source="fixture",
                returned_count=len(papers),
                latency_seconds=0.01,
                error_message=error_message,
            )
        ],
        warnings=warnings,
        latency_seconds=0.01,
    )


def make_eval_query() -> EvalQuery:
    return EvalQuery(
        query_id="q1",
        query="latest LLM reranking methods for scientific literature retrieval",
        gold_papers=[
            EvalGoldPaper(doi="10.123/baseline"),
            EvalGoldPaper(doi="10.123/evolved"),
            EvalGoldPaper(doi="10.123/refchain"),
        ],
        top_k_values=[5, 10, 20],
        current_year=2026,
    )


def make_fake_retriever(
    *,
    include_warning: bool = False,
) -> Callable[[str, int, list[str] | None], RetrievalOutput]:
    initial_queries = {
        subquery.query
        for subquery in analyze_query(
            make_eval_query().query,
            current_year=2026,
        ).subqueries
    }

    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        del limit_per_source, sources
        if query not in initial_queries:
            return make_output(query, [EVOLVED_PAPER])
        return make_output(
            query,
            [BASELINE_PAPER],
            warning="fixture_openalex_warning" if include_warning else None,
            error_message="fixture HTTP 503" if include_warning else None,
        )

    return fake_retriever


def fake_reference_fetcher(paper: Paper, limit: int = 20) -> list[Paper]:
    assert limit > 0
    if paper.identifiers.doi in {"10.123/baseline", "10.123/evolved"}:
        return [REFCHAIN_PAPER]
    return []


def forbidden_reference_fetcher(paper: Paper, limit: int = 20) -> list[Paper]:
    del paper, limit
    raise AssertionError("reference fetcher should not run for baseline/evolution")


def test_baseline_can_run_offline() -> None:
    result = evaluate_search_service_offline(
        [make_eval_query()],
        retriever=make_fake_retriever(),
        reference_fetcher=forbidden_reference_fetcher,
        groups=["baseline"],
        max_workers=1,
    )

    baseline = result.query_results[0].group_results["baseline"]

    assert not baseline.failed
    assert baseline.metrics.raw_count > 0
    assert baseline.metrics.recall_at_k[20] == pytest.approx(1 / 3)
    assert baseline.metrics.precision_at_k[5] > 0
    assert baseline.ranked_paper_ids
    assert result.aggregate_metrics["baseline"].recall_at_k[20] == pytest.approx(1 / 3)


def test_query_evolution_adds_candidates_and_can_improve_recall() -> None:
    result = evaluate_search_service_offline(
        [make_eval_query()],
        retriever=make_fake_retriever(),
        reference_fetcher=forbidden_reference_fetcher,
        groups=["baseline", "query_evolution"],
        max_workers=1,
    )

    groups = result.query_results[0].group_results
    baseline = groups["baseline"]
    evolved = groups["query_evolution"]

    assert evolved.raw_count > baseline.raw_count
    assert evolved.metrics.recall_at_k[20] > baseline.metrics.recall_at_k[20]
    assert "doi:10.123/evolved" in evolved.ranked_paper_ids


def test_refchain_references_participate_in_scoring() -> None:
    result = evaluate_search_service_offline(
        [make_eval_query()],
        retriever=make_fake_retriever(),
        reference_fetcher=fake_reference_fetcher,
        groups=["query_evolution", "refchain"],
        max_workers=1,
    )

    groups = result.query_results[0].group_results
    evolved = groups["query_evolution"]
    refchain = groups["refchain"]

    assert refchain.raw_count > evolved.raw_count
    assert refchain.metrics.recall_at_k[20] > evolved.metrics.recall_at_k[20]
    assert "doi:10.123/refchain" in refchain.ranked_paper_ids
    assert refchain.metrics.per_source_returned_count["refchain"] >= 1


def test_connector_warning_and_error_enter_error_metrics() -> None:
    result = evaluate_search_service_offline(
        [make_eval_query()],
        retriever=make_fake_retriever(include_warning=True),
        reference_fetcher=forbidden_reference_fetcher,
        groups=["baseline"],
        max_workers=1,
    )

    baseline = result.query_results[0].group_results["baseline"]

    assert "fixture_openalex_warning" in baseline.warnings
    assert baseline.metrics.source_error_count > 0
    assert baseline.metrics.source_error_rate > 0
    assert baseline.metrics.warning_count == 1
    assert baseline.metrics.query_warning_rate == 1.0


def test_evaluator_uses_injected_fixtures_without_real_connectors() -> None:
    retriever_calls: list[str] = []
    reference_calls: list[str] = []

    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        del limit_per_source, sources
        retriever_calls.append(query)
        return make_output(query, [BASELINE_PAPER])

    def fake_fetcher(paper: Paper, limit: int = 20) -> list[Paper]:
        del limit
        reference_calls.append(paper.identifiers.doi or paper.title)
        return [REFCHAIN_PAPER]

    result = evaluate_search_service_offline(
        [make_eval_query()],
        retriever=fake_retriever,
        reference_fetcher=fake_fetcher,
        groups=["refchain"],
        max_workers=1,
    )

    assert retriever_calls
    assert reference_calls
    assert not result.query_results[0].group_results["refchain"].failed

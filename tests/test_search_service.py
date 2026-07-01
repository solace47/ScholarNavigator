from __future__ import annotations

import pytest

from scholar_agent.agents.retriever import RetrievalOutput, SourceStats
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers
from scholar_agent.services.search_service import SearchService


def make_paper(
    title: str,
    *,
    doi: str | None = None,
    year: int | None = 2024,
    citation_count: int = 0,
    sources: list[str] | None = None,
) -> Paper:
    return Paper(
        title=title,
        authors=["Alice"],
        year=year,
        venue="ACL",
        abstract="This paper studies LLM reranking for scientific literature retrieval.",
        identifiers=PaperIdentifiers(doi=doi),
        sources=sources or ["openalex"],
        citation_count=citation_count,
    )


def make_output(
    query: str,
    papers: list[Paper],
    *,
    warnings: list[str] | None = None,
) -> RetrievalOutput:
    return RetrievalOutput(
        query=query,
        requested_sources=["openalex", "arxiv"],
        raw_count=len(papers),
        deduplicated_count=len(papers),
        papers=papers,
        source_stats=[
            SourceStats(
                source="openalex",
                returned_count=len(papers),
                latency_seconds=0.01,
            )
        ],
        warnings=warnings or [],
        latency_seconds=0.01,
    )


def test_run_search_complete_pipeline_with_injected_retriever() -> None:
    calls: list[tuple[str, int, list[str] | None]] = []

    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        calls.append((query, limit_per_source, sources))
        return make_output(
            query,
            [
                make_paper(
                    f"LLM Reranking for Retrieval {len(calls)}",
                    doi=f"10.123/{len(calls)}",
                    citation_count=10,
                )
            ],
        )

    output = SearchService(retriever=fake_retriever).run_search(
        "latest LLM reranking retrieval papers",
        top_k=5,
        current_year=2026,
    )

    assert output.search_plan.subqueries
    assert len(calls) == len(output.search_plan.subqueries)
    assert len(output.retrieval_outputs) == len(output.search_plan.subqueries)
    assert output.raw_count == len(output.search_plan.subqueries)
    assert output.deduplicated_count == len(output.judgements)
    assert output.ranked_papers
    assert output.latency_seconds >= 0
    assert all(call[1] == output.search_plan.limit_per_source for call in calls)
    assert all(call[2] == ["openalex", "arxiv"] for call in calls)


def test_run_search_deduplicates_across_subqueries() -> None:
    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        return make_output(
            query,
            [
                make_paper("Shared LLM Reranking Paper", doi="10.123/shared"),
                make_paper(
                    f"Unique LLM Reranking Paper {query[:8]}",
                    doi=f"10.123/{sum(ord(char) for char in query)}",
                ),
            ],
        )

    output = SearchService(retriever=fake_retriever).run_search(
        "latest LLM reranking retrieval benchmark papers",
        run_profile="high_recall",
        current_year=2026,
    )

    assert len(output.search_plan.subqueries) > 1
    assert output.raw_count == len(output.search_plan.subqueries) * 2
    assert output.deduplicated_count < output.raw_count
    assert sum(
        1
        for judgement in output.judgements
        if judgement.paper.identifiers.doi == "10.123/shared"
    ) == 1


def test_run_search_aggregates_warnings() -> None:
    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        return make_output(
            query,
            [make_paper("Clinical LLM Retrieval Paper", doi="10.123/clinical")],
            warnings=["mock_retriever_warning"],
        )

    output = SearchService(retriever=fake_retriever).run_search(
        "recent clinical gene therapy PubMed retrieval papers",
        current_year=2026,
    )

    assert "pubmed_not_implemented" in output.warnings
    assert "mock_retriever_warning" in output.warnings
    assert output.warnings.count("mock_retriever_warning") == 1


def test_run_search_top_k_is_applied() -> None:
    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        return make_output(
            query,
            [
                make_paper(
                    "LLM Reranking for Literature Retrieval",
                    doi="10.123/a",
                ),
                make_paper(
                    "Neural Retrieval with Large Language Models",
                    doi="10.123/b",
                ),
                make_paper(
                    "Transformer Ranking for Scientific Search",
                    doi="10.123/c",
                ),
            ],
        )

    output = SearchService(retriever=fake_retriever).run_search(
        "LLM reranking retrieval papers",
        top_k=2,
        current_year=2026,
    )

    assert len(output.ranked_papers) == 2
    assert [paper.rank for paper in output.ranked_papers] == [1, 2]


def test_run_search_empty_query_raises_value_error() -> None:
    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        raise AssertionError("retriever should not be called for an empty query")

    with pytest.raises(ValueError):
        SearchService(retriever=fake_retriever).run_search("   ")


def test_run_search_uses_injected_retriever_without_network(monkeypatch) -> None:
    monkeypatch.setattr(
        "scholar_agent.services.search_service.retrieve_papers",
        lambda *args, **kwargs: pytest.fail("default retriever should not be used"),
    )
    called = {"value": False}

    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        called["value"] = True
        return make_output(
            query,
            [make_paper("LLM Reranking Retrieval Paper", doi="10.123/no-network")],
        )

    output = SearchService(retriever=fake_retriever).run_search(
        "LLM reranking retrieval papers",
        current_year=2026,
    )

    assert called["value"] is True
    assert output.ranked_papers

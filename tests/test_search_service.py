from __future__ import annotations

import time

import pytest

from scholar_agent.agents.query_understanding import analyze_query
from scholar_agent.agents.retriever import RetrievalOutput, SourceStats
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers
from scholar_agent.core.search_schemas import EvolvedSubquery, QueryEvolutionRecord
from scholar_agent.services import search_service
from scholar_agent.services.search_service import SearchService


def make_paper(
    title: str,
    *,
    doi: str | None = None,
    openalex_id: str | None = None,
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
        identifiers=PaperIdentifiers(doi=doi, openalex_id=openalex_id),
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

    def failing_reference_fetcher(paper: Paper, limit: int) -> list[Paper]:
        raise AssertionError("reference_fetcher should not run when refchain is disabled")

    output = SearchService(
        retriever=fake_retriever,
        reference_fetcher=failing_reference_fetcher,
    ).run_search(
        "latest LLM reranking retrieval papers",
        top_k=5,
        current_year=2026,
    )

    assert output.search_plan.subqueries
    assert len(calls) == len(output.search_plan.subqueries)
    assert len(output.retrieval_outputs) == len(output.search_plan.subqueries)
    assert output.query_evolution_records == []
    assert output.refchain_output is None
    assert output.raw_count == len(output.search_plan.subqueries)
    assert output.deduplicated_count == len(output.judgements)
    assert output.ranked_papers
    assert output.synthesis_output is not None
    assert output.synthesis_output.evidence_table
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


def test_run_search_preserves_retrieval_cache_hit_stats() -> None:
    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        return RetrievalOutput(
            query=query,
            requested_sources=["openalex"],
            raw_count=1,
            deduplicated_count=1,
            papers=[
                make_paper("LLM Reranking Retrieval Paper", doi="10.123/cache-hit")
            ],
            source_stats=[
                SourceStats(
                    source="openalex",
                    returned_count=1,
                    latency_seconds=0.0,
                    cache_hit=True,
                )
            ],
            warnings=["retrieval_cache_hit:openalex"],
            latency_seconds=0.0,
        )

    output = SearchService(retriever=fake_retriever).run_search(
        "LLM reranking retrieval papers",
        current_year=2026,
    )

    assert output.source_stats[0].source == "openalex"
    assert output.source_stats[0].cache_hit is True
    assert "retrieval_cache_hit:openalex" in output.warnings


def test_run_search_can_disable_synthesis() -> None:
    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        return make_output(
            query,
            [make_paper("LLM Reranking Retrieval Paper", doi="10.123/no-synthesis")],
        )

    output = SearchService(retriever=fake_retriever).run_search(
        "LLM reranking retrieval papers",
        enable_synthesis=False,
        current_year=2026,
    )

    assert output.ranked_papers
    assert output.synthesis_output is None


def test_synthesis_output_includes_source_warnings_and_errors() -> None:
    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        return RetrievalOutput(
            query=query,
            requested_sources=["openalex", "arxiv"],
            raw_count=1,
            deduplicated_count=1,
            papers=[
                make_paper(
                    "LLM Reranking Retrieval Paper",
                    doi="10.123/source-error",
                )
            ],
            source_stats=[
                SourceStats(
                    source="openalex",
                    returned_count=0,
                    latency_seconds=0.01,
                    error_message="HTTP Error 503: Service Unavailable",
                ),
                SourceStats(
                    source="arxiv",
                    returned_count=1,
                    latency_seconds=0.01,
                ),
            ],
            warnings=["retriever_warning"],
            latency_seconds=0.02,
        )

    output = SearchService(retriever=fake_retriever).run_search(
        "LLM reranking retrieval papers",
        current_year=2026,
    )

    assert output.synthesis_output is not None
    assert "retriever_warning" in output.synthesis_output.limitations
    assert (
        "source_error:openalex:HTTP Error 503: Service Unavailable"
        in output.synthesis_output.limitations
    )
    expected_error_count = sum(
        1 for stats in output.source_stats if stats.error_message
    )
    assert (
        output.synthesis_output.citation_coverage.source_error_count
        == expected_error_count
    )


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


def test_run_search_with_query_evolution_retrieves_evolved_queries() -> None:
    query = "latest LLM reranking retrieval papers"
    expected_plan = analyze_query(query, current_year=2026)
    initial_queries = {subquery.query for subquery in expected_plan.subqueries}
    calls: list[str] = []

    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        calls.append(query)
        if query in initial_queries:
            return make_output(
                query,
                [
                    make_paper(
                        "LLM Reranking for Scientific Literature Retrieval",
                        doi=f"10.123/initial-{len(calls)}",
                        citation_count=1,
                    )
                ],
            )
        return make_output(
            query,
            [
                make_paper(
                    "Advanced LLM Reranking for Scientific Literature Retrieval",
                    doi=f"10.123/evolved-{len(calls)}",
                    citation_count=500,
                    sources=["openalex", "arxiv"],
                )
            ],
            warnings=["evolved_retriever_warning"],
        )

    output = SearchService(retriever=fake_retriever).run_search(
        query,
        top_k=5,
        enable_query_evolution=True,
        current_year=2026,
    )

    assert output.query_evolution_records
    assert output.query_evolution_records[0].generated_queries
    assert len(calls) > len(initial_queries)
    assert any(call not in initial_queries for call in calls)
    assert output.raw_count == len(output.retrieval_outputs)
    assert "evolved_retriever_warning" in output.warnings


def test_query_evolution_results_participate_in_final_ranking() -> None:
    query = "latest LLM reranking retrieval papers"
    expected_plan = analyze_query(query, current_year=2026)
    initial_queries = {subquery.query for subquery in expected_plan.subqueries}

    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        if query in initial_queries:
            return make_output(
                query,
                [
                    make_paper(
                        "LLM Reranking for Scientific Literature Retrieval",
                        doi=f"10.123/initial-{sum(ord(char) for char in query)}",
                        citation_count=0,
                    )
                ],
            )
        return make_output(
            query,
            [
                make_paper(
                    "High Authority LLM Reranking Retrieval Paper",
                    doi=f"10.123/evolved-{sum(ord(char) for char in query)}",
                    citation_count=1000,
                    sources=["openalex", "arxiv"],
                )
            ],
        )

    output = SearchService(retriever=fake_retriever).run_search(
        query,
        top_k=3,
        enable_query_evolution=True,
        current_year=2026,
    )

    assert output.query_evolution_records[0].generated_queries
    assert any(
        ranked.paper.title == "High Authority LLM Reranking Retrieval Paper"
        for ranked in output.ranked_papers
    )
    assert output.ranked_papers[0].paper.title == "High Authority LLM Reranking Retrieval Paper"


def test_query_evolution_used_queries_skip_duplicate_retrieval(monkeypatch) -> None:
    query = "LLM reranking retrieval papers"
    calls: list[str] = []
    captured_used_queries: set[str] = set()

    def fake_evolve_queries(
        query_analysis,
        search_plan,
        judgements,
        ranked_papers,
        used_queries,
    ) -> QueryEvolutionRecord:
        captured_used_queries.update(used_queries)
        duplicate_query = search_plan.subqueries[0].query
        return QueryEvolutionRecord(
            seed_count=1,
            generated_queries=[
                EvolvedSubquery(
                    query=duplicate_query,
                    source_hints=["openalex", "arxiv"],
                    priority=1,
                    purpose="duplicate_for_test",
                ),
                EvolvedSubquery(
                    query="unique evolved LLM reranking retrieval query",
                    source_hints=["openalex", "arxiv"],
                    priority=2,
                    purpose="unique_for_test",
                ),
            ],
        )

    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        calls.append(query)
        return make_output(
            query,
            [
                make_paper(
                    f"Paper for {query[:24]}",
                    doi=f"10.123/{sum(ord(char) for char in query)}",
                )
            ],
        )

    monkeypatch.setattr(search_service, "evolve_queries", fake_evolve_queries)

    output = SearchService(retriever=fake_retriever).run_search(
        query,
        enable_query_evolution=True,
        current_year=2026,
    )

    initial_queries = {subquery.query for subquery in output.search_plan.subqueries}
    duplicate_query = output.search_plan.subqueries[0].query
    assert captured_used_queries == initial_queries
    assert calls.count(duplicate_query) == 1
    assert "unique evolved LLM reranking retrieval query" in calls
    assert "duplicate_evolved_query_skipped" in output.warnings


def test_run_search_with_refchain_calls_reference_fetcher() -> None:
    calls: list[tuple[str, int]] = []

    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        return make_output(
            query,
            [
                make_paper(
                    "LLM Reranking for Scientific Literature Retrieval",
                    doi=f"10.123/{sum(ord(char) for char in query)}",
                    openalex_id=f"W{sum(ord(char) for char in query)}",
                )
            ],
        )

    def fake_reference_fetcher(paper: Paper, limit: int) -> list[Paper]:
        calls.append((paper.title, limit))
        return [
            make_paper(
                "Reference LLM Reranking Retrieval Paper",
                doi="10.123/refchain-reference",
                openalex_id="WREFCHAIN",
            )
        ]

    output = SearchService(
        retriever=fake_retriever,
        reference_fetcher=fake_reference_fetcher,
    ).run_search(
        "LLM reranking retrieval papers",
        enable_refchain=True,
        current_year=2026,
    )

    assert calls
    assert output.refchain_output is not None
    assert output.refchain_output.references
    assert output.raw_count == (
        sum(item.raw_count for item in output.retrieval_outputs) + len(calls)
    )
    assert output.source_stats[-1].source == "refchain"
    assert output.source_stats[-1].returned_count == len(calls)


def test_refchain_references_participate_in_final_ranking() -> None:
    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        return make_output(
            query,
            [
                make_paper(
                    "LLM Reranking for Scientific Literature Retrieval",
                    doi=f"10.123/{sum(ord(char) for char in query)}",
                    openalex_id=f"W{sum(ord(char) for char in query)}",
                    citation_count=0,
                )
            ],
        )

    def fake_reference_fetcher(paper: Paper, limit: int) -> list[Paper]:
        return [
            make_paper(
                "High Authority LLM Reranking Retrieval Reference",
                doi="10.123/high-authority-reference",
                openalex_id="WHIGHREF",
                citation_count=2000,
                sources=["openalex", "arxiv"],
            )
        ]

    output = SearchService(
        retriever=fake_retriever,
        reference_fetcher=fake_reference_fetcher,
    ).run_search(
        "LLM reranking retrieval papers",
        top_k=3,
        enable_refchain=True,
        current_year=2026,
    )

    assert output.refchain_output is not None
    assert any(
        ranked.paper.title == "High Authority LLM Reranking Retrieval Reference"
        for ranked in output.ranked_papers
    )
    assert output.ranked_papers[0].paper.title == (
        "High Authority LLM Reranking Retrieval Reference"
    )


def test_refchain_missing_seed_identifier_warning_is_aggregated() -> None:
    called = {"value": False}

    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        return make_output(
            query,
            [
                make_paper(
                    "LLM Reranking for Scientific Literature Retrieval",
                    citation_count=10,
                )
            ],
        )

    def fake_reference_fetcher(paper: Paper, limit: int) -> list[Paper]:
        called["value"] = True
        return []

    output = SearchService(
        retriever=fake_retriever,
        reference_fetcher=fake_reference_fetcher,
    ).run_search(
        "LLM reranking retrieval papers",
        enable_refchain=True,
        current_year=2026,
    )

    assert called["value"] is False
    assert output.refchain_output is not None
    assert "refchain_seed_missing_supported_identifier:1" in output.warnings
    assert output.source_stats[-1].source == "refchain"
    assert output.source_stats[-1].returned_count == 0


def test_query_evolution_and_refchain_can_run_together() -> None:
    query = "latest LLM reranking retrieval papers"
    expected_plan = analyze_query(query, current_year=2026)
    initial_queries = {subquery.query for subquery in expected_plan.subqueries}
    retriever_calls: list[str] = []
    reference_calls: list[str] = []

    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        retriever_calls.append(query)
        if query in initial_queries:
            return make_output(
                query,
                [
                    make_paper(
                        "LLM Reranking for Scientific Literature Retrieval",
                        doi=f"10.123/initial-{len(retriever_calls)}",
                        openalex_id=f"WINITIAL{len(retriever_calls)}",
                    )
                ],
            )
        return make_output(
            query,
            [
                make_paper(
                    "Evolved LLM Reranking Retrieval Paper",
                    doi=f"10.123/evolved-{len(retriever_calls)}",
                    openalex_id=f"WEVOLVED{len(retriever_calls)}",
                    citation_count=100,
                )
            ],
        )

    def fake_reference_fetcher(paper: Paper, limit: int) -> list[Paper]:
        reference_calls.append(paper.title)
        return [
            make_paper(
                "RefChain LLM Reranking Retrieval Reference",
                doi=f"10.123/ref-{len(reference_calls)}",
                openalex_id=f"WREF{len(reference_calls)}",
            )
        ]

    output = SearchService(
        retriever=fake_retriever,
        reference_fetcher=fake_reference_fetcher,
    ).run_search(
        query,
        enable_query_evolution=True,
        enable_refchain=True,
        current_year=2026,
    )

    assert output.query_evolution_records
    assert output.refchain_output is not None
    assert any(call not in initial_queries for call in retriever_calls)
    assert reference_calls
    assert output.refchain_output.references
    assert output.ranked_papers
    assert output.synthesis_output is not None
    assert output.synthesis_output.evidence_table


def test_run_search_can_use_llm_query_understanding_with_injected_client() -> None:
    llm_client = FakeLLMClient()
    calls: list[str] = []

    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        calls.append(query)
        return make_output(
            query,
            [
                make_paper(
                    "LLM Planned Reranking Retrieval Paper",
                    doi="10.123/llm-plan",
                )
            ],
        )

    output = SearchService(
        retriever=fake_retriever,
        llm_client=llm_client,
    ).run_search(
        "latest LLM reranking retrieval papers",
        current_year=2026,
        enable_llm_query_understanding=True,
    )

    assert llm_client.calls == 1
    assert output.search_plan.subqueries[0].query == "LLM reranking scientific retrieval"
    assert calls == ["LLM reranking scientific retrieval"]
    assert "llm_query_understanding_used" in output.warnings
    assert output.ranked_papers


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


def test_run_search_concurrent_mode_preserves_retrieval_output_order() -> None:
    query = "latest LLM reranking retrieval benchmark papers"
    expected_plan = analyze_query(
        query,
        run_profile="high_recall",
        current_year=2026,
    )
    expected_queries = [subquery.query for subquery in expected_plan.subqueries]
    delay_by_query = {
        subquery: (len(expected_queries) - index) * 0.01
        for index, subquery in enumerate(expected_queries)
    }

    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        time.sleep(delay_by_query[query])
        return make_output(
            query,
            [
                make_paper(
                    f"Paper for {query[:24]}",
                    doi=f"10.123/{sum(ord(char) for char in query)}",
                )
            ],
        )

    output = SearchService(retriever=fake_retriever, max_workers=4).run_search(
        query,
        run_profile="high_recall",
        current_year=2026,
    )

    assert [item.query for item in output.retrieval_outputs] == expected_queries
    assert len(output.ranked_papers) > 0


def test_run_search_subquery_failure_keeps_other_results_and_warnings() -> None:
    query = "latest LLM reranking retrieval benchmark papers"
    expected_plan = analyze_query(
        query,
        run_profile="high_recall",
        current_year=2026,
    )
    failing_query = expected_plan.subqueries[1].query

    def fake_retriever(
        query: str,
        limit_per_source: int = 20,
        sources: list[str] | None = None,
    ) -> RetrievalOutput:
        if query == failing_query:
            raise RuntimeError("mock subquery outage")
        return make_output(
            query,
            [
                make_paper(
                    f"Recovered Paper {query[:16]}",
                    doi=f"10.123/{sum(ord(char) for char in query)}",
                )
            ],
            warnings=["retriever_warning"],
        )

    output = SearchService(retriever=fake_retriever, max_workers=4).run_search(
        query,
        run_profile="high_recall",
        current_year=2026,
    )

    assert [item.query for item in output.retrieval_outputs] == [
        subquery.query for subquery in expected_plan.subqueries
    ]
    assert output.retrieval_outputs[1].raw_count == 0
    assert output.retrieval_outputs[1].source_stats[0].source == "subquery"
    assert output.retrieval_outputs[1].source_stats[0].error_message is not None
    assert "subquery_failed:1:mock subquery outage" in output.warnings
    assert output.warnings.count("retriever_warning") == 1
    assert output.raw_count == len(expected_plan.subqueries) - 1
    assert output.ranked_papers


class FakeLLMClient:
    def __init__(self) -> None:
        self.calls = 0

    def chat_json(self, messages, *, temperature=0, timeout=None):  # noqa: ANN001
        self.calls += 1
        return {
            "language": "en",
            "intent": "recent_progress",
            "domain": "machine_learning",
            "selected_sources": ["openalex", "arxiv"],
            "subqueries": [
                {
                    "query": "LLM reranking scientific retrieval",
                    "source_hints": ["openalex", "arxiv"],
                    "purpose": "llm_test_plan",
                }
            ],
            "warnings": [],
        }

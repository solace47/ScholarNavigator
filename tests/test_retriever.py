from __future__ import annotations

import pytest

from scholar_agent.connectors import ConnectorSearchResult
from scholar_agent.agents.retriever import clear_retrieval_cache, retrieve_papers
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers


@pytest.fixture(autouse=True)
def reset_retrieval_cache(monkeypatch: pytest.MonkeyPatch):
    clear_retrieval_cache()
    monkeypatch.delenv("SCHOLAR_AGENT_RETRIEVAL_CACHE", raising=False)
    monkeypatch.delenv("SCHOLAR_AGENT_RETRIEVAL_CACHE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("SCHOLAR_AGENT_RETRIEVAL_CACHE_MAX_ENTRIES", raising=False)
    yield
    clear_retrieval_cache()


def make_paper(
    title: str,
    *,
    doi: str | None = None,
    sources: list[str] | None = None,
    citation_count: int = 0,
    abstract: str = "",
) -> Paper:
    return Paper(
        title=title,
        authors=["Alice"],
        year=2024,
        abstract=abstract,
        identifiers=PaperIdentifiers(doi=doi),
        sources=sources or [],
        citation_count=citation_count,
    )


def test_retrieve_papers_aggregates_and_deduplicates(monkeypatch) -> None:
    def fake_openalex(query: str, limit: int) -> ConnectorSearchResult:
        assert query == "llm reranking"
        assert limit == 5
        return ConnectorSearchResult(
            papers=[
                make_paper(
                    "Shared Paper",
                    doi="10.123/shared",
                    sources=["openalex"],
                    citation_count=2,
                    abstract="short",
                ),
                make_paper("OpenAlex Only", sources=["openalex"], citation_count=1),
            ]
        )

    def fake_arxiv(query: str, limit: int) -> ConnectorSearchResult:
        return ConnectorSearchResult(
            papers=[
                make_paper(
                    "Shared Paper Extended",
                    doi="https://doi.org/10.123/shared",
                    sources=["arxiv"],
                    citation_count=9,
                    abstract="This abstract is longer and should be retained.",
                ),
                make_paper("arXiv Only", sources=["arxiv"], citation_count=0),
            ]
        )

    monkeypatch.setattr("scholar_agent.agents.retriever.search_openalex_detailed", fake_openalex)
    monkeypatch.setattr("scholar_agent.agents.retriever.search_arxiv_detailed", fake_arxiv)

    output = retrieve_papers("llm reranking", limit_per_source=5)

    assert output.query == "llm reranking"
    assert output.requested_sources == ["openalex", "arxiv"]
    assert output.raw_count == 4
    assert output.deduplicated_count == 3
    assert len(output.papers) == 3
    assert output.papers[0].sources == ["openalex", "arxiv"]
    assert output.papers[0].citation_count == 9
    assert output.papers[0].abstract == "This abstract is longer and should be retained."
    assert [stat.source for stat in output.source_stats] == ["openalex", "arxiv"]
    assert [stat.returned_count for stat in output.source_stats] == [2, 2]
    assert all(stat.error_message is None for stat in output.source_stats)
    assert all(stat.cache_hit is False for stat in output.source_stats)
    assert output.warnings == []
    assert output.latency_seconds >= 0


def test_retrieve_papers_single_source_error_keeps_other_results(monkeypatch) -> None:
    def failing_openalex(query: str, limit: int) -> ConnectorSearchResult:
        return ConnectorSearchResult(
            error_message="OpenAlex search failed: HTTP Error 503: Service Unavailable",
            warnings=["OpenAlex search failed: HTTP Error 503: Service Unavailable"],
        )

    def fake_arxiv(query: str, limit: int) -> ConnectorSearchResult:
        return ConnectorSearchResult(
            papers=[make_paper("arXiv Result", sources=["arxiv"])]
        )

    monkeypatch.setattr("scholar_agent.agents.retriever.search_openalex_detailed", failing_openalex)
    monkeypatch.setattr("scholar_agent.agents.retriever.search_arxiv_detailed", fake_arxiv)

    output = retrieve_papers("llm reranking")

    assert output.raw_count == 1
    assert output.deduplicated_count == 1
    assert output.papers[0].title == "arXiv Result"
    assert len(output.source_stats) == 2
    assert output.source_stats[0].source == "openalex"
    assert output.source_stats[0].returned_count == 0
    assert output.source_stats[0].cache_hit is False
    assert (
        output.source_stats[0].error_message
        == "OpenAlex search failed: HTTP Error 503: Service Unavailable"
    )
    assert output.source_stats[1].source == "arxiv"
    assert output.source_stats[1].returned_count == 1
    assert output.source_stats[1].cache_hit is False
    assert output.warnings == [
        "OpenAlex search failed: HTTP Error 503: Service Unavailable"
    ]


def test_retrieve_papers_unknown_source_warning(monkeypatch) -> None:
    def fake_arxiv(query: str, limit: int) -> ConnectorSearchResult:
        return ConnectorSearchResult(
            papers=[make_paper("arXiv Result", sources=["arxiv"])]
        )

    monkeypatch.setattr("scholar_agent.agents.retriever.search_arxiv_detailed", fake_arxiv)

    output = retrieve_papers("llm reranking", sources=["unknown", "arxiv"])

    assert output.requested_sources == ["unknown", "arxiv"]
    assert output.raw_count == 1
    assert output.deduplicated_count == 1
    assert output.source_stats[0].source == "unknown"
    assert output.source_stats[0].error_message == "unsupported_source:unknown"
    assert output.warnings == ["unsupported_source:unknown"]


def test_retrieve_papers_connector_warning_without_error_is_aggregated(monkeypatch) -> None:
    def fake_openalex(query: str, limit: int) -> ConnectorSearchResult:
        return ConnectorSearchResult(
            papers=[make_paper("OpenAlex Result", sources=["openalex"])],
            warnings=["OpenAlex parse warning"],
        )

    monkeypatch.setattr("scholar_agent.agents.retriever.search_openalex_detailed", fake_openalex)

    output = retrieve_papers("llm reranking", sources=["openalex"])

    assert output.source_stats[0].source == "openalex"
    assert output.source_stats[0].returned_count == 1
    assert output.source_stats[0].error_message is None
    assert output.warnings == ["OpenAlex parse warning"]


def test_retrieve_papers_cache_hit_reuses_successful_connector_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"openalex": 0}

    def fake_openalex(query: str, limit: int) -> ConnectorSearchResult:
        calls["openalex"] += 1
        return ConnectorSearchResult(
            papers=[make_paper("Cached OpenAlex Result", sources=["openalex"])],
            warnings=["OpenAlex parse warning"],
        )

    monkeypatch.setattr("scholar_agent.agents.retriever.search_openalex_detailed", fake_openalex)

    first = retrieve_papers("llm reranking cache", limit_per_source=5, sources=["openalex"])
    second = retrieve_papers("llm reranking cache", limit_per_source=5, sources=["openalex"])

    assert calls["openalex"] == 1
    assert first.source_stats[0].cache_hit is False
    assert second.source_stats[0].cache_hit is True
    assert second.papers[0].title == "Cached OpenAlex Result"
    assert "OpenAlex parse warning" in second.warnings
    assert "retrieval_cache_hit:openalex" in second.warnings


def test_retrieve_papers_cache_key_includes_query_and_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []

    def fake_openalex(query: str, limit: int) -> ConnectorSearchResult:
        calls.append((query, limit))
        return ConnectorSearchResult(
            papers=[
                make_paper(
                    f"Result {query} {limit}",
                    doi=f"10.123/{len(calls)}",
                    sources=["openalex"],
                )
            ]
        )

    monkeypatch.setattr("scholar_agent.agents.retriever.search_openalex_detailed", fake_openalex)

    retrieve_papers("query one", limit_per_source=5, sources=["openalex"])
    retrieve_papers("query two", limit_per_source=5, sources=["openalex"])
    retrieve_papers("query one", limit_per_source=10, sources=["openalex"])
    cached = retrieve_papers("query one", limit_per_source=5, sources=["openalex"])

    assert calls == [("query one", 5), ("query two", 5), ("query one", 10)]
    assert cached.source_stats[0].cache_hit is True


def test_retrieve_papers_cache_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"openalex": 0}

    def fake_openalex(query: str, limit: int) -> ConnectorSearchResult:
        calls["openalex"] += 1
        return ConnectorSearchResult(
            papers=[make_paper("No Cache Result", sources=["openalex"])]
        )

    monkeypatch.setenv("SCHOLAR_AGENT_RETRIEVAL_CACHE", "0")
    monkeypatch.setattr("scholar_agent.agents.retriever.search_openalex_detailed", fake_openalex)

    first = retrieve_papers("llm reranking no cache", sources=["openalex"])
    second = retrieve_papers("llm reranking no cache", sources=["openalex"])

    assert calls["openalex"] == 2
    assert first.source_stats[0].cache_hit is False
    assert second.source_stats[0].cache_hit is False


def test_retrieve_papers_does_not_cache_connector_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"openalex": 0}

    def flaky_openalex(query: str, limit: int) -> ConnectorSearchResult:
        calls["openalex"] += 1
        if calls["openalex"] == 1:
            return ConnectorSearchResult(
                error_message="OpenAlex search failed: timeout",
                warnings=["OpenAlex search failed: timeout"],
            )
        return ConnectorSearchResult(
            papers=[make_paper("Recovered OpenAlex Result", sources=["openalex"])]
        )

    monkeypatch.setattr("scholar_agent.agents.retriever.search_openalex_detailed", flaky_openalex)

    first = retrieve_papers("llm reranking flaky", sources=["openalex"])
    second = retrieve_papers("llm reranking flaky", sources=["openalex"])

    assert calls["openalex"] == 2
    assert first.source_stats[0].cache_hit is False
    assert first.source_stats[0].error_message == "OpenAlex search failed: timeout"
    assert second.source_stats[0].cache_hit is False
    assert second.source_stats[0].error_message is None
    assert second.papers[0].title == "Recovered OpenAlex Result"

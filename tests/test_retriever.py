from __future__ import annotations

from scholar_agent.agents.retriever import retrieve_papers
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers


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
    def fake_openalex(query: str, limit: int) -> list[Paper]:
        assert query == "llm reranking"
        assert limit == 5
        return [
            make_paper(
                "Shared Paper",
                doi="10.123/shared",
                sources=["openalex"],
                citation_count=2,
                abstract="short",
            ),
            make_paper("OpenAlex Only", sources=["openalex"], citation_count=1),
        ]

    def fake_arxiv(query: str, limit: int) -> list[Paper]:
        return [
            make_paper(
                "Shared Paper Extended",
                doi="https://doi.org/10.123/shared",
                sources=["arxiv"],
                citation_count=9,
                abstract="This abstract is longer and should be retained.",
            ),
            make_paper("arXiv Only", sources=["arxiv"], citation_count=0),
        ]

    monkeypatch.setattr("scholar_agent.agents.retriever.search_openalex", fake_openalex)
    monkeypatch.setattr("scholar_agent.agents.retriever.search_arxiv", fake_arxiv)

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
    assert output.warnings == []
    assert output.latency_seconds >= 0


def test_retrieve_papers_single_source_failure_keeps_other_results(monkeypatch) -> None:
    def failing_openalex(query: str, limit: int) -> list[Paper]:
        raise RuntimeError("openalex unavailable")

    def fake_arxiv(query: str, limit: int) -> list[Paper]:
        return [make_paper("arXiv Result", sources=["arxiv"])]

    monkeypatch.setattr("scholar_agent.agents.retriever.search_openalex", failing_openalex)
    monkeypatch.setattr("scholar_agent.agents.retriever.search_arxiv", fake_arxiv)

    output = retrieve_papers("llm reranking")

    assert output.raw_count == 1
    assert output.deduplicated_count == 1
    assert output.papers[0].title == "arXiv Result"
    assert len(output.source_stats) == 2
    assert output.source_stats[0].source == "openalex"
    assert output.source_stats[0].returned_count == 0
    assert output.source_stats[0].error_message == "openalex unavailable"
    assert output.source_stats[1].source == "arxiv"
    assert output.source_stats[1].returned_count == 1
    assert output.warnings == ["openalex failed: openalex unavailable"]


def test_retrieve_papers_unknown_source_warning(monkeypatch) -> None:
    def fake_arxiv(query: str, limit: int) -> list[Paper]:
        return [make_paper("arXiv Result", sources=["arxiv"])]

    monkeypatch.setattr("scholar_agent.agents.retriever.search_arxiv", fake_arxiv)

    output = retrieve_papers("llm reranking", sources=["unknown", "arxiv"])

    assert output.requested_sources == ["unknown", "arxiv"]
    assert output.raw_count == 1
    assert output.deduplicated_count == 1
    assert output.source_stats[0].source == "unknown"
    assert output.source_stats[0].error_message == "unsupported_source:unknown"
    assert output.warnings == ["unsupported_source:unknown"]


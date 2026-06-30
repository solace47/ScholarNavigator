from __future__ import annotations

from scholar_agent.core.dedup import deduplicate_papers
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers, PaperUrls


def make_paper(
    title: str,
    *,
    year: int | None = 2024,
    doi: str | None = None,
    arxiv_id: str | None = None,
    openalex_id: str | None = None,
    semantic_scholar_id: str | None = None,
    pubmed_id: str | None = None,
    sources: list[str] | None = None,
    citation_count: int = 0,
    abstract: str = "",
    authors: list[str] | None = None,
    venue: str | None = None,
    landing_page: str | None = None,
    pdf: str | None = None,
) -> Paper:
    return Paper(
        title=title,
        authors=authors or [],
        year=year,
        venue=venue,
        abstract=abstract,
        identifiers=PaperIdentifiers(
            doi=doi,
            arxiv_id=arxiv_id,
            openalex_id=openalex_id,
            semantic_scholar_id=semantic_scholar_id,
            pubmed_id=pubmed_id,
        ),
        urls=PaperUrls(landing_page=landing_page, pdf=pdf),
        sources=sources or [],
        citation_count=citation_count,
    )


def test_deduplicate_by_doi_and_merge_metadata() -> None:
    first = make_paper(
        "Short Title",
        doi="https://doi.org/10.1000/ABC",
        sources=["openalex"],
        citation_count=3,
        abstract="short",
        authors=["Alice"],
        openalex_id="W1",
        landing_page="https://example.org/a",
    )
    second = make_paper(
        "Longer and More Complete Title",
        doi="10.1000/abc",
        sources=["arxiv"],
        citation_count=12,
        abstract="This is a much longer abstract with more context.",
        authors=["Alice", "Bob"],
        arxiv_id="2401.12345v2",
        pdf="https://example.org/a.pdf",
        venue="ACL",
    )

    papers = deduplicate_papers([first, second])

    assert len(papers) == 1
    paper = papers[0]
    assert paper.title == "Longer and More Complete Title"
    assert paper.authors == ["Alice", "Bob"]
    assert paper.venue == "ACL"
    assert paper.abstract == "This is a much longer abstract with more context."
    assert paper.identifiers.doi == "https://doi.org/10.1000/ABC"
    assert paper.identifiers.arxiv_id == "2401.12345v2"
    assert paper.identifiers.openalex_id == "W1"
    assert paper.urls.landing_page == "https://example.org/a"
    assert paper.urls.pdf == "https://example.org/a.pdf"
    assert paper.sources == ["openalex", "arxiv"]
    assert paper.citation_count == 12


def test_deduplicate_by_arxiv_id_ignores_version() -> None:
    papers = deduplicate_papers(
        [
            make_paper("Version One", arxiv_id="2407.18940v1", sources=["arxiv"]),
            make_paper("Version Two", arxiv_id="2407.18940v3", sources=["openalex"]),
        ]
    )

    assert len(papers) == 1
    assert papers[0].sources == ["arxiv", "openalex"]


def test_deduplicate_by_title_and_near_year() -> None:
    papers = deduplicate_papers(
        [
            make_paper(
                "A Survey of LLM-Based Reranking!",
                year=2024,
                sources=["openalex"],
                citation_count=5,
            ),
            make_paper(
                "a survey of llm based reranking",
                year=2025,
                sources=["arxiv"],
                citation_count=7,
            ),
        ]
    )

    assert len(papers) == 1
    assert papers[0].sources == ["openalex", "arxiv"]
    assert papers[0].citation_count == 7


def test_deduplicate_keeps_distinct_title_when_year_far_apart() -> None:
    papers = deduplicate_papers(
        [
            make_paper("A Survey of LLM Based Reranking", year=2020),
            make_paper("A Survey of LLM Based Reranking", year=2024),
        ]
    )

    assert len(papers) == 2


def test_deduplicate_by_other_identifiers() -> None:
    papers = deduplicate_papers(
        [
            make_paper("OpenAlex Paper", openalex_id="https://openalex.org/W123"),
            make_paper("OpenAlex Paper Copy", openalex_id="w123"),
            make_paper("S2 Paper", semantic_scholar_id="S2-1"),
            make_paper("S2 Paper Copy", semantic_scholar_id="s2-1"),
            make_paper("PubMed Paper", pubmed_id="https://pubmed.ncbi.nlm.nih.gov/999/"),
            make_paper("PubMed Paper Copy", pubmed_id="999"),
        ]
    )

    assert len(papers) == 3


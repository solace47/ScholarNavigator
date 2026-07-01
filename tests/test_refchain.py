from __future__ import annotations

from scholar_agent.agents.refchain import expand_refchain
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers
from scholar_agent.core.search_schemas import (
    EvidenceItem,
    QueryAnalysis,
    QueryConstraint,
    RankedPaper,
    RefChainOptions,
    RerankScoreBreakdown,
)


def make_query_analysis() -> QueryAnalysis:
    return QueryAnalysis(
        original_query="LLM reranking for scientific literature retrieval",
        language="en",
        intent="survey",
        domain="machine_learning",
        constraints=QueryConstraint(
            methods=["reranking"],
            must_include_terms=["LLM", "retrieval"],
        ),
    )


def make_paper(
    title: str,
    *,
    openalex_id: str | None = None,
    doi: str | None = None,
) -> Paper:
    return Paper(
        title=title,
        authors=["Alice"],
        year=2024,
        venue="ACL",
        abstract="A paper about LLM reranking and retrieval.",
        identifiers=PaperIdentifiers(openalex_id=openalex_id, doi=doi),
        sources=["openalex"],
        citation_count=10,
    )


def make_ranked(
    paper: Paper,
    *,
    rank: int,
    category: str = "highly_relevant",
    final_score: float = 0.8,
) -> RankedPaper:
    return RankedPaper(
        rank=rank,
        paper=paper,
        final_score=final_score,
        category=category,
        score_breakdown=RerankScoreBreakdown(
            relevance_score=0.8,
            authority_score=0.5,
            timeliness_score=0.7,
            metadata_score=0.9,
            final_score=final_score,
            relevance_weight=0.62,
            authority_weight=0.25,
            timeliness_weight=0.08,
            metadata_weight=0.05,
        ),
        ranking_reason="metadata ranking",
        evidence=[EvidenceItem(source="title", text=paper.title, confidence=0.9)],
        matched_terms=["LLM", "retrieval"],
    )


def test_refchain_selects_only_relevant_seeds() -> None:
    seeds = [
        make_ranked(make_paper("Highly Relevant", openalex_id="W1"), rank=1),
        make_ranked(
            make_paper("Partial Relevant", openalex_id="W2"),
            rank=2,
            category="partially_relevant",
            final_score=0.6,
        ),
        make_ranked(
            make_paper("Irrelevant", openalex_id="W3"),
            rank=3,
            category="irrelevant",
            final_score=0.9,
        ),
        make_ranked(
            make_paper("Insufficient", openalex_id="W4"),
            rank=4,
            category="insufficient_evidence",
            final_score=0.9,
        ),
    ]
    called_titles: list[str] = []

    def fake_fetcher(paper: Paper, limit: int) -> list[Paper]:
        called_titles.append(paper.title)
        return [make_paper(f"Reference for {paper.title}", openalex_id=f"R{len(called_titles)}")]

    output = expand_refchain(make_query_analysis(), seeds, fake_fetcher)

    assert called_titles == ["Highly Relevant", "Partial Relevant"]
    assert [seed.paper.title for seed in output.record.seeds] == [
        "Highly Relevant",
        "Partial Relevant",
    ]
    assert len(output.references) == 2


def test_seed_and_reference_limits_are_applied() -> None:
    ranked = [
        make_ranked(make_paper(f"Seed {index}", openalex_id=f"W{index}"), rank=index)
        for index in range(1, 5)
    ]
    calls: list[tuple[str, int]] = []

    def fake_fetcher(paper: Paper, limit: int) -> list[Paper]:
        calls.append((paper.title, limit))
        return [
            make_paper(f"{paper.title} Reference {item}", openalex_id=f"{paper.identifiers.openalex_id}R{item}")
            for item in range(5)
        ]

    output = expand_refchain(
        make_query_analysis(),
        ranked,
        fake_fetcher,
        options=RefChainOptions(
            max_seed_papers=2,
            max_references_per_seed=2,
            max_total_references=3,
        ),
    )

    assert calls == [("Seed 1", 2), ("Seed 2", 1)]
    assert len(output.references) == 3
    assert len(output.reference_edges) == 3


def test_fake_fetcher_returns_references_and_edges() -> None:
    ranked = [
        make_ranked(make_paper("Seed", openalex_id="WSEED"), rank=1),
    ]

    def fake_fetcher(paper: Paper, limit: int) -> list[Paper]:
        return [
            make_paper("Reference One", openalex_id="WREF1"),
            make_paper("Reference Two", doi="10.123/ref-two"),
        ]

    output = expand_refchain(make_query_analysis(), ranked, fake_fetcher)

    assert [paper.title for paper in output.references] == [
        "Reference One",
        "Reference Two",
    ]
    assert [edge.seed_paper_id for edge in output.reference_edges] == [
        "openalex:wseed",
        "openalex:wseed",
    ]
    assert [edge.reference_paper_id for edge in output.reference_edges] == [
        "openalex:wref1",
        "doi:10.123/ref-two",
    ]
    assert output.record.reference_edges == output.reference_edges


def test_fetcher_exception_warns_and_continues() -> None:
    ranked = [
        make_ranked(make_paper("Failing Seed", openalex_id="WFAIL"), rank=1),
        make_ranked(make_paper("Recovered Seed", openalex_id="WOK"), rank=2),
    ]

    def fake_fetcher(paper: Paper, limit: int) -> list[Paper]:
        if paper.identifiers.openalex_id == "WFAIL":
            raise RuntimeError("mock outage")
        return [make_paper("Recovered Reference", openalex_id="WREF")]

    output = expand_refchain(make_query_analysis(), ranked, fake_fetcher)

    assert [paper.title for paper in output.references] == ["Recovered Reference"]
    assert "refchain_seed_failed:1:mock outage" in output.warnings


def test_missing_supported_identifier_is_skipped() -> None:
    ranked = [
        make_ranked(make_paper("No Identifier"), rank=1),
        make_ranked(make_paper("With DOI", doi="10.123/seed"), rank=2),
    ]
    called_titles: list[str] = []

    def fake_fetcher(paper: Paper, limit: int) -> list[Paper]:
        called_titles.append(paper.title)
        return [make_paper("DOI Reference", openalex_id="WREF")]

    output = expand_refchain(make_query_analysis(), ranked, fake_fetcher)

    assert called_titles == ["With DOI"]
    assert "refchain_seed_missing_supported_identifier:1" in output.warnings
    assert len(output.references) == 1


def test_refchain_is_single_layer_not_recursive() -> None:
    ranked = [
        make_ranked(make_paper("Seed", openalex_id="WSEED"), rank=1),
    ]
    calls: list[str] = []

    def fake_fetcher(paper: Paper, limit: int) -> list[Paper]:
        calls.append(paper.title)
        return [make_paper("Reference With Own References", openalex_id="WREF")]

    output = expand_refchain(make_query_analysis(), ranked, fake_fetcher)

    assert calls == ["Seed"]
    assert [paper.title for paper in output.references] == ["Reference With Own References"]


def test_refchain_output_is_stable() -> None:
    ranked = [
        make_ranked(make_paper("Seed", openalex_id="WSEED"), rank=1),
    ]

    def fake_fetcher(paper: Paper, limit: int) -> list[Paper]:
        return [make_paper("Reference", openalex_id="WREF")]

    first = expand_refchain(make_query_analysis(), ranked, fake_fetcher)
    second = expand_refchain(make_query_analysis(), ranked, fake_fetcher)

    first_dump = first.model_dump()
    second_dump = second.model_dump()
    first_dump["latency_seconds"] = 0.0
    second_dump["latency_seconds"] = 0.0
    first_dump["record"]["latency_seconds"] = 0.0
    second_dump["record"]["latency_seconds"] = 0.0

    assert first_dump == second_dump

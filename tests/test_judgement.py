from __future__ import annotations

from scholar_agent.agents.judgement import judge_papers
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers
from scholar_agent.core.search_schemas import QueryAnalysis, QueryConstraint, TimeRange


def make_query_analysis(
    *,
    original_query: str = "LLM reranking for scientific literature retrieval",
    venues: list[str] | None = None,
    time_range: TimeRange | None = None,
) -> QueryAnalysis:
    return QueryAnalysis(
        original_query=original_query,
        language="en",
        intent="paper_finding",
        domain="machine_learning",
        constraints=QueryConstraint(
            time_range=time_range,
            venues=venues or [],
            methods=["reranking"],
            datasets=[],
            domains=["machine_learning"],
            must_include_terms=["LLM", "reranking", "retrieval"],
        ),
    )


def make_paper(
    title: str,
    *,
    abstract: str = "",
    year: int | None = 2024,
    venue: str | None = None,
    sources: list[str] | None = None,
) -> Paper:
    return Paper(
        title=title,
        authors=["Alice"],
        year=year,
        venue=venue,
        abstract=abstract,
        identifiers=PaperIdentifiers(doi="10.123/test"),
        sources=sources or ["openalex"],
        citation_count=3,
    )


def test_strong_title_and_abstract_match_is_highly_relevant() -> None:
    query_analysis = make_query_analysis(
        venues=["ACL"],
        time_range=TimeRange(start_year=2020, end_year=2026),
    )
    paper = make_paper(
        "LLM Reranking for Scientific Literature Retrieval",
        abstract=(
            "This paper studies retrieval and reranking with large language models "
            "for scientific literature search."
        ),
        year=2024,
        venue="ACL",
    )

    result = judge_papers(query_analysis, [paper])[0]

    assert result.category == "highly_relevant"
    assert result.score >= 0.72
    assert {"LLM", "reranking", "retrieval"}.issubset(set(result.matched_terms))
    assert result.evidence


def test_background_only_match_is_weak_or_partial() -> None:
    query_analysis = make_query_analysis()
    paper = make_paper(
        "Scientific Literature Search Systems",
        abstract="This paper discusses retrieval tools for academic libraries.",
    )

    result = judge_papers(query_analysis, [paper])[0]

    assert result.category in {"weakly_relevant", "partially_relevant"}
    assert result.score < 0.72


def test_unrelated_paper_is_irrelevant() -> None:
    query_analysis = make_query_analysis()
    paper = make_paper(
        "Crystal Growth in Volcanic Rocks",
        abstract="We analyze mineral structures in geological samples.",
    )

    result = judge_papers(query_analysis, [paper])[0]

    assert result.category == "irrelevant"
    assert result.score < 0.25


def test_empty_title_and_abstract_is_insufficient_evidence() -> None:
    query_analysis = make_query_analysis()
    paper = make_paper("", abstract="")

    result = judge_papers(query_analysis, [paper])[0]

    assert result.category == "insufficient_evidence"
    assert result.score == 0
    assert "missing_title" in result.warnings
    assert "missing_abstract" in result.warnings
    assert result.evidence == []


def test_time_range_match_scores_higher_than_out_of_range_paper() -> None:
    query_analysis = make_query_analysis(
        original_query="LLM retrieval since 2020",
        time_range=TimeRange(start_year=2020, end_year=2026),
    )
    current = make_paper("LLM Retrieval", year=2024)
    old = make_paper("LLM Retrieval", year=2017)

    current_result, old_result = judge_papers(query_analysis, [current, old])

    assert current_result.score > old_result.score
    assert any(
        item.source == "metadata" and item.text == "year=2024"
        for item in current_result.evidence
    )
    assert any(
        item.source == "metadata" and item.text == "year=2017"
        for item in old_result.evidence
    )


def test_venue_constraint_match_increases_score() -> None:
    query_analysis = make_query_analysis(venues=["ACL"])
    matching = make_paper("LLM Retrieval", venue="ACL")
    non_matching = make_paper("LLM Retrieval", venue="KDD")

    matching_result, non_matching_result = judge_papers(
        query_analysis,
        [matching, non_matching],
    )

    assert matching_result.score > non_matching_result.score
    assert any(item.source == "venue" and item.text == "ACL" for item in matching_result.evidence)


def test_missing_year_with_time_range_adds_warning() -> None:
    query_analysis = make_query_analysis(time_range=TimeRange(start_year=2020))
    paper = make_paper("LLM Retrieval", year=None)

    result = judge_papers(query_analysis, [paper])[0]

    assert "missing_year_for_time_range" in result.warnings


def test_evidence_sources_and_text_are_metadata_grounded() -> None:
    query_analysis = make_query_analysis(
        venues=["SIGIR"],
        time_range=TimeRange(start_year=2020, end_year=2026),
    )
    paper = make_paper(
        "LLM Reranking for Retrieval",
        abstract="Reranking improves retrieval quality in literature search.",
        year=2024,
        venue="SIGIR",
        sources=["openalex", "arxiv"],
    )

    result = judge_papers(query_analysis, [paper])[0]

    assert result.evidence
    for item in result.evidence:
        assert item.source in {"title", "abstract", "venue", "metadata"}
        if item.source == "title":
            assert item.text in paper.title
        elif item.source == "abstract":
            assert item.text in paper.abstract
        elif item.source == "venue":
            assert item.text == paper.venue
        elif item.source == "metadata":
            assert item.text.startswith("year=")


def test_judge_papers_preserves_input_order() -> None:
    query_analysis = make_query_analysis()
    first = make_paper("LLM Reranking")
    second = make_paper("Crystal Growth")

    results = judge_papers(query_analysis, [first, second])

    assert [result.paper.title for result in results] == [
        "LLM Reranking",
        "Crystal Growth",
    ]


def test_threshold_parameters_affect_category() -> None:
    query_analysis = make_query_analysis(original_query="LLM retrieval")
    paper = make_paper("LLM Retrieval")

    default_result = judge_papers(query_analysis, [paper])[0]
    lower_high_threshold = judge_papers(
        query_analysis,
        [paper],
        threshold_high=0.5,
        threshold_partial=0.45,
        threshold_weak=0.25,
    )[0]

    assert default_result.category != "highly_relevant"
    assert lower_high_threshold.category == "highly_relevant"

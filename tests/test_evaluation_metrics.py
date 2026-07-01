from __future__ import annotations

import pytest

from scholar_agent.core.evaluation_schemas import EvalGoldPaper
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers
from scholar_agent.evaluation.metrics import (
    candidate_count_metrics,
    canonical_paper_id,
    error_rate_metrics,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


def make_paper(
    title: str,
    *,
    year: int | None = 2024,
    doi: str | None = None,
    arxiv_id: str | None = None,
    openalex_id: str | None = None,
    semantic_scholar_id: str | None = None,
    pubmed_id: str | None = None,
) -> Paper:
    return Paper(
        title=title,
        authors=["Alice"],
        year=year,
        venue="ACL",
        abstract="A test paper.",
        identifiers=PaperIdentifiers(
            doi=doi,
            arxiv_id=arxiv_id,
            openalex_id=openalex_id,
            semantic_scholar_id=semantic_scholar_id,
            pubmed_id=pubmed_id,
        ),
        sources=["fixture"],
    )


def test_canonical_paper_id_priority_and_normalization() -> None:
    assert (
        canonical_paper_id(
            doi="https://doi.org/10.1000/ABC",
            arxiv_id="2401.00001v2",
            openalex_id="https://openalex.org/W123",
        )
        == "doi:10.1000/abc"
    )
    assert (
        canonical_paper_id(make_paper("arXiv Paper", arxiv_id="arXiv:2401.00001v3"))
        == "arxiv:2401.00001"
    )
    assert (
        canonical_paper_id(
            make_paper("OpenAlex Paper", openalex_id="https://openalex.org/W123")
        )
        == "openalex:w123"
    )
    assert (
        canonical_paper_id(
            make_paper("Semantic Paper", semantic_scholar_id="CorpusId:12345")
        )
        == "s2:12345"
    )
    assert canonical_paper_id(make_paper("PubMed Paper", pubmed_id="PMID:987")) == (
        "pubmed:987"
    )
    assert (
        canonical_paper_id(title="A {LaTeX} Study!", year=2024)
        == "title_year:a latex study:2024"
    )


def test_recall_precision_and_mrr() -> None:
    gold = [
        EvalGoldPaper(doi="10.123/a"),
        EvalGoldPaper(doi="10.123/b"),
        EvalGoldPaper(doi="10.123/c"),
    ]
    ranked = [
        make_paper("Not Relevant", doi="10.999/x"),
        make_paper("Relevant B", doi="10.123/b"),
        make_paper("Relevant C", doi="10.123/c"),
    ]

    assert recall_at_k(ranked, gold, 2) == pytest.approx(1 / 3)
    assert precision_at_k(ranked, gold, 2) == pytest.approx(0.5)
    assert mrr(ranked, gold) == pytest.approx(0.5)


def test_binary_ndcg() -> None:
    gold = [
        EvalGoldPaper(doi="10.123/a"),
        EvalGoldPaper(doi="10.123/b"),
    ]
    ranked = [
        make_paper("Relevant A", doi="10.123/a"),
        make_paper("Irrelevant", doi="10.999/x"),
        make_paper("Relevant B", doi="10.123/b"),
    ]

    expected = (1.0 + 0.5) / (1.0 + (1.0 / 1.584962500721156))
    assert ndcg_at_k(ranked, gold, 3) == pytest.approx(expected)
    assert ndcg_at_k(ranked, gold, 5) > 0
    assert ndcg_at_k(ranked, gold, 10) > 0
    assert ndcg_at_k(ranked, gold, 20) > 0


def test_graded_ndcg() -> None:
    gold = [
        EvalGoldPaper(doi="10.123/a", relevance_grade=3),
        EvalGoldPaper(doi="10.123/b", relevance_grade=2),
        EvalGoldPaper(doi="10.123/c", relevance_grade=1),
    ]
    ideal = [
        make_paper("Grade 3", doi="10.123/a"),
        make_paper("Grade 2", doi="10.123/b"),
        make_paper("Grade 1", doi="10.123/c"),
    ]
    non_ideal = [
        make_paper("Grade 2", doi="10.123/b"),
        make_paper("Grade 3", doi="10.123/a"),
        make_paper("Grade 1", doi="10.123/c"),
    ]

    assert ndcg_at_k(ideal, gold, 3) == pytest.approx(1.0)
    assert 0 < ndcg_at_k(non_ideal, gold, 3) < 1


def test_empty_inputs_do_not_crash() -> None:
    assert canonical_paper_id({}) is None
    assert recall_at_k([], [], 10) == 0.0
    assert precision_at_k([], [], 10) == 0.0
    assert mrr([], []) == 0.0
    assert ndcg_at_k([], [], 10) == 0.0
    assert ndcg_at_k([make_paper("No Gold", doi="10.1/x")], [], 10) == 0.0


def test_candidate_count_metrics() -> None:
    metrics = candidate_count_metrics(
        10,
        7,
        ranked_count=5,
        source_stats=[
            {"source": "openalex", "returned_count": 3},
            {"source": "arxiv", "returned_count": 4},
            {"source": "openalex", "returned_count": 3},
        ],
    )

    assert metrics["raw_count"] == 10
    assert metrics["deduplicated_count"] == 7
    assert metrics["ranked_count"] == 5
    assert metrics["duplicate_count"] == 3
    assert metrics["duplicate_ratio"] == pytest.approx(0.3)
    assert metrics["per_source_returned_count"] == {"openalex": 6, "arxiv": 4}


def test_error_rate_metrics() -> None:
    metrics = error_rate_metrics(
        source_stats=[
            {"source": "openalex", "error_message": "HTTP 503"},
            {"source": "arxiv", "error_message": None},
            {"source": "openalex", "error_message": "timeout"},
        ],
        warnings=["HTTP 503", ""],
        failed_case_count=1,
        total_case_count=4,
        warning_case_count=2,
    )

    assert metrics["source_call_count"] == 3
    assert metrics["source_error_count"] == 2
    assert metrics["source_error_rate"] == pytest.approx(2 / 3)
    assert metrics["warning_count"] == 1
    assert metrics["query_warning_rate"] == pytest.approx(0.5)
    assert metrics["failed_case_rate"] == pytest.approx(0.25)

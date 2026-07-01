from __future__ import annotations

import pytest

from scholar_agent.agents.query_understanding import analyze_query


def test_chinese_long_query_generates_search_plan() -> None:
    plan = analyze_query(
        "请检索近三年 LLM reranking 在科研论文搜索中的代表性论文，重点关注 ACL 和 SIGIR。",
        current_year=2026,
    )

    assert plan.query_analysis.original_query.startswith("请检索近三年")
    assert plan.query_analysis.language == "mixed"
    assert plan.query_analysis.domain == "machine_learning"
    assert plan.query_analysis.constraints.time_range is not None
    assert plan.query_analysis.constraints.venues == ["ACL", "SIGIR"]
    assert plan.selected_sources == ["openalex", "arxiv"]
    assert 1 <= len(plan.subqueries) <= 5
    assert all(subquery.purpose for subquery in plan.subqueries)


def test_english_recent_query_detects_recent_progress() -> None:
    plan = analyze_query(
        "latest LLM reranking methods for scientific literature retrieval",
        current_year=2026,
    )

    assert plan.query_analysis.language == "en"
    assert plan.query_analysis.intent == "recent_progress"
    assert plan.query_analysis.constraints.time_range is not None
    assert plan.query_analysis.constraints.time_range.start_year == 2023
    assert plan.query_analysis.constraints.time_range.end_year == 2026


def test_since_year_parses_start_year() -> None:
    plan = analyze_query("LLM reranking papers since 2020", current_year=2026)

    time_range = plan.query_analysis.constraints.time_range
    assert time_range is not None
    assert time_range.start_year == 2020
    assert time_range.end_year is None


def test_year_range_parses_start_and_end_year() -> None:
    plan = analyze_query("RAG retrieval papers 2021-2024", current_year=2026)

    time_range = plan.query_analysis.constraints.time_range
    assert time_range is not None
    assert time_range.start_year == 2021
    assert time_range.end_year == 2024


def test_chinese_recent_three_years_uses_current_year() -> None:
    plan = analyze_query("近三年 LLM reranking 论文", current_year=2026)

    time_range = plan.query_analysis.constraints.time_range
    assert time_range is not None
    assert time_range.start_year == 2023
    assert time_range.end_year == 2026


def test_llm_reranking_retrieval_query_selects_openalex_and_arxiv() -> None:
    plan = analyze_query("LLM reranking for retrieval", current_year=2026)

    assert plan.selected_sources == ["openalex", "arxiv"]
    assert plan.query_analysis.domain == "machine_learning"
    assert all(
        set(subquery.source_hints).issubset({"openalex", "arxiv"})
        for subquery in plan.subqueries
    )


def test_biomedical_query_does_not_return_pubmed_but_warns() -> None:
    plan = analyze_query(
        "recent clinical gene therapy studies in PubMed",
        current_year=2026,
    )

    assert plan.query_analysis.domain == "biomedical"
    assert "pubmed" not in plan.selected_sources
    assert "pubmed_not_implemented" in plan.warnings


def test_fast_and_high_recall_profiles_differ() -> None:
    fast = analyze_query(
        "latest LLM reranking benchmark dataset papers",
        run_profile="fast",
        current_year=2026,
    )
    high_recall = analyze_query(
        "latest LLM reranking benchmark dataset papers",
        run_profile="high_recall",
        current_year=2026,
    )

    assert fast.limit_per_source < high_recall.limit_per_source
    assert len(fast.subqueries) < len(high_recall.subqueries)


def test_empty_query_raises_value_error() -> None:
    with pytest.raises(ValueError):
        analyze_query("   ")


def test_subqueries_are_deduplicated() -> None:
    plan = analyze_query("LLM LLM reranking reranking", current_year=2026)
    queries = [subquery.query.casefold() for subquery in plan.subqueries]

    assert len(queries) == len(set(queries))


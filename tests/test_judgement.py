from __future__ import annotations

import pytest

from scholar_agent.agents import judgement as judgement_module
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


def test_llm_disabled_falls_back_to_rules_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCHOLAR_AGENT_LLM_PROVIDER", raising=False)
    query_analysis = make_query_analysis()
    paper = make_paper(
        "LLM Reranking for Retrieval",
        abstract="LLM reranking improves retrieval.",
    )

    result = judge_papers(query_analysis, [paper], use_llm=True)[0]

    assert result.category in {"partially_relevant", "highly_relevant"}
    assert "llm_judgement_disabled" in result.warnings


def test_valid_llm_json_generates_judgement_result() -> None:
    query_analysis = make_query_analysis()
    paper = make_paper(
        "LLM Reranking for Scientific Literature Retrieval",
        abstract="This paper studies LLM reranking for retrieval.",
        venue="ACL",
    )
    client = FakeLLMClient(
        [
            {
                "judgements": [
                    {
                        "paper_index": 0,
                        "score": 0.93,
                        "category": "highly_relevant",
                        "reasoning": "Strong match based on title and abstract metadata.",
                        "evidence": [
                            {
                                "source": "title",
                                "text": "LLM Reranking for Scientific Literature Retrieval",
                                "confidence": 0.95,
                            }
                        ],
                        "matched_terms": ["LLM", "reranking", "retrieval"],
                        "warnings": [],
                    }
                ],
                "warnings": ["batch_note"],
            }
        ]
    )

    result = judge_papers(
        query_analysis,
        [paper],
        use_llm=True,
        llm_client=client,
    )[0]

    assert client.calls == 1
    assert result.score == 0.93
    assert result.category == "highly_relevant"
    assert result.reasoning.startswith("Strong match")
    assert result.evidence[0].source == "title"
    assert "llm_judgement_used" in result.warnings
    assert "batch_note" in result.warnings


def test_llm_evidence_object_is_accepted_as_single_item() -> None:
    query_analysis = make_query_analysis()
    paper = make_paper(
        "LLM Reranking for Scientific Literature Retrieval",
        abstract="This paper studies LLM reranking for retrieval.",
    )
    client = FakeLLMClient(
        [
            {
                "judgements": [
                    {
                        "paper_index": 0,
                        "score": 0.91,
                        "category": "highly_relevant",
                        "reasoning": "Relevant based on title metadata.",
                        "evidence": {
                            "source": "title",
                            "text": "LLM Reranking for Scientific Literature Retrieval",
                            "confidence": 0.94,
                        },
                        "matched_terms": ["LLM", "reranking"],
                    }
                ]
            }
        ]
    )

    result = judge_papers(
        query_analysis,
        [paper],
        use_llm=True,
        llm_client=client,
    )[0]

    assert result.score == 0.91
    assert result.category == "highly_relevant"
    assert len(result.evidence) == 1
    assert result.evidence[0].source == "title"
    assert "llm_judgement_invalid_evidence:0" not in result.warnings
    assert "llm_judgement_used" in result.warnings


def test_llm_missing_evidence_keeps_valid_judgement_with_warning() -> None:
    query_analysis = make_query_analysis()
    paper = make_paper(
        "LLM Reranking for Scientific Literature Retrieval",
        abstract="This paper studies LLM reranking for retrieval.",
    )
    client = FakeLLMClient(
        [
            {
                "judgements": [
                    {
                        "paper_index": 0,
                        "score": 0.82,
                        "category": "highly_relevant",
                        "reasoning": "Relevant based on metadata in the candidate.",
                        "matched_terms": ["LLM", "reranking"],
                    }
                ]
            }
        ]
    )

    result = judge_papers(
        query_analysis,
        [paper],
        use_llm=True,
        llm_client=client,
    )[0]

    assert result.score == 0.82
    assert result.category == "highly_relevant"
    assert result.reasoning.startswith("Relevant based on metadata")
    assert result.evidence == []
    assert "llm_judgement_missing_evidence:0" in result.warnings
    assert "llm_judgement_failed" not in " ".join(result.warnings)


def test_llm_judgement_max_papers_limits_llm_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCHOLAR_AGENT_LLM_JUDGEMENT_MAX_PAPERS", "1")
    query_analysis = make_query_analysis()
    first = make_paper(
        "LLM Reranking for Scientific Literature Retrieval",
        abstract="LLM reranking improves retrieval.",
    )
    second = make_paper(
        "Neural Retrieval Survey",
        abstract="A survey of neural retrieval methods.",
    )
    client = FakeLLMClient(
        [
            {
                "judgements": [
                    {
                        "paper_index": 0,
                        "score": 0.91,
                        "category": "highly_relevant",
                        "reasoning": "LLM judged the first candidate.",
                        "evidence": {
                            "source": "title",
                            "text": "LLM Reranking for Scientific Literature Retrieval",
                            "confidence": 0.9,
                        },
                    }
                ]
            }
        ]
    )

    first_result, second_result = judge_papers(
        query_analysis,
        [first, second],
        use_llm=True,
        llm_client=client,
    )

    assert client.calls == 1
    assert first_result.score == 0.91
    assert "llm_judgement_used" in first_result.warnings
    assert "llm_judgement_skipped_by_limit:1" in second_result.warnings
    assert "llm_judgement_used" not in second_result.warnings


@pytest.mark.parametrize("raw_value", ["0", "-1", "not-an-int"])
def test_llm_judgement_max_papers_invalid_values_fall_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
) -> None:
    monkeypatch.setenv("SCHOLAR_AGENT_LLM_JUDGEMENT_MAX_PAPERS", raw_value)

    assert (
        judgement_module.llm_judgement_max_papers_from_env()
        == judgement_module.DEFAULT_LLM_JUDGEMENT_MAX_PAPERS
    )


def test_llm_exception_falls_back_to_rules_with_warning() -> None:
    query_analysis = make_query_analysis()
    paper = make_paper("LLM Reranking", abstract="LLM retrieval reranking.")
    client = FailingLLMClient(RuntimeError("provider unavailable"))

    result = judge_papers(
        query_analysis,
        [paper],
        use_llm=True,
        llm_client=client,
    )[0]

    assert result.category in {"partially_relevant", "highly_relevant"}
    assert any(
        warning.startswith("llm_judgement_failed:provider unavailable")
        for warning in result.warnings
    )


def test_invalid_llm_json_falls_back_to_rules_with_warning() -> None:
    query_analysis = make_query_analysis()
    paper = make_paper("LLM Reranking", abstract="LLM retrieval reranking.")
    client = FakeLLMClient([["not", "a", "json", "object"]])

    result = judge_papers(
        query_analysis,
        [paper],
        use_llm=True,
        llm_client=client,
    )[0]

    assert any(
        warning.startswith("llm_judgement_failed:")
        for warning in result.warnings
    )


def test_invalid_llm_category_and_missing_index_use_rule_fallback() -> None:
    query_analysis = make_query_analysis()
    first = make_paper("LLM Reranking", abstract="LLM retrieval reranking.")
    second = make_paper("Scientific Literature Retrieval", abstract="retrieval system")
    client = FakeLLMClient(
        [
            {
                "judgements": [
                    {
                        "paper_index": 0,
                        "score": 0.99,
                        "category": "not_a_category",
                        "reasoning": "Invalid category should be rejected.",
                        "evidence": [],
                    }
                ]
            }
        ]
    )

    first_result, second_result = judge_papers(
        query_analysis,
        [first, second],
        use_llm=True,
        llm_client=client,
    )

    assert "llm_judgement_invalid_category:0" in first_result.warnings
    assert "llm_judgement_missing_paper_index:1" in second_result.warnings
    assert first_result.category != "not_a_category"


def test_llm_score_clamp_and_bad_evidence_source_are_warned() -> None:
    query_analysis = make_query_analysis()
    paper = make_paper(
        "LLM Reranking for Retrieval",
        abstract="LLM reranking improves retrieval.",
    )
    client = FakeLLMClient(
        [
            {
                "judgements": [
                    {
                        "paper_index": 0,
                        "score": 1.4,
                        "category": "highly_relevant",
                        "reasoning": "Relevant based on metadata.",
                        "evidence": [
                            {
                                "source": "full_text",
                                "text": "unsupported full text evidence",
                                "confidence": 0.9,
                            },
                            {
                                "source": "title",
                                "text": "ungrounded generated title",
                                "confidence": 0.8,
                            },
                        ],
                        "matched_terms": ["LLM"],
                    }
                ]
            }
        ]
    )

    result = judge_papers(
        query_analysis,
        [paper],
        use_llm=True,
        llm_client=client,
    )[0]

    assert result.score == 1.0
    assert "llm_judgement_score_clamped:0" in result.warnings
    assert "llm_judgement_bad_evidence_source:0:full_text" in result.warnings
    assert "llm_judgement_evidence_regrounded:0:title" in result.warnings
    assert result.evidence[0].source == "title"
    assert result.evidence[0].text == paper.title


def test_llm_judgement_batch_size_controls_call_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCHOLAR_AGENT_LLM_JUDGEMENT_BATCH_SIZE", "1")
    query_analysis = make_query_analysis()
    papers = [
        make_paper("LLM Reranking", abstract="LLM retrieval reranking."),
        make_paper("Neural Retrieval", abstract="retrieval with neural models."),
    ]
    client = FakeLLMClient(
        [
            {
                "judgements": [
                    {
                        "paper_index": 0,
                        "score": 0.8,
                        "category": "highly_relevant",
                        "reasoning": "first batch",
                        "evidence": [],
                    }
                ]
            },
            {
                "judgements": [
                    {
                        "paper_index": 0,
                        "score": 0.5,
                        "category": "partially_relevant",
                        "reasoning": "second batch uses local paper_index",
                        "evidence": [],
                    }
                ]
            },
        ]
    )

    results = judge_papers(
        query_analysis,
        papers,
        use_llm=True,
        llm_client=client,
    )

    assert client.calls == 2
    assert [result.category for result in results] == [
        "highly_relevant",
        "partially_relevant",
    ]


class FakeLLMClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls = 0

    def chat_json(self, messages, *, temperature=0, timeout=None):  # noqa: ANN001
        self.calls += 1
        assert messages
        assert temperature == 0
        return self.responses[self.calls - 1]


class FailingLLMClient:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def chat_json(self, messages, *, temperature=0, timeout=None):  # noqa: ANN001
        raise self.error

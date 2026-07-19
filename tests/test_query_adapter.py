from __future__ import annotations

import re

from scholar_agent.core.search_schemas import QueryConstraint
from scholar_agent.retrieval.query_adapter import (
    MAX_ADAPTED_QUERIES_PER_SOURCE,
    MAX_ARXIV_QUERY_LENGTH,
    MAX_OPENALEX_QUERY_LENGTH,
    adapt_queries_for_source,
    adapt_query_for_source,
)


def test_arxiv_long_natural_language_query_becomes_short_stable_expression() -> None:
    query = (
        "Could you please tell me about papers that explored target networks "
        "for deep reinforcement learning and value based decision making? " * 4
    )

    first = adapt_query_for_source(query, "arxiv")
    second = adapt_query_for_source(query, "arxiv")

    assert first == second
    assert len(first.query) <= MAX_ARXIV_QUERY_LENGTH
    assert re.search(r"(?:ti|abs):", first.query)
    assert "Could you please" not in first.query
    assert "adapted_query_truncated" in first.warnings


def test_arxiv_special_characters_are_safely_removed_from_phrases() -> None:
    adapted = adapt_query_for_source(
        'papers on graph (neural): retrieval? "C++" [survey]',
        "arxiv",
    )

    assert "?" not in adapted.query
    assert "[" not in adapted.query
    assert "]" not in adapted.query
    assert adapted.query.count('"') % 2 == 0
    assert adapted.query.startswith("(ti:")


def test_openalex_long_dirty_query_is_deterministically_truncated() -> None:
    query = ("retrieval\x00 graph, graph; ranking! multilingual 中文 " * 30).strip()

    first = adapt_query_for_source(query, "openalex")
    second = adapt_query_for_source(query, "openalex")

    assert first == second
    assert len(first.query) <= MAX_OPENALEX_QUERY_LENGTH
    assert "\x00" not in first.query
    assert "," not in first.query
    assert len(first.query.split()) <= 12
    assert first.query.split().count("graph") == 1
    assert "adapted_query_truncated" in first.warnings


def test_explicit_method_and_dataset_are_prioritized() -> None:
    constraints = QueryConstraint(
        methods=["contrastive learning"],
        datasets=["clinical notes"],
        explicit_fields=["methods", "datasets"],
    )

    adapted = adapt_query_for_source(
        "Could you list some papers about representation learning?",
        "openalex",
        constraints=constraints,
    )

    assert adapted.query.startswith("contrastive learning clinical notes")


def test_query_variant_count_is_centrally_bounded() -> None:
    queries = adapt_queries_for_source(
        "graph neural retrieval with contrastive learning",
        "arxiv",
        max_queries=99,
    )

    assert len(queries) == MAX_ADAPTED_QUERIES_PER_SOURCE
    assert len({item.query for item in queries}) == len(queries)

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scholar_agent.core.api_schemas import SearchRunCreateRequest


def test_api_defaults_to_current_rules_planning() -> None:
    request = SearchRunCreateRequest(query="graph retrieval")

    assert request.options.query_planning_policy == "current_rules"


def test_api_accepts_facet_balanced_planning() -> None:
    request = SearchRunCreateRequest.model_validate(
        {
            "query": "graph retrieval",
            "options": {"query_planning_policy": "facet_balanced"},
        }
    )

    assert request.options.query_planning_policy == "facet_balanced"


def test_api_rejects_unknown_planning_policy() -> None:
    with pytest.raises(ValidationError):
        SearchRunCreateRequest.model_validate(
            {
                "query": "graph retrieval",
                "options": {"query_planning_policy": "unknown"},
            }
        )

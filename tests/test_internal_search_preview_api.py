from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scholar_agent.agents.retriever import SourceStats  # noqa: E402
from scholar_agent.app.main import app  # noqa: E402
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers  # noqa: E402
from scholar_agent.core.search_schemas import (  # noqa: E402
    EvidenceItem,
    QueryAnalysis,
    QueryConstraint,
    RankedPaper,
    RerankScoreBreakdown,
    SearchPlan,
    SearchSubquery,
)
from scholar_agent.services.search_service import SearchServiceOutput  # noqa: E402


client = TestClient(app)


def test_internal_search_preview_maps_search_service_output(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeSearchService:
        def run_search(
            self,
            query: str,
            top_k: int = 20,
            run_profile: str = "balanced",
            enable_refchain: bool = False,
            enable_query_evolution: bool = False,
            current_year: int | None = None,
        ) -> SearchServiceOutput:
            captured.update(
                {
                    "query": query,
                    "top_k": top_k,
                    "run_profile": run_profile,
                    "enable_refchain": enable_refchain,
                    "enable_query_evolution": enable_query_evolution,
                    "current_year": current_year,
                }
            )
            return _fake_output(query, top_k)

    monkeypatch.setattr("scholar_agent.app.api.routes.SearchService", FakeSearchService)

    response = client.post(
        "/api/v1/internal/search/preview",
        json={
            "query": "latest LLM reranking retrieval papers",
            "top_k": 3,
            "run_profile": "high_recall",
            "enable_refchain": False,
            "enable_query_evolution": False,
            "current_year": 2026,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert captured == {
        "query": "latest LLM reranking retrieval papers",
        "top_k": 3,
        "run_profile": "high_recall",
        "enable_refchain": False,
        "enable_query_evolution": False,
        "current_year": 2026,
    }
    assert body["query_analysis"]["original_query"] == captured["query"]
    assert body["search_plan"]["selected_sources"] == ["openalex", "arxiv"]
    assert body["ranked_papers"][0]["rank"] == 1
    assert body["ranked_papers"][0]["paper"]["title"] == "Preview Paper"
    assert body["raw_count"] == 4
    assert body["deduplicated_count"] == 2
    assert body["warnings"] == ["mock_warning"]
    assert body["source_stats"][0]["source"] == "openalex"
    assert body["latency_seconds"] == 0.123


def test_internal_search_preview_returns_400_for_service_value_error(monkeypatch) -> None:
    class FakeSearchService:
        def run_search(self, *args, **kwargs) -> SearchServiceOutput:
            raise ValueError("query must not be empty")

    monkeypatch.setattr("scholar_agent.app.api.routes.SearchService", FakeSearchService)

    response = client.post(
        "/api/v1/internal/search/preview",
        json={
            "query": " ",
            "top_k": 3,
            "run_profile": "balanced",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "query must not be empty"


def test_existing_mock_search_runs_endpoint_still_works(monkeypatch) -> None:
    class FailingSearchService:
        def run_search(self, *args, **kwargs) -> SearchServiceOutput:
            raise AssertionError("mock run endpoint must not call SearchService")

    monkeypatch.setattr("scholar_agent.app.api.routes.SearchService", FailingSearchService)

    response = client.post(
        "/api/v1/search/runs",
        json={"query": "请帮我搜索关于 LLM reranking 的代表性论文"},
    )

    assert response.status_code == 201
    assert response.json()["run_id"].startswith("run_")


def _fake_output(query: str, top_k: int) -> SearchServiceOutput:
    query_analysis = QueryAnalysis(
        original_query=query,
        language="en",
        intent="recent_progress",
        domain="machine_learning",
        constraints=QueryConstraint(
            methods=["reranking"],
            must_include_terms=["LLM", "retrieval"],
        ),
    )
    search_plan = SearchPlan(
        query_analysis=query_analysis,
        subqueries=[
            SearchSubquery(
                query=query,
                source_hints=["openalex", "arxiv"],
                priority=1,
                purpose="original_query",
            )
        ],
        selected_sources=["openalex", "arxiv"],
        limit_per_source=20,
        top_k=top_k,
        run_profile="high_recall",
        warnings=["mock_warning"],
    )
    paper = Paper(
        title="Preview Paper",
        authors=["Alice"],
        year=2024,
        venue="ACL",
        abstract="A preview paper about LLM reranking and retrieval.",
        identifiers=PaperIdentifiers(doi="10.123/preview"),
        sources=["openalex"],
        citation_count=12,
    )
    ranked_paper = RankedPaper(
        rank=1,
        paper=paper,
        final_score=0.88,
        category="highly_relevant",
        score_breakdown=RerankScoreBreakdown(
            relevance_score=0.9,
            authority_score=0.5,
            timeliness_score=0.8,
            metadata_score=0.9,
            final_score=0.88,
            relevance_weight=0.65,
            authority_weight=0.08,
            timeliness_weight=0.22,
            metadata_weight=0.05,
        ),
        ranking_reason="metadata-only preview ranking",
        evidence=[
            EvidenceItem(source="title", text="Preview Paper", confidence=0.9),
        ],
        matched_terms=["LLM", "reranking"],
    )
    return SearchServiceOutput(
        search_plan=search_plan,
        raw_count=4,
        deduplicated_count=2,
        ranked_papers=[ranked_paper],
        warnings=["mock_warning"],
        source_stats=[
            SourceStats(
                source="openalex",
                returned_count=2,
                latency_seconds=0.02,
            )
        ],
        latency_seconds=0.123,
    )


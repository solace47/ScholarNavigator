from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scholar_agent.agents.retriever import RetrievalOutput, SourceStats  # noqa: E402
from scholar_agent.agents.synthesis import synthesize_answer  # noqa: E402
from scholar_agent.app.main import app  # noqa: E402
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers  # noqa: E402
from scholar_agent.core.search_schemas import (  # noqa: E402
    EvidenceItem,
    JudgementResult,
    QueryAnalysis,
    QueryConstraint,
    RankedPaper,
    RerankScoreBreakdown,
    SearchPlan,
    SearchSubquery,
    TimeRange,
)
from scholar_agent.services.search_service import SearchServiceOutput  # noqa: E402


client = TestClient(app)


def test_real_search_run_lifecycle_returns_saved_result_and_events(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.delenv("REAL_SEARCH_MAX_WORKERS", raising=False)

    class FakeSearchService:
        def __init__(self, *args, **kwargs) -> None:
            captured["max_workers"] = kwargs.get("max_workers")

        def run_search(
            self,
            query: str,
            top_k: int = 20,
            run_profile: str = "balanced",
            enable_refchain: bool = False,
            enable_query_evolution: bool = False,
            enable_synthesis: bool = True,
            current_year: int | None = None,
        ) -> SearchServiceOutput:
            captured.update(
                {
                    "query": query,
                    "top_k": top_k,
                    "run_profile": run_profile,
                    "enable_refchain": enable_refchain,
                    "enable_query_evolution": enable_query_evolution,
                    "enable_synthesis": enable_synthesis,
                    "current_year": current_year,
                }
            )
            return _fake_output(query, top_k=top_k)

    monkeypatch.setattr("scholar_agent.app.api.routes.SearchService", FakeSearchService)

    create_response = client.post(
        "/api/v1/real/search/runs",
        json={
            "query": "latest LLM reranking retrieval papers",
            "top_k": 5,
            "run_profile": "high_recall",
            "constraints": {
                "time_range": {"start_year": 2023, "end_year": 2026},
            },
            "options": {
                "enable_query_evolution": True,
                "enable_refchain": False,
            },
        },
    )

    assert create_response.status_code == 201
    create_body = create_response.json()
    run_id = create_body["run_id"]
    assert run_id.startswith("run_real_")
    assert create_body["status"] == "succeeded"
    assert create_body["links"]["self"] == f"/api/v1/real/search/runs/{run_id}"
    assert captured == {
        "max_workers": 2,
        "query": "latest LLM reranking retrieval papers",
        "top_k": 5,
        "run_profile": "high_recall",
        "enable_refchain": False,
        "enable_query_evolution": True,
        "enable_synthesis": True,
        "current_year": 2026,
    }

    status_response = client.get(f"/api/v1/real/search/runs/{run_id}")
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["run_id"] == run_id
    assert status_body["status"] == "succeeded"
    assert status_body["current_stage"] == "synthesis"
    assert status_body["progress"]["candidate_paper_count"] == 2
    assert status_body["progress"]["judged_paper_count"] == 2
    assert status_body["cost_report"]["judged_paper_count"] == 2

    result_response = client.get(f"/api/v1/real/search/runs/{run_id}/result")
    assert result_response.status_code == 200
    result_body = result_response.json()
    assert result_body["run_id"] == run_id
    assert result_body["status"] == "succeeded"
    assert result_body["synthesis"] is not None
    assert result_body["synthesis"]["evidence_table"][0]["citation_key"] == "R1"
    assert result_body["highly_relevant_papers"][0]["paper"]["title"] == "Real High"
    assert result_body["partially_relevant_papers"][0]["paper"]["title"] == "Real Partial"

    with client.stream("GET", f"/api/v1/real/search/runs/{run_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = "".join(response.iter_text())

    assert "event: run_started" in text
    assert "event: stage_started" in text
    assert '"stage": "synthesis"' in text
    assert "event: run_completed" in text


def test_real_search_unknown_run_id_returns_404() -> None:
    status_response = client.get("/api/v1/real/search/runs/run_real_missing")
    result_response = client.get("/api/v1/real/search/runs/run_real_missing/result")
    events_response = client.get("/api/v1/real/search/runs/run_real_missing/events")

    assert status_response.status_code == 404
    assert result_response.status_code == 404
    assert events_response.status_code == 404


def test_real_search_returns_400_for_service_value_error(monkeypatch) -> None:
    class FakeSearchService:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run_search(self, *args, **kwargs) -> SearchServiceOutput:
            raise ValueError("query must not be empty")

    monkeypatch.setattr("scholar_agent.app.api.routes.SearchService", FakeSearchService)

    response = client.post(
        "/api/v1/real/search/runs",
        json={"query": " ", "top_k": 5},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "query must not be empty"


def test_real_search_returns_500_for_unexpected_service_error(monkeypatch) -> None:
    class FakeSearchService:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run_search(self, *args, **kwargs) -> SearchServiceOutput:
            raise RuntimeError("service exploded")

    monkeypatch.setattr("scholar_agent.app.api.routes.SearchService", FakeSearchService)

    response = client.post(
        "/api/v1/real/search/runs",
        json={"query": "LLM reranking retrieval papers", "top_k": 5},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "service exploded"


def test_real_search_uses_real_search_max_workers_env(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeSearchService:
        def __init__(self, *args, **kwargs) -> None:
            captured["max_workers"] = kwargs.get("max_workers")

        def run_search(self, query: str, *args, **kwargs) -> SearchServiceOutput:
            return _fake_output(query)

    monkeypatch.setenv("REAL_SEARCH_MAX_WORKERS", "7")
    monkeypatch.setattr("scholar_agent.app.api.routes.SearchService", FakeSearchService)

    response = client.post(
        "/api/v1/real/search/runs",
        json={"query": "LLM reranking retrieval papers", "top_k": 5},
    )

    assert response.status_code == 201
    assert captured["max_workers"] == 7


def test_real_search_normalizes_invalid_or_small_max_workers_env(monkeypatch) -> None:
    captured: list[int | None] = []

    class FakeSearchService:
        def __init__(self, *args, **kwargs) -> None:
            captured.append(kwargs.get("max_workers"))

        def run_search(self, query: str, *args, **kwargs) -> SearchServiceOutput:
            return _fake_output(query)

    monkeypatch.setattr("scholar_agent.app.api.routes.SearchService", FakeSearchService)

    monkeypatch.setenv("REAL_SEARCH_MAX_WORKERS", "not-an-int")
    invalid_response = client.post(
        "/api/v1/real/search/runs",
        json={"query": "LLM reranking retrieval papers"},
    )
    assert invalid_response.status_code == 201

    monkeypatch.setenv("REAL_SEARCH_MAX_WORKERS", "0")
    small_response = client.post(
        "/api/v1/real/search/runs",
        json={"query": "LLM reranking retrieval papers"},
    )
    assert small_response.status_code == 201

    assert captured[-2:] == [2, 1]


def test_existing_mock_api_does_not_call_search_service(monkeypatch) -> None:
    class FailingSearchService:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("mock API must not instantiate SearchService")

    monkeypatch.setattr("scholar_agent.app.api.routes.SearchService", FailingSearchService)

    create_response = client.post(
        "/api/v1/search/runs",
        json={"query": "请帮我搜索关于 LLM reranking 的代表性论文"},
    )

    assert create_response.status_code == 201
    run_id = create_response.json()["run_id"]
    status_response = client.get(f"/api/v1/search/runs/{run_id}")
    result_response = client.get(f"/api/v1/search/runs/{run_id}/result")

    assert status_response.status_code == 200
    assert status_response.json()["status"] == "succeeded"
    assert result_response.status_code == 200
    assert result_response.json()["highly_relevant_papers"][0]["paper"][
        "title"
    ].startswith("SPAR:")


def _fake_output(query: str, top_k: int = 5) -> SearchServiceOutput:
    search_plan = _search_plan(query, top_k=top_k)
    high = _ranked(_paper("Real High", doi="10.123/high"), rank=1, final_score=0.91)
    partial = _ranked(
        _paper("Real Partial", doi="10.123/partial"),
        rank=2,
        category="partially_relevant",
        final_score=0.62,
    )
    output = SearchServiceOutput(
        search_plan=search_plan,
        retrieval_outputs=[
            RetrievalOutput(
                query=query,
                requested_sources=["openalex", "arxiv"],
                raw_count=2,
                deduplicated_count=2,
                papers=[high.paper, partial.paper],
                source_stats=[],
                warnings=[],
                latency_seconds=0.01,
            )
        ],
        raw_count=2,
        deduplicated_count=2,
        judgements=[_judgement(high), _judgement(partial)],
        ranked_papers=[high, partial],
        warnings=["real_search_warning"],
        source_stats=[
            SourceStats(
                source="openalex",
                returned_count=1,
                latency_seconds=0.1,
            ),
            SourceStats(
                source="arxiv",
                returned_count=1,
                latency_seconds=0.1,
            ),
        ],
        latency_seconds=0.25,
    )
    output.synthesis_output = synthesize_answer(output)
    return output


def _search_plan(query: str, top_k: int = 5) -> SearchPlan:
    query_analysis = QueryAnalysis(
        original_query=query,
        language="en",
        intent="recent_progress",
        domain="machine_learning",
        constraints=QueryConstraint(
            time_range=TimeRange(start_year=2023, end_year=2026),
            methods=["reranking"],
            must_include_terms=["LLM", "retrieval"],
        ),
    )
    return SearchPlan(
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
        run_profile="balanced",
    )


def _paper(title: str, *, doi: str) -> Paper:
    return Paper(
        title=title,
        authors=["Alice"],
        year=2025,
        venue="ACL",
        abstract="A paper about LLM reranking for scientific literature retrieval.",
        identifiers=PaperIdentifiers(doi=doi, openalex_id=f"W-{title.upper()}"),
        sources=["fixture"],
        citation_count=12,
    )


def _ranked(
    paper: Paper,
    *,
    rank: int,
    category: str = "highly_relevant",
    final_score: float,
) -> RankedPaper:
    return RankedPaper(
        rank=rank,
        paper=paper,
        final_score=final_score,
        category=category,
        score_breakdown=RerankScoreBreakdown(
            relevance_score=final_score,
            authority_score=0.5,
            timeliness_score=0.8,
            metadata_score=0.9,
            final_score=final_score,
            relevance_weight=0.65,
            authority_weight=0.1,
            timeliness_weight=0.15,
            metadata_weight=0.1,
        ),
        ranking_reason="Matched the query using fixture metadata.",
        evidence=[
            EvidenceItem(
                source="abstract",
                text="A paper about LLM reranking for scientific literature retrieval.",
                confidence=0.9,
            )
        ],
        matched_terms=["LLM", "reranking", "retrieval"],
    )


def _judgement(ranked: RankedPaper) -> JudgementResult:
    return JudgementResult(
        paper=ranked.paper,
        score=ranked.final_score,
        category=ranked.category,
        reasoning="Fixture judgement.",
        evidence=ranked.evidence,
        matched_terms=ranked.matched_terms,
    )

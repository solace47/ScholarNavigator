from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scholar_agent.app.main import app  # noqa: E402


client = TestClient(app)


def _create_run() -> str:
    response = client.post(
        "/api/v1/search/runs",
        json={
            "query": "请帮我搜索关于 LLM reranking 的代表性论文",
            "constraints": {
                "time_range": {"start_year": 2020, "end_year": 2026},
                "venues": ["ACL", "EMNLP", "SIGIR"],
                "must_have_terms": ["reranking"],
                "paper_types": ["method", "benchmark"],
            },
            "source_preferences": ["openalex", "arxiv", "semantic_scholar"],
            "top_k": 20,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["run_id"].startswith("run_")
    assert body["status"] == "queued"
    return body["run_id"]


def test_health() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["time"]


def test_runtime_config() -> None:
    response = client.get("/api/v1/runtime/config")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "hybrid"
    assert body["llm"] == {
        "provider": "mock",
        "model": "mock-no-llm",
        "available": False,
    }
    assert body["features"]["sse"] is True
    assert body["features"]["real_search"] is True
    assert body["features"]["real_search_cancel"] is True
    assert body["features"]["real_search_sse"] is True
    assert body["features"]["retrieval_cache"] is True
    assert body["features"]["batch_cli"] is True
    assert body["limits"]["real_search_max_workers"] >= 1
    assert body["limits"]["real_search_background_workers"] >= 1
    assert "real_search_run_ttl_seconds" in body["limits"]
    assert "real_search_max_stored_runs" in body["limits"]
    assert {connector["name"] for connector in body["connectors"]} >= {
        "mock",
        "openalex",
        "arxiv",
        "semantic_scholar",
        "pubmed",
    }
    connectors = {connector["name"]: connector for connector in body["connectors"]}
    assert connectors["mock"]["available"] is True
    assert connectors["openalex"]["available"] is True
    assert connectors["openalex"]["reason"] != "mock_api_only"
    assert connectors["arxiv"]["available"] is True
    assert connectors["arxiv"]["reason"] != "mock_api_only"
    assert connectors["semantic_scholar"]["available"] is False
    assert connectors["semantic_scholar"]["requires_key"] is True
    assert connectors["semantic_scholar"]["reason"] == "not_implemented"
    assert connectors["pubmed"]["available"] is False
    assert connectors["pubmed"]["reason"] == "not_implemented"


def test_create_and_get_run() -> None:
    run_id = _create_run()

    response = client.get(f"/api/v1/search/runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert body["status"] == "succeeded"
    assert body["current_stage"] == "synthesis"
    assert body["cost_report"]["llm_call_count"] == 0


def test_get_search_result_shape() -> None:
    run_id = _create_run()

    response = client.get(f"/api/v1/search/runs/{run_id}/result")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert body["query_analysis"]
    assert body["search_plan"]["expanded_queries"]
    assert body["highly_relevant_papers"]
    assert body["partially_relevant_papers"]
    assert body["method_clusters"]
    assert body["timeline"]
    assert body["missing_evidence"]
    assert body["cost_report"]["api_call_count"] > 0

    first_paper = body["highly_relevant_papers"][0]
    assert first_paper["paper"]["title"]
    assert first_paper["paper"]["authors"]
    assert first_paper["paper"]["year"]
    assert first_paper["paper"]["venue"]
    assert first_paper["paper"]["abstract"]
    assert first_paper["paper"]["identifiers"]
    assert first_paper["paper"]["urls"]
    assert first_paper["paper"]["sources"]
    assert first_paper["relevance_score"] > 0
    assert first_paper["category"] == "highly_relevant"
    assert first_paper["ranking_reason"]
    assert first_paper["evidence"]


def test_sse_events_are_accessible() -> None:
    run_id = _create_run()

    with client.stream("GET", f"/api/v1/search/runs/{run_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = "".join(response.iter_text())

    assert "event: run_started" in text
    assert "event: stage_started" in text
    assert "event: connector_completed" in text
    assert "event: run_completed" in text

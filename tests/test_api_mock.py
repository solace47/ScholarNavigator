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


def test_health() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["time"]


def test_runtime_config_is_real_search_only(monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    response = client.get("/api/v1/runtime/config")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] in {"real_search", "real"}
    assert body["llm"] == {
        "provider": "disabled",
        "model": None,
        "available": False,
        "base_url_host": None,
        "reason": "provider_disabled",
    }
    assert body["features"]["sse"] is True
    assert body["features"]["real_search"] is True
    assert body["features"]["real_search_cancel"] is True
    assert body["features"]["real_search_sse"] is True
    assert body["features"]["retrieval_cache"] is True
    assert body["features"]["batch_cli"] is True
    assert body["features"]["llm_query_understanding"] is False
    assert body["features"]["llm_judgement"] is False
    assert body["limits"]["real_search_max_workers"] >= 1
    assert body["limits"]["real_search_background_workers"] >= 1
    assert "real_search_run_ttl_seconds" in body["limits"]
    assert "real_search_max_stored_runs" in body["limits"]

    connectors = {connector["name"]: connector for connector in body["connectors"]}
    assert "mock" not in connectors
    assert connectors["openalex"]["available"] is True
    assert connectors["openalex"]["reason"] == "implemented_for_real_search"
    assert connectors["arxiv"]["available"] is True
    assert connectors["arxiv"]["reason"] == "implemented_for_real_search"
    assert connectors["semantic_scholar"]["available"] is True
    assert connectors["semantic_scholar"]["requires_key"] is False
    assert connectors["semantic_scholar"]["reason"].startswith("implemented_for_real_search")
    assert connectors["pubmed"]["available"] is True
    assert connectors["pubmed"]["requires_key"] is False
    assert connectors["pubmed"]["reason"].startswith("implemented_for_real_search")


def test_runtime_config_shows_enabled_llm_without_api_key_leak(monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("SCHOLAR_AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("SCHOLAR_AGENT_LLM_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setenv("SCHOLAR_AGENT_LLM_API_KEY", "sk-do-not-leak")
    monkeypatch.setenv("SCHOLAR_AGENT_LLM_MODEL", "gpt-test")
    monkeypatch.setenv("SCHOLAR_AGENT_ENABLE_LLM_QUERY_UNDERSTANDING", "1")
    monkeypatch.setenv("SCHOLAR_AGENT_ENABLE_LLM_JUDGEMENT", "1")

    response = client.get("/api/v1/runtime/config")

    assert response.status_code == 200
    body = response.json()
    assert body["llm"] == {
        "provider": "openai_compatible",
        "model": "gpt-test",
        "available": True,
        "base_url_host": "api.example.test",
        "reason": None,
    }
    assert body["features"]["llm_query_understanding"] is True
    assert body["features"]["llm_judgement"] is True
    assert "sk-do-not-leak" not in response.text


def test_legacy_mock_search_run_endpoints_are_not_available() -> None:
    post_response = client.post(
        "/api/v1/search/runs",
        json={"query": "请帮我搜索关于 LLM reranking 的代表性论文"},
    )
    status_response = client.get("/api/v1/search/runs/some_id")
    result_response = client.get("/api/v1/search/runs/some_id/result")
    events_response = client.get("/api/v1/search/runs/some_id/events")

    assert post_response.status_code in {404, 405}
    assert status_response.status_code in {404, 405}
    assert result_response.status_code in {404, 405}
    assert events_response.status_code in {404, 405}


def test_legacy_mock_search_run_paths_are_not_in_openapi() -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    assert "/api/v1/search/runs" not in paths
    assert "/api/v1/search/runs/{run_id}" not in paths
    assert "/api/v1/search/runs/{run_id}/result" not in paths
    assert "/api/v1/search/runs/{run_id}/events" not in paths
    assert "/api/v1/real/search/runs" in paths
    assert "/api/v1/real/search/runs/{run_id}" in paths
    assert "/api/v1/real/search/runs/{run_id}/result" in paths
    assert "/api/v1/real/search/runs/{run_id}/events" in paths
    assert "/api/v1/real/search/runs/{run_id}/cancel" in paths


def _clear_llm_env(monkeypatch) -> None:
    for env_name in (
        "SCHOLAR_AGENT_LLM_PROVIDER",
        "SCHOLAR_AGENT_LLM_BASE_URL",
        "SCHOLAR_AGENT_LLM_API_KEY",
        "SCHOLAR_AGENT_LLM_MODEL",
        "SCHOLAR_AGENT_ENABLE_LLM_QUERY_UNDERSTANDING",
        "SCHOLAR_AGENT_ENABLE_LLM_JUDGEMENT",
    ):
        monkeypatch.delenv(env_name, raising=False)

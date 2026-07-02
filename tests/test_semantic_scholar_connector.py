from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

import scholar_agent.connectors.semantic_scholar as semantic_scholar_connector
from scholar_agent.connectors.semantic_scholar import (
    search_semantic_scholar,
    search_semantic_scholar_detailed,
)


class MockResponse:
    def __init__(self, payload: dict, status: int = 200, headers: dict | None = None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch) -> None:
    semantic_scholar_connector._reset_semantic_scholar_throttle_for_tests()
    monkeypatch.setattr(
        "scholar_agent.connectors.semantic_scholar.time.sleep",
        lambda _: None,
    )
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    monkeypatch.delenv(
        "SCHOLAR_AGENT_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS",
        raising=False,
    )
    yield
    semantic_scholar_connector._reset_semantic_scholar_throttle_for_tests()


def test_search_semantic_scholar_parses_normal_response(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        captured["timeout"] = timeout
        return MockResponse(
            {
                "data": [
                    {
                        "paperId": "S2PAPER123",
                        "title": "Semantic Scholar Test Paper",
                        "authors": [{"name": "Alice Chen"}, {"name": "Bob Smith"}],
                        "year": 2025,
                        "venue": "SIGIR",
                        "abstract": "A paper about LLM reranking for literature search.",
                        "externalIds": {
                            "DOI": "10.1234/s2",
                            "ArXiv": "2501.00001",
                            "PubMed": "987654",
                        },
                        "url": "https://www.semanticscholar.org/paper/S2PAPER123",
                        "citationCount": 42,
                    }
                ]
            }
        )

    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "s2-secret")
    monkeypatch.setattr("scholar_agent.connectors.semantic_scholar.urlopen", fake_urlopen)

    papers = search_semantic_scholar("llm reranking", limit=5)

    assert len(papers) == 1
    paper = papers[0]
    assert paper.title == "Semantic Scholar Test Paper"
    assert paper.authors == ["Alice Chen", "Bob Smith"]
    assert paper.year == 2025
    assert paper.venue == "SIGIR"
    assert paper.abstract == "A paper about LLM reranking for literature search."
    assert paper.identifiers.doi == "10.1234/s2"
    assert paper.identifiers.arxiv_id == "2501.00001"
    assert paper.identifiers.semantic_scholar_id == "S2PAPER123"
    assert paper.identifiers.pubmed_id == "987654"
    assert paper.urls.landing_page == "https://www.semanticscholar.org/paper/S2PAPER123"
    assert paper.sources == ["semantic_scholar"]
    assert paper.citation_count == 42
    assert captured["headers"]["x-api-key"] == "s2-secret"
    assert captured["timeout"] == 10.0
    query_params = parse_qs(urlparse(captured["url"]).query)
    assert query_params["query"] == ["llm reranking"]
    assert query_params["limit"] == ["5"]
    assert "paperId" in query_params["fields"][0]


def test_search_semantic_scholar_detailed_normal_response_has_no_error(
    monkeypatch,
) -> None:
    def fake_urlopen(request, timeout):
        return MockResponse(
            {
                "data": [
                    {
                        "paperId": "S2DETAIL",
                        "title": "Detailed Semantic Scholar Paper",
                    }
                ]
            }
        )

    monkeypatch.setattr("scholar_agent.connectors.semantic_scholar.urlopen", fake_urlopen)

    result = search_semantic_scholar_detailed("llm reranking", limit=5)

    assert len(result.papers) == 1
    assert result.papers[0].title == "Detailed Semantic Scholar Paper"
    assert result.papers[0].urls.landing_page == "https://www.semanticscholar.org/paper/S2DETAIL"
    assert result.error_message is None
    assert result.warnings == []
    assert result.latency_seconds >= 0


def test_search_semantic_scholar_detailed_retries_429_then_succeeds(
    monkeypatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError(request.full_url, 429, "Too Many Requests", hdrs=None, fp=None)
        return MockResponse(
            {
                "data": [
                    {
                        "paperId": "S2RETRY",
                        "title": "Recovered Semantic Scholar Paper",
                    }
                ]
            }
        )

    monkeypatch.setattr("scholar_agent.connectors.semantic_scholar.urlopen", fake_urlopen)

    result = search_semantic_scholar_detailed(
        "llm reranking",
        retry_sleep=lambda seconds: sleeps.append(seconds),
    )

    assert calls == 2
    assert sleeps == [2.0]
    assert result.error_message is None
    assert [paper.title for paper in result.papers] == ["Recovered Semantic Scholar Paper"]
    assert any("retried" in warning for warning in result.warnings)
    assert any("HTTP Error 429" in warning for warning in result.warnings)


def test_search_semantic_scholar_detailed_429_respects_retry_after(
    monkeypatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return MockResponse({}, status=429, headers={"Retry-After": "3.5"})
        return MockResponse({"data": [{"paperId": "S2RETRYAFTER"}]})

    monkeypatch.setattr("scholar_agent.connectors.semantic_scholar.urlopen", fake_urlopen)

    result = search_semantic_scholar_detailed(
        "llm reranking",
        retry_sleep=lambda seconds: sleeps.append(seconds),
    )

    assert calls == 2
    assert sleeps == [3.5]
    assert result.error_message is None
    assert result.papers[0].identifiers.semantic_scholar_id == "S2RETRYAFTER"


def test_search_semantic_scholar_detailed_retry_failure_keeps_diagnostics(
    monkeypatch,
) -> None:
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        raise HTTPError(request.full_url, 503, "Service Unavailable", hdrs=None, fp=None)

    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "s2-secret")
    monkeypatch.setattr("scholar_agent.connectors.semantic_scholar.urlopen", fake_urlopen)

    result = search_semantic_scholar_detailed("llm reranking")

    assert calls == 2
    assert result.papers == []
    assert result.error_message is not None
    assert "HTTP Error 503" in result.error_message
    assert result.error_message in result.warnings
    assert any("retried" in warning for warning in result.warnings)
    assert "s2-secret" not in " ".join(result.warnings)


def test_search_semantic_scholar_exception_wrapper_returns_empty(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise URLError("timeout")

    monkeypatch.setattr("scholar_agent.connectors.semantic_scholar.urlopen", fake_urlopen)

    assert search_semantic_scholar("llm reranking") == []


def test_search_semantic_scholar_detailed_url_error_returns_error_message(
    monkeypatch,
) -> None:
    def fake_urlopen(request, timeout):
        raise URLError("timeout")

    monkeypatch.setattr("scholar_agent.connectors.semantic_scholar.urlopen", fake_urlopen)

    result = search_semantic_scholar_detailed("llm reranking")

    assert result.papers == []
    assert result.error_message is not None
    assert "timeout" in result.error_message
    assert result.error_message in result.warnings


def test_search_semantic_scholar_detailed_timeout_error_returns_error_message(
    monkeypatch,
) -> None:
    def fake_urlopen(request, timeout):
        raise TimeoutError("request timed out")

    monkeypatch.setattr("scholar_agent.connectors.semantic_scholar.urlopen", fake_urlopen)

    result = search_semantic_scholar_detailed("llm reranking")

    assert result.papers == []
    assert result.error_message is not None
    assert "request timed out" in result.error_message
    assert result.error_message in result.warnings


def test_search_semantic_scholar_detailed_non_2xx_returns_error_message(
    monkeypatch,
) -> None:
    def fake_urlopen(request, timeout):
        return MockResponse({}, status=503)

    monkeypatch.setattr("scholar_agent.connectors.semantic_scholar.urlopen", fake_urlopen)

    result = search_semantic_scholar_detailed("llm reranking")

    assert result.papers == []
    assert result.error_message == "Semantic Scholar search returned non-2xx status: 503"
    assert result.error_message in result.warnings


def test_search_semantic_scholar_missing_fields_returns_available_result(
    monkeypatch,
) -> None:
    def fake_urlopen(request, timeout):
        return MockResponse({"data": [{"paperId": "S2MIN"}]})

    monkeypatch.setattr("scholar_agent.connectors.semantic_scholar.urlopen", fake_urlopen)

    result = search_semantic_scholar_detailed("minimal")

    assert len(result.papers) == 1
    assert result.papers[0].title == "Untitled Semantic Scholar Paper"
    assert result.papers[0].identifiers.semantic_scholar_id == "S2MIN"
    assert result.papers[0].sources == ["semantic_scholar"]
    assert result.error_message is None


def test_semantic_scholar_throttle_waits_between_consecutive_requests(
    monkeypatch,
) -> None:
    calls = 0
    clock = [100.0]
    sleeps: list[float] = []

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        return MockResponse({"data": [{"paperId": f"S2THROTTLE{calls}"}]})

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setenv("SCHOLAR_AGENT_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS", "1.5")
    monkeypatch.setattr("scholar_agent.connectors.semantic_scholar.urlopen", fake_urlopen)

    first = search_semantic_scholar_detailed(
        "first query",
        max_retries=0,
        throttle_sleep=fake_sleep,
        monotonic=lambda: clock[0],
    )
    second = search_semantic_scholar_detailed(
        "second query",
        max_retries=0,
        throttle_sleep=fake_sleep,
        monotonic=lambda: clock[0],
    )

    assert calls == 2
    assert first.error_message is None
    assert second.error_message is None
    assert sleeps == pytest.approx([1.5])


def test_semantic_scholar_throttle_can_be_disabled(monkeypatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        return MockResponse({"data": [{"paperId": f"S2NO{calls}"}]})

    monkeypatch.setenv("SCHOLAR_AGENT_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setattr("scholar_agent.connectors.semantic_scholar.urlopen", fake_urlopen)

    search_semantic_scholar_detailed(
        "first query",
        max_retries=0,
        throttle_sleep=lambda seconds: sleeps.append(seconds),
    )
    search_semantic_scholar_detailed(
        "second query",
        max_retries=0,
        throttle_sleep=lambda seconds: sleeps.append(seconds),
    )

    assert calls == 2
    assert sleeps == []


def test_semantic_scholar_throttle_serializes_concurrent_requests(
    monkeypatch,
) -> None:
    calls = 0
    clock = [200.0]
    sleeps: list[float] = []

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        return MockResponse({"data": [{"paperId": f"S2CONCURRENT{calls}"}]})

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setenv("SCHOLAR_AGENT_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS", "1.0")
    monkeypatch.setattr("scholar_agent.connectors.semantic_scholar.urlopen", fake_urlopen)

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(
            executor.map(
                lambda query: search_semantic_scholar_detailed(
                    query,
                    max_retries=0,
                    throttle_sleep=fake_sleep,
                    monotonic=lambda: clock[0],
                ),
                ["query one", "query two", "query three"],
            )
        )

    assert calls == 3
    assert all(result.error_message is None for result in results)
    assert sleeps == pytest.approx([1.0, 1.0])


def test_semantic_scholar_retry_after_still_overrides_rate_limit_backoff(
    monkeypatch,
) -> None:
    calls = 0
    retry_sleeps: list[float] = []

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return MockResponse({}, status=429, headers={"Retry-After": "4.25"})
        return MockResponse({"data": [{"paperId": "S2RETRYAFTERPRIORITY"}]})

    monkeypatch.setenv("SCHOLAR_AGENT_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setattr("scholar_agent.connectors.semantic_scholar.urlopen", fake_urlopen)

    result = search_semantic_scholar_detailed(
        "retry after query",
        retry_sleep=lambda seconds: retry_sleeps.append(seconds),
    )

    assert calls == 2
    assert retry_sleeps == [4.25]
    assert result.error_message is None
    assert result.papers[0].identifiers.semantic_scholar_id == "S2RETRYAFTERPRIORITY"

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import unquote

from scholar_agent.connectors.openalex import (
    fetch_openalex_references,
    search_openalex,
    search_openalex_detailed,
)
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers


class MockResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_search_openalex_parses_normal_response(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return MockResponse(
            {
                "results": [
                    {
                        "id": "https://openalex.org/W123",
                        "display_name": "Test OpenAlex Paper",
                        "publication_year": 2024,
                        "cited_by_count": 17,
                        "doi": "https://doi.org/10.1234/test",
                        "ids": {
                            "openalex": "https://openalex.org/W123",
                            "doi": "https://doi.org/10.1234/test",
                            "pmid": "https://pubmed.ncbi.nlm.nih.gov/987654/",
                        },
                        "authorships": [
                            {"author": {"display_name": "Alice Chen"}},
                            {"author": {"display_name": "Bob Smith"}},
                        ],
                        "primary_location": {
                            "landing_page_url": "https://example.org/paper",
                            "pdf_url": "https://example.org/paper.pdf",
                            "source": {"display_name": "ACL"},
                        },
                        "abstract_inverted_index": {
                            "A": [0],
                            "mock": [1],
                            "abstract": [2],
                        },
                    }
                ]
            }
        )

    monkeypatch.setenv("OPENALEX_MAILTO", "team@example.org")
    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", fake_urlopen)

    papers = search_openalex("llm reranking", limit=5)

    assert len(papers) == 1
    paper = papers[0]
    assert paper.title == "Test OpenAlex Paper"
    assert paper.authors == ["Alice Chen", "Bob Smith"]
    assert paper.year == 2024
    assert paper.venue == "ACL"
    assert paper.abstract == "A mock abstract"
    assert paper.identifiers.doi == "10.1234/test"
    assert paper.identifiers.openalex_id == "W123"
    assert paper.identifiers.pubmed_id == "987654"
    assert paper.urls.landing_page == "https://example.org/paper"
    assert paper.urls.pdf == "https://example.org/paper.pdf"
    assert paper.sources == ["openalex"]
    assert paper.citation_count == 17
    assert "mailto=team%40example.org" in captured["url"]
    assert captured["timeout"] == 10.0


def test_search_openalex_detailed_normal_response_has_no_error(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        return MockResponse(
            {
                "results": [
                    {
                        "id": "https://openalex.org/W123",
                        "display_name": "Detailed OpenAlex Paper",
                    }
                ]
            }
        )

    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", fake_urlopen)

    result = search_openalex_detailed("llm reranking", limit=5)

    assert len(result.papers) == 1
    assert result.papers[0].title == "Detailed OpenAlex Paper"
    assert result.error_message is None
    assert result.warnings == []


def test_search_openalex_exception_returns_empty(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise URLError("timeout")

    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", fake_urlopen)

    assert search_openalex("llm reranking") == []


def test_search_openalex_detailed_url_error_returns_error_message(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise URLError("timeout")

    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", fake_urlopen)

    result = search_openalex_detailed("llm reranking")

    assert result.papers == []
    assert result.error_message is not None
    assert "timeout" in result.error_message
    assert result.error_message in result.warnings


def test_search_openalex_detailed_timeout_error_returns_error_message(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise TimeoutError("request timed out")

    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", fake_urlopen)

    result = search_openalex_detailed("llm reranking")

    assert result.papers == []
    assert result.error_message is not None
    assert "request timed out" in result.error_message
    assert result.error_message in result.warnings


def test_search_openalex_detailed_http_error_returns_error_message(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            503,
            "Service Unavailable",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", fake_urlopen)

    result = search_openalex_detailed("llm reranking")

    assert result.papers == []
    assert result.error_message is not None
    assert "HTTP Error 503" in result.error_message
    assert result.error_message in result.warnings


def test_search_openalex_detailed_non_2xx_returns_error_message(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        return MockResponse({}, status=503)

    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", fake_urlopen)

    result = search_openalex_detailed("llm reranking")

    assert result.papers == []
    assert result.error_message == "OpenAlex search returned non-2xx status: 503"
    assert result.warnings == ["OpenAlex search returned non-2xx status: 503"]


def test_search_openalex_missing_fields_returns_available_result(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        return MockResponse({"results": [{"id": "https://openalex.org/W999"}]})

    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", fake_urlopen)

    papers = search_openalex("minimal")

    assert len(papers) == 1
    assert papers[0].title == "Untitled OpenAlex Work"
    assert papers[0].authors == []
    assert papers[0].abstract == ""
    assert papers[0].identifiers.openalex_id == "W999"
    assert papers[0].sources == ["openalex"]


def test_fetch_openalex_references_with_openalex_id_seed(monkeypatch) -> None:
    requested_urls: list[str] = []

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        if request.full_url.endswith("/WSEED"):
            return MockResponse(
                {
                    "id": "https://openalex.org/WSEED",
                    "referenced_works": [
                        "https://openalex.org/WREF1",
                        "https://openalex.org/WREF2",
                    ],
                }
            )
        if request.full_url.endswith("/WREF1"):
            return MockResponse(_openalex_work("WREF1", "Reference One"))
        if request.full_url.endswith("/WREF2"):
            return MockResponse(_openalex_work("WREF2", "Reference Two"))
        raise AssertionError(f"unexpected url: {request.full_url}")

    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", fake_urlopen)
    seed = Paper(
        title="Seed",
        identifiers=PaperIdentifiers(openalex_id="WSEED"),
    )

    references = fetch_openalex_references(seed, limit=20)

    assert [paper.title for paper in references] == ["Reference One", "Reference Two"]
    assert [paper.identifiers.openalex_id for paper in references] == ["WREF1", "WREF2"]
    assert all(paper.sources == ["openalex"] for paper in references)
    assert requested_urls[0].endswith("/WSEED")


def test_fetch_openalex_references_with_doi_seed(monkeypatch) -> None:
    requested_urls: list[str] = []

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        decoded = unquote(request.full_url)
        if "filter=doi:10.555/seed" in decoded:
            return MockResponse(
                {
                    "results": [
                        {
                            "id": "https://openalex.org/WSEED",
                            "referenced_works": ["https://openalex.org/WREFDOI"],
                        }
                    ]
                }
            )
        if request.full_url.endswith("/WREFDOI"):
            return MockResponse(_openalex_work("WREFDOI", "DOI Reference"))
        raise AssertionError(f"unexpected url: {request.full_url}")

    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", fake_urlopen)
    seed = Paper(
        title="Seed",
        identifiers=PaperIdentifiers(doi="https://doi.org/10.555/seed"),
    )

    references = fetch_openalex_references(seed)

    assert len(references) == 1
    assert references[0].title == "DOI Reference"
    assert references[0].identifiers.openalex_id == "WREFDOI"
    assert "filter=doi:10.555/seed" in unquote(requested_urls[0])


def test_fetch_openalex_references_limit_is_applied(monkeypatch) -> None:
    requested_urls: list[str] = []

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        if request.full_url.endswith("/WSEED"):
            return MockResponse(
                {
                    "id": "https://openalex.org/WSEED",
                    "referenced_works": [
                        "https://openalex.org/WREF1",
                        "https://openalex.org/WREF2",
                    ],
                }
            )
        if request.full_url.endswith("/WREF1"):
            return MockResponse(_openalex_work("WREF1", "Reference One"))
        if request.full_url.endswith("/WREF2"):
            raise AssertionError("limit should avoid requesting WREF2")
        raise AssertionError(f"unexpected url: {request.full_url}")

    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", fake_urlopen)
    seed = Paper(
        title="Seed",
        identifiers=PaperIdentifiers(openalex_id="WSEED"),
    )

    references = fetch_openalex_references(seed, limit=1)

    assert len(references) == 1
    assert references[0].title == "Reference One"
    assert not any(url.endswith("/WREF2") for url in requested_urls)


def test_fetch_openalex_references_without_supported_identifier_returns_empty(
    monkeypatch,
) -> None:
    def fake_urlopen(request, timeout):
        raise AssertionError("OpenAlex should not be called without an identifier")

    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", fake_urlopen)
    seed = Paper(title="Seed", identifiers=PaperIdentifiers())

    assert fetch_openalex_references(seed) == []


def test_fetch_openalex_references_timeout_and_non_2xx_return_empty(monkeypatch) -> None:
    seed = Paper(title="Seed", identifiers=PaperIdentifiers(openalex_id="WSEED"))

    def timeout_urlopen(request, timeout):
        raise URLError("timeout")

    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", timeout_urlopen)
    assert fetch_openalex_references(seed) == []

    def non_2xx_urlopen(request, timeout):
        return MockResponse({}, status=503)

    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", non_2xx_urlopen)
    assert fetch_openalex_references(seed) == []


def test_fetch_openalex_references_missing_fields_are_tolerated(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/WSEED"):
            return MockResponse(
                {
                    "id": "https://openalex.org/WSEED",
                    "referenced_works": [
                        "https://openalex.org/WMINIMAL",
                        None,
                    ],
                }
            )
        if request.full_url.endswith("/WMINIMAL"):
            return MockResponse({"id": "https://openalex.org/WMINIMAL"})
        raise AssertionError(f"unexpected url: {request.full_url}")

    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", fake_urlopen)
    seed = Paper(title="Seed", identifiers=PaperIdentifiers(openalex_id="WSEED"))

    references = fetch_openalex_references(seed)

    assert len(references) == 1
    assert references[0].title == "Untitled OpenAlex Work"
    assert references[0].authors == []
    assert references[0].abstract == ""
    assert references[0].identifiers.openalex_id == "WMINIMAL"
    assert references[0].sources == ["openalex"]


def _openalex_work(openalex_id: str, title: str) -> dict:
    return {
        "id": f"https://openalex.org/{openalex_id}",
        "display_name": title,
        "publication_year": 2023,
        "cited_by_count": 5,
        "ids": {
            "openalex": f"https://openalex.org/{openalex_id}",
            "doi": f"https://doi.org/10.123/{openalex_id.casefold()}",
        },
        "authorships": [{"author": {"display_name": "Reference Author"}}],
        "primary_location": {
            "landing_page_url": f"https://example.org/{openalex_id}",
            "source": {"display_name": "OpenAlex Venue"},
        },
        "abstract_inverted_index": {
            "Reference": [0],
            "abstract": [1],
        },
    }

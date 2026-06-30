from __future__ import annotations

import json
from urllib.error import URLError

from scholar_agent.connectors.openalex import search_openalex


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


def test_search_openalex_exception_returns_empty(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise URLError("timeout")

    monkeypatch.setattr("scholar_agent.connectors.openalex.urlopen", fake_urlopen)

    assert search_openalex("llm reranking") == []


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


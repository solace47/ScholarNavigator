"""OpenAlex Works API connector."""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers, PaperUrls


logger = logging.getLogger(__name__)

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_OPENALEX_LIMIT = 200


def search_openalex(query: str, limit: int = 20) -> list[Paper]:
    """Search papers from OpenAlex Works.

    The connector is deliberately fail-closed: external failures return an
    empty list and malformed individual records are skipped without aborting the
    full response.
    """

    query = query.strip()
    if not query or limit <= 0:
        return []

    params = {
        "search": query,
        "per-page": str(min(limit, MAX_OPENALEX_LIMIT)),
    }
    mailto = os.getenv("OPENALEX_MAILTO")
    if mailto:
        params["mailto"] = mailto

    url = f"{OPENALEX_WORKS_URL}?{urlencode(params)}"
    headers = {"User-Agent": "SPAR Scholar Agent (mailto: unavailable)"}
    if mailto:
        headers["User-Agent"] = f"SPAR Scholar Agent (mailto: {mailto})"

    request = Request(url, headers=headers)

    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", getattr(response, "code", 200))
            if status < 200 or status >= 300:
                logger.warning("OpenAlex returned non-2xx status: %s", status)
                return []
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.warning("OpenAlex search failed: %s", exc)
        return []

    results = payload.get("results", [])
    if not isinstance(results, list):
        return []

    papers: list[Paper] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            paper = _parse_work(item)
        except Exception as exc:  # noqa: BLE001 - isolate malformed records
            logger.warning("Failed to parse OpenAlex work: %s", exc)
            continue
        if paper is not None:
            papers.append(paper)

    return papers


def _parse_work(work: dict[str, Any]) -> Paper | None:
    title = _clean_text(work.get("display_name")) or "Untitled OpenAlex Work"
    authors = _parse_authors(work.get("authorships"))
    year = _as_int(work.get("publication_year"))
    primary_location = _as_dict(work.get("primary_location"))
    source = _as_dict(primary_location.get("source"))
    venue = _clean_text(source.get("display_name"))
    abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))
    ids = _as_dict(work.get("ids"))

    doi = _normalize_doi(ids.get("doi") or work.get("doi"))
    openalex_id = _normalize_openalex_id(ids.get("openalex") or work.get("id"))
    arxiv_id = _normalize_arxiv_id(ids.get("arxiv"))
    pubmed_id = _normalize_pubmed_id(ids.get("pmid"))

    landing_page = _clean_text(primary_location.get("landing_page_url"))
    if not landing_page:
        landing_page = _clean_text(ids.get("openalex") or work.get("id"))
    pdf_url = _clean_text(primary_location.get("pdf_url"))

    return Paper(
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        abstract=abstract,
        identifiers=PaperIdentifiers(
            doi=doi,
            arxiv_id=arxiv_id,
            openalex_id=openalex_id,
            pubmed_id=pubmed_id,
        ),
        urls=PaperUrls(
            landing_page=landing_page,
            pdf=pdf_url,
        ),
        sources=["openalex"],
        citation_count=max(_as_int(work.get("cited_by_count")) or 0, 0),
    )


def _parse_authors(authorships: Any) -> list[str]:
    if not isinstance(authorships, list):
        return []
    authors: list[str] = []
    for authorship in authorships:
        author = _as_dict(_as_dict(authorship).get("author"))
        name = _clean_text(author.get("display_name"))
        if name:
            authors.append(name)
    return authors


def _reconstruct_abstract(inverted_index: Any) -> str:
    if not isinstance(inverted_index, dict):
        return ""

    positioned_words: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        for position in positions:
            int_position = _as_int(position)
            if int_position is not None:
                positioned_words.append((int_position, word))

    if not positioned_words:
        return ""

    return " ".join(word for _, word in sorted(positioned_words))


def _normalize_doi(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    lower = text.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if lower.startswith(prefix):
            return text[len(prefix) :]
    return text


def _normalize_openalex_id(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme and parsed.path:
        return parsed.path.rstrip("/").split("/")[-1] or text
    return text


def _normalize_pubmed_id(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme and parsed.path:
        return parsed.path.rstrip("/").split("/")[-1] or text
    return text.removeprefix("pmid:")


def _normalize_arxiv_id(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme and parsed.path:
        text = parsed.path.rstrip("/").split("/")[-1] or text
    return text.removeprefix("arXiv:").removeprefix("arxiv:")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


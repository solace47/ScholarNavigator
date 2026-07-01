"""OpenAlex Works API connector."""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
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

    payload = _request_json(f"{OPENALEX_WORKS_URL}?{urlencode(params)}")
    if payload is None:
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


def fetch_openalex_references(paper: Paper, limit: int = 20) -> list[Paper]:
    """Fetch references for a seed paper from OpenAlex metadata.

    The function uses only metadata available through the OpenAlex Works API.
    It does not read full text or infer references. Failures return an empty
    list or the successfully parsed subset of references.
    """

    if limit <= 0:
        return []

    seed_work = _fetch_seed_work(paper)
    if not seed_work:
        return []

    reference_ids = _referenced_work_ids(seed_work.get("referenced_works"))
    if not reference_ids:
        return []

    papers: list[Paper] = []
    for reference_id in reference_ids[: min(limit, MAX_OPENALEX_LIMIT)]:
        work = _fetch_work_by_openalex_id(reference_id)
        if not work:
            continue
        try:
            reference_paper = _parse_work(work)
        except Exception as exc:  # noqa: BLE001 - isolate malformed references
            logger.warning("Failed to parse OpenAlex reference work: %s", exc)
            continue
        if reference_paper is not None:
            papers.append(reference_paper)
    return papers[:limit]


def _fetch_seed_work(paper: Paper) -> dict[str, Any] | None:
    openalex_id = _normalize_openalex_id(paper.identifiers.openalex_id)
    if openalex_id:
        return _fetch_work_by_openalex_id(openalex_id)

    doi = _normalize_doi(paper.identifiers.doi)
    if doi:
        return _fetch_work_by_doi(doi)

    return None


def _fetch_work_by_openalex_id(openalex_id: str) -> dict[str, Any] | None:
    normalized = _normalize_openalex_id(openalex_id)
    if not normalized:
        return None
    url = _url_with_mailto(f"{OPENALEX_WORKS_URL}/{quote(normalized, safe='')}", {})
    payload = _request_json(url)
    return payload if isinstance(payload, dict) else None


def _fetch_work_by_doi(doi: str) -> dict[str, Any] | None:
    normalized = _normalize_doi(doi)
    if not normalized:
        return None

    params = {
        "filter": f"doi:{normalized}",
        "per-page": "1",
    }
    payload = _request_json(_url_with_mailto(OPENALEX_WORKS_URL, params))
    if not payload:
        return None

    results = payload.get("results", [])
    if not isinstance(results, list) or not results:
        return None

    first = results[0]
    return first if isinstance(first, dict) else None


def _referenced_work_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    reference_ids: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized = _normalize_openalex_id(item)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        reference_ids.append(normalized)
        seen.add(key)
    return reference_ids


def _request_json(url: str) -> dict[str, Any] | None:
    request = Request(url, headers=_openalex_headers())
    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", getattr(response, "code", 200))
            if status < 200 or status >= 300:
                logger.warning("OpenAlex returned non-2xx status: %s", status)
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.warning("OpenAlex request failed: %s", exc)
        return None

    return payload if isinstance(payload, dict) else None


def _url_with_mailto(base_url: str, params: dict[str, str]) -> str:
    query_params = dict(params)
    mailto = os.getenv("OPENALEX_MAILTO")
    if mailto:
        query_params["mailto"] = mailto
    if not query_params:
        return base_url
    return f"{base_url}?{urlencode(query_params)}"


def _openalex_headers() -> dict[str, str]:
    mailto = os.getenv("OPENALEX_MAILTO")
    if mailto:
        return {"User-Agent": f"SPAR Scholar Agent (mailto: {mailto})"}
    return {"User-Agent": "SPAR Scholar Agent (mailto: unavailable)"}


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

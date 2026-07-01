"""arXiv public API connector."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from scholar_agent.connectors.schemas import ConnectorSearchResult
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers, PaperUrls


logger = logging.getLogger(__name__)

ARXIV_QUERY_URL = "https://export.arxiv.org/api/query"
DEFAULT_TIMEOUT_SECONDS = 10.0
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def search_arxiv(query: str, limit: int = 20) -> list[Paper]:
    """Search papers from the arXiv public API."""

    return search_arxiv_detailed(query, limit).papers


def search_arxiv_detailed(query: str, limit: int = 20) -> ConnectorSearchResult:
    """Search papers from the arXiv public API with diagnostic details."""

    query = query.strip()
    if not query or limit <= 0:
        return ConnectorSearchResult()

    params = {
        "search_query": f"all:{query}",
        "start": "0",
        "max_results": str(limit),
    }
    request = Request(
        f"{ARXIV_QUERY_URL}?{urlencode(params)}",
        headers={"User-Agent": "SPAR Scholar Agent"},
    )

    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", getattr(response, "code", 200))
            if status < 200 or status >= 300:
                message = f"arXiv search returned non-2xx status: {status}"
                logger.warning(message)
                return ConnectorSearchResult(
                    error_message=message,
                    warnings=[message],
                )
            payload = response.read()
        root = ET.fromstring(payload)
    except (HTTPError, URLError, TimeoutError, OSError, ET.ParseError) as exc:
        message = f"arXiv search failed: {exc}"
        logger.warning(message)
        return ConnectorSearchResult(
            error_message=message,
            warnings=[message],
        )

    papers: list[Paper] = []
    warnings: list[str] = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        try:
            paper = _parse_entry(entry)
        except Exception as exc:  # noqa: BLE001 - isolate malformed records
            message = f"Failed to parse arXiv entry: {exc}"
            logger.warning(message)
            warnings.append(message)
            continue
        if paper is not None:
            papers.append(paper)

    return ConnectorSearchResult(papers=papers, warnings=warnings)


def _parse_entry(entry: ET.Element) -> Paper | None:
    landing_page = _text(entry.find(f"{ATOM_NS}id"))
    title = _normalize_space(_text(entry.find(f"{ATOM_NS}title"))) or "Untitled arXiv Paper"
    abstract = _normalize_space(_text(entry.find(f"{ATOM_NS}summary"))) or ""
    published = _text(entry.find(f"{ATOM_NS}published")) or _text(entry.find(f"{ATOM_NS}updated"))
    year = _parse_year(published)
    authors = [
        name
        for author in entry.findall(f"{ATOM_NS}author")
        if (name := _normalize_space(_text(author.find(f"{ATOM_NS}name"))))
    ]
    doi = _normalize_space(_text(entry.find(f"{ARXIV_NS}doi")))
    venue = _normalize_space(_text(entry.find(f"{ARXIV_NS}journal_ref"))) or "arXiv"
    arxiv_id = _extract_arxiv_id(landing_page)
    pdf_url = _extract_pdf_url(entry, landing_page)

    return Paper(
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        abstract=abstract,
        identifiers=PaperIdentifiers(
            doi=doi,
            arxiv_id=arxiv_id,
        ),
        urls=PaperUrls(
            landing_page=landing_page,
            pdf=pdf_url,
        ),
        sources=["arxiv"],
        citation_count=0,
    )


def _extract_pdf_url(entry: ET.Element, landing_page: str | None) -> str | None:
    for link in entry.findall(f"{ATOM_NS}link"):
        href = link.attrib.get("href")
        if not href:
            continue
        if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
            return href
    if landing_page and "/abs/" in landing_page:
        return landing_page.replace("/abs/", "/pdf/")
    return None


def _extract_arxiv_id(landing_page: str | None) -> str | None:
    if not landing_page:
        return None
    parsed = urlparse(landing_page)
    raw_id = parsed.path.rstrip("/").split("/")[-1] or landing_page.rstrip("/").split("/")[-1]
    return re.sub(r"v\d+$", "", raw_id)


def _parse_year(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).year
    except ValueError:
        match = re.match(r"(\d{4})", value)
        if match:
            return int(match.group(1))
    return None


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    return element.text.strip() or None


def _normalize_space(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None

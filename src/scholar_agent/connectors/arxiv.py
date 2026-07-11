"""arXiv public API connector."""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from scholar_agent.connectors.schemas import ConnectorSearchResult
from scholar_agent.core.diagnostics_schemas import ConnectorDiagnostics
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers, PaperUrls


logger = logging.getLogger(__name__)

ARXIV_QUERY_URL = "https://export.arxiv.org/api/query"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RETRIES = 1
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def search_arxiv(query: str, limit: int = 20) -> list[Paper]:
    """Search papers from the arXiv public API."""

    return search_arxiv_detailed(query, limit).papers


def search_arxiv_detailed(
    query: str,
    limit: int = 20,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_sleep: Callable[[float], None] | None = None,
) -> ConnectorSearchResult:
    """Search papers from the arXiv public API with diagnostic details."""

    start = time.perf_counter()
    query = query.strip()
    if not query or limit <= 0:
        return ConnectorSearchResult(
            diagnostics=ConnectorDiagnostics(
                latency_seconds=time.perf_counter() - start
            )
        )

    params = {
        "search_query": f"all:{query}",
        "start": "0",
        "max_results": str(limit),
    }
    request = Request(
        f"{ARXIV_QUERY_URL}?{urlencode(params)}",
        headers={"User-Agent": "ScholarNavigator"},
    )

    payload, error_message, warnings, diagnostics = _request_feed_detailed(
        request,
        max_retries=max_retries,
        retry_sleep=retry_sleep,
    )
    if payload is None:
        return ConnectorSearchResult(
            error_message=error_message,
            warnings=warnings,
            latency_seconds=time.perf_counter() - start,
            diagnostics=_with_total_latency(diagnostics, start),
        )

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        message = f"arXiv search failed: {exc}"
        logger.warning(message)
        return ConnectorSearchResult(
            error_message=message,
            warnings=warnings + [message],
            latency_seconds=time.perf_counter() - start,
            diagnostics=_with_total_latency(
                diagnostics.model_copy(
                    update={"error_count": diagnostics.error_count + 1}
                ),
                start,
            ),
        )

    papers: list[Paper] = []
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

    return ConnectorSearchResult(
        papers=papers,
        warnings=warnings,
        latency_seconds=time.perf_counter() - start,
        diagnostics=_with_total_latency(diagnostics, start),
    )


def _request_feed_detailed(
    request: Request,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_sleep: Callable[[float], None] | None = None,
) -> tuple[bytes | None, str | None, list[str], ConnectorDiagnostics]:
    started_at = time.perf_counter()
    warnings: list[str] = []
    attempts = max(0, max_retries) + 1
    sleep = retry_sleep or time.sleep

    request_count = 0
    retry_count = 0
    for attempt in range(attempts):
        request_count += 1
        retry_count += int(attempt > 0)
        try:
            with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
                status = getattr(response, "status", getattr(response, "code", 200))
                if status < 200 or status >= 300:
                    message = f"arXiv search returned non-2xx status: {status}"
                    if _should_retry_status(status) and attempt < attempts - 1:
                        _record_retry_warning(
                            warnings,
                            attempt=attempt,
                            attempts=attempts,
                            reason=message,
                        )
                        sleep(_retry_backoff_seconds(attempt))
                        continue
                    logger.warning(message)
                    return None, message, warnings + [message], ConnectorDiagnostics(
                        request_count=request_count,
                        retry_count=retry_count,
                        error_count=1,
                        latency_seconds=time.perf_counter() - started_at,
                    )
                return response.read(), None, warnings, ConnectorDiagnostics(
                    request_count=request_count,
                    retry_count=retry_count,
                    latency_seconds=time.perf_counter() - started_at,
                )
        except HTTPError as exc:
            if _should_retry_status(exc.code) and attempt < attempts - 1:
                _record_retry_warning(
                    warnings,
                    attempt=attempt,
                    attempts=attempts,
                    reason=str(exc),
                )
                sleep(_retry_backoff_seconds(attempt))
                continue
            message = f"arXiv search failed: {exc}"
            logger.warning(message)
            return None, message, warnings + [message], ConnectorDiagnostics(
                request_count=request_count,
                retry_count=retry_count,
                error_count=1,
                latency_seconds=time.perf_counter() - started_at,
            )
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < attempts - 1:
                _record_retry_warning(
                    warnings,
                    attempt=attempt,
                    attempts=attempts,
                    reason=str(exc),
                )
                sleep(_retry_backoff_seconds(attempt))
                continue
            message = f"arXiv search failed: {exc}"
            logger.warning(message)
            return None, message, warnings + [message], ConnectorDiagnostics(
                request_count=request_count,
                retry_count=retry_count,
                error_count=1,
                latency_seconds=time.perf_counter() - started_at,
            )

    message = "arXiv search failed: retry attempts exhausted"
    logger.warning(message)
    return None, message, warnings + [message], ConnectorDiagnostics(
        request_count=request_count,
        retry_count=retry_count,
        error_count=1,
        latency_seconds=time.perf_counter() - started_at,
    )


def _with_total_latency(
    diagnostics: ConnectorDiagnostics,
    started_at: float,
) -> ConnectorDiagnostics:
    return diagnostics.model_copy(
        update={"latency_seconds": time.perf_counter() - started_at}
    )


def _record_retry_warning(
    warnings: list[str],
    *,
    attempt: int,
    attempts: int,
    reason: str,
) -> None:
    message = (
        f"arXiv search transient error on attempt {attempt + 1}/{attempts}: "
        f"{reason}; retried"
    )
    logger.warning(message)
    warnings.append(message)


def _retry_backoff_seconds(attempt: int) -> float:
    return DEFAULT_RETRY_BACKOFF_SECONDS * (attempt + 1)


def _should_retry_status(status: int | None) -> bool:
    return status == 429 or (status is not None and 500 <= status <= 599)


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

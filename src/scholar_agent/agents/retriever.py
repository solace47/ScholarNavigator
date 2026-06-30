"""Multi-source retrieval aggregation."""

from __future__ import annotations

import time
from collections.abc import Callable

from pydantic import BaseModel, Field

from scholar_agent.connectors import search_arxiv, search_openalex
from scholar_agent.core.dedup import deduplicate_papers
from scholar_agent.core.paper_schemas import Paper


SUPPORTED_SOURCES = ("openalex", "arxiv")


class SourceStats(BaseModel):
    source: str
    returned_count: int = 0
    latency_seconds: float = 0.0
    error_message: str | None = None


class RetrievalOutput(BaseModel):
    query: str
    requested_sources: list[str]
    raw_count: int
    deduplicated_count: int
    papers: list[Paper] = Field(default_factory=list)
    source_stats: list[SourceStats] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    latency_seconds: float = 0.0


def retrieve_papers(
    query: str,
    limit_per_source: int = 20,
    sources: list[str] | None = None,
) -> RetrievalOutput:
    """Retrieve papers from supported sources and deduplicate them."""

    requested_sources = _normalize_sources(sources)
    start = time.perf_counter()
    warnings: list[str] = []
    source_stats: list[SourceStats] = []
    raw_papers: list[Paper] = []

    if not query.strip():
        warnings.append("empty_query")

    for source in requested_sources:
        search = _source_registry().get(source)
        if search is None:
            message = f"unsupported_source:{source}"
            warnings.append(message)
            source_stats.append(
                SourceStats(
                    source=source,
                    returned_count=0,
                    latency_seconds=0.0,
                    error_message=message,
                )
            )
            continue

        source_start = time.perf_counter()
        try:
            papers = search(query, limit_per_source)
            raw_papers.extend(papers)
            source_stats.append(
                SourceStats(
                    source=source,
                    returned_count=len(papers),
                    latency_seconds=time.perf_counter() - source_start,
                )
            )
        except Exception as exc:  # noqa: BLE001 - isolate connector failures
            message = f"{source} failed: {exc}"
            warnings.append(message)
            source_stats.append(
                SourceStats(
                    source=source,
                    returned_count=0,
                    latency_seconds=time.perf_counter() - source_start,
                    error_message=str(exc),
                )
            )

    deduplicated = deduplicate_papers(raw_papers)
    return RetrievalOutput(
        query=query,
        requested_sources=requested_sources,
        raw_count=len(raw_papers),
        deduplicated_count=len(deduplicated),
        papers=deduplicated,
        source_stats=source_stats,
        warnings=warnings,
        latency_seconds=time.perf_counter() - start,
    )


def _normalize_sources(sources: list[str] | None) -> list[str]:
    if sources is None:
        return list(SUPPORTED_SOURCES)

    normalized: list[str] = []
    seen: set[str] = set()
    for source in sources:
        key = source.strip().lower()
        if not key or key in seen:
            continue
        normalized.append(key)
        seen.add(key)
    return normalized


def _source_registry() -> dict[str, Callable[[str, int], list[Paper]]]:
    return {
        "openalex": search_openalex,
        "arxiv": search_arxiv,
    }


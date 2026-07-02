"""Multi-source retrieval aggregation."""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from collections.abc import Callable
from threading import RLock

from pydantic import BaseModel, Field

from scholar_agent.connectors import (
    ConnectorSearchResult,
    search_arxiv_detailed,
    search_openalex_detailed,
    search_semantic_scholar_detailed,
)
from scholar_agent.core.dedup import deduplicate_papers
from scholar_agent.core.paper_schemas import Paper


SUPPORTED_SOURCES = ("openalex", "arxiv", "semantic_scholar")
DEFAULT_CACHE_TTL_SECONDS = 15 * 60
DEFAULT_CACHE_MAX_ENTRIES = 256
DEFAULT_SOURCE_COOLDOWN_SECONDS = 60
CACHE_DISABLED_VALUES = {"0", "false", "False", "no", "NO", "off", "OFF"}


_CacheKey = tuple[str, str, int]
_RETRIEVAL_CACHE: OrderedDict[_CacheKey, tuple[float, ConnectorSearchResult]] = (
    OrderedDict()
)
_RETRIEVAL_CACHE_LOCK = RLock()
_SOURCE_FAILURE_COOLDOWNS: dict[str, float] = {}
_SOURCE_FAILURE_COOLDOWNS_LOCK = RLock()


class SourceStats(BaseModel):
    source: str
    returned_count: int = 0
    latency_seconds: float = 0.0
    error_message: str | None = None
    cache_hit: bool = False


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

        if _is_source_in_cooldown(source):
            message = f"source_cooldown_skip:{source}"
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
            result, cache_hit = _search_with_cache(
                source,
                query,
                limit_per_source,
                search,
            )
            raw_papers.extend(result.papers)
            warnings.extend(result.warnings)
            if cache_hit:
                warnings.append(f"retrieval_cache_hit:{source}")
            if result.error_message and result.error_message not in warnings:
                warnings.append(result.error_message)
            if _has_cooldown_trigger(result.error_message, result.warnings):
                _record_source_cooldown(source)
            source_stats.append(
                SourceStats(
                    source=source,
                    returned_count=len(result.papers),
                    latency_seconds=time.perf_counter() - source_start,
                    error_message=result.error_message,
                    cache_hit=cache_hit,
                )
            )
        except Exception as exc:  # noqa: BLE001 - isolate connector failures
            message = f"{source} failed: {exc}"
            warnings.append(message)
            if _is_cooldown_error_message(message):
                _record_source_cooldown(source)
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


def clear_retrieval_cache() -> None:
    """Clear the process-local retrieval cache.

    This is primarily intended for tests and local debugging. The cache is
    intentionally in-memory only and is not shared across worker processes.
    """

    with _RETRIEVAL_CACHE_LOCK:
        _RETRIEVAL_CACHE.clear()


def clear_source_cooldowns() -> None:
    """Clear process-local source cooldown state for tests and local debugging."""

    with _SOURCE_FAILURE_COOLDOWNS_LOCK:
        _SOURCE_FAILURE_COOLDOWNS.clear()


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


def _source_registry() -> dict[str, Callable[[str, int], ConnectorSearchResult]]:
    return {
        "openalex": search_openalex_detailed,
        "arxiv": search_arxiv_detailed,
        "semantic_scholar": search_semantic_scholar_detailed,
    }


def _search_with_cache(
    source: str,
    query: str,
    limit_per_source: int,
    search: Callable[[str, int], ConnectorSearchResult],
) -> tuple[ConnectorSearchResult, bool]:
    config = _cache_config()
    if not config.enabled:
        return search(query, limit_per_source), False

    key = _cache_key(source, query, limit_per_source)
    cached = _get_cached_result(key, config.ttl_seconds)
    if cached is not None:
        return cached, True

    result = search(query, limit_per_source)
    if result.error_message is None:
        _store_cached_result(key, result, config.max_entries)
    return result, False


class _CacheConfig(BaseModel):
    enabled: bool
    ttl_seconds: float
    max_entries: int


def _cache_config() -> _CacheConfig:
    enabled_value = os.getenv("SCHOLAR_AGENT_RETRIEVAL_CACHE", "1")
    enabled = enabled_value not in CACHE_DISABLED_VALUES
    ttl_seconds = _float_env(
        "SCHOLAR_AGENT_RETRIEVAL_CACHE_TTL_SECONDS",
        DEFAULT_CACHE_TTL_SECONDS,
    )
    max_entries = _int_env(
        "SCHOLAR_AGENT_RETRIEVAL_CACHE_MAX_ENTRIES",
        DEFAULT_CACHE_MAX_ENTRIES,
    )
    if ttl_seconds <= 0 or max_entries <= 0:
        enabled = False
    return _CacheConfig(
        enabled=enabled,
        ttl_seconds=ttl_seconds,
        max_entries=max_entries,
    )


def _cache_key(source: str, query: str, limit_per_source: int) -> _CacheKey:
    return (source.strip().lower(), query.strip(), int(limit_per_source))


def _get_cached_result(
    key: _CacheKey,
    ttl_seconds: float,
) -> ConnectorSearchResult | None:
    now = time.monotonic()
    with _RETRIEVAL_CACHE_LOCK:
        cached = _RETRIEVAL_CACHE.get(key)
        if cached is None:
            return None

        stored_at, result = cached
        if now - stored_at > ttl_seconds:
            _RETRIEVAL_CACHE.pop(key, None)
            return None

        _RETRIEVAL_CACHE.move_to_end(key)
        return result.model_copy(deep=True)


def _store_cached_result(
    key: _CacheKey,
    result: ConnectorSearchResult,
    max_entries: int,
) -> None:
    with _RETRIEVAL_CACHE_LOCK:
        _RETRIEVAL_CACHE[key] = (time.monotonic(), result.model_copy(deep=True))
        _RETRIEVAL_CACHE.move_to_end(key)
        while len(_RETRIEVAL_CACHE) > max_entries:
            _RETRIEVAL_CACHE.popitem(last=False)


def _is_source_in_cooldown(source: str) -> bool:
    cooldown_seconds = _source_cooldown_seconds()
    if cooldown_seconds <= 0:
        return False

    now = time.monotonic()
    with _SOURCE_FAILURE_COOLDOWNS_LOCK:
        expires_at = _SOURCE_FAILURE_COOLDOWNS.get(source)
        if expires_at is None:
            return False
        if now >= expires_at:
            _SOURCE_FAILURE_COOLDOWNS.pop(source, None)
            return False
        return True


def _record_source_cooldown(source: str) -> None:
    cooldown_seconds = _source_cooldown_seconds()
    if cooldown_seconds <= 0:
        return

    with _SOURCE_FAILURE_COOLDOWNS_LOCK:
        _SOURCE_FAILURE_COOLDOWNS[source] = time.monotonic() + cooldown_seconds


def _source_cooldown_seconds() -> float:
    value = _float_env(
        "SCHOLAR_AGENT_SOURCE_COOLDOWN_SECONDS",
        DEFAULT_SOURCE_COOLDOWN_SECONDS,
    )
    return max(0.0, value)


def _has_cooldown_trigger(
    error_message: str | None,
    warnings: list[str],
) -> bool:
    return any(
        _is_cooldown_error_message(message)
        for message in [error_message, *warnings]
        if message
    )


def _is_cooldown_error_message(message: str) -> bool:
    normalized = message.casefold()
    return (
        "http error 429" in normalized
        or " 429" in normalized
        or "timeout" in normalized
        or "timed out" in normalized
    )


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return float(default)
    try:
        return float(value)
    except ValueError:
        return float(default)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return int(default)
    try:
        return int(value)
    except ValueError:
        return int(default)

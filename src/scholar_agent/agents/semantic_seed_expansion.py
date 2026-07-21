"""Deterministic seed selection for Semantic Scholar recommendations."""

from __future__ import annotations

import time
from collections.abc import Callable

from scholar_agent.connectors.schemas import ConnectorSearchResult
from scholar_agent.core.diagnostics_schemas import ConnectorDiagnostics
from scholar_agent.core.search_schemas import (
    RankedPaper,
    SemanticSeedExpansionOutput,
    SemanticSeedExpansionRecord,
    SemanticSeedExpansionSeed,
)


RecommendationFetcher = Callable[[list[str], int], ConnectorSearchResult]


def expand_semantic_seeds(
    ranked_papers: list[RankedPaper],
    fetch_recommendations: RecommendationFetcher,
    *,
    max_seeds: int = 3,
    limit: int = 100,
) -> SemanticSeedExpansionOutput:
    """Select top-ranked exact S2 IDs and make at most one recommendation call."""

    started = time.perf_counter()
    seeds = _select_seeds(ranked_papers, max_seeds=max_seeds)
    if not seeds:
        warning = "semantic_seed_expansion_no_eligible_seed"
        record = SemanticSeedExpansionRecord(
            status="no_eligible_seed",
            seeds=[],
            skip_reason="no_eligible_seed",
            warnings=[warning],
            latency_seconds=time.perf_counter() - started,
        )
        return SemanticSeedExpansionOutput(
            record=record,
            warnings=record.warnings,
            latency_seconds=record.latency_seconds,
        )

    try:
        result = fetch_recommendations(
            [seed.semantic_scholar_id for seed in seeds],
            limit,
        )
    except Exception as exc:  # noqa: BLE001 - preserve initial results on one call failure
        warning = f"semantic_seed_expansion_source_failure:{type(exc).__name__}"
        elapsed = time.perf_counter() - started
        diagnostics = ConnectorDiagnostics(error_count=1, latency_seconds=elapsed)
        record = SemanticSeedExpansionRecord(
            status="source_failure",
            seeds=seeds,
            skip_reason="source_failure",
            warnings=[warning],
            diagnostics=diagnostics,
            latency_seconds=elapsed,
        )
        return SemanticSeedExpansionOutput(
            record=record,
            warnings=record.warnings,
            diagnostics=diagnostics,
            latency_seconds=elapsed,
        )

    elapsed = time.perf_counter() - started
    recorded = result.recorded_diagnostics or ConnectorDiagnostics()
    status = "source_failure" if result.error_message else "success"
    warnings = list(result.warnings)
    if result.error_message:
        warnings.append("semantic_seed_expansion_source_failure")
    record = SemanticSeedExpansionRecord(
        status=status,
        seeds=seeds,
        snapshot_key=result.snapshot_key,
        raw_recommendation_count=len(result.papers),
        skip_reason="source_failure" if result.error_message else None,
        warnings=_dedupe(warnings),
        diagnostics=result.diagnostics,
        recorded_diagnostics=recorded,
        latency_seconds=max(result.latency_seconds, elapsed),
        recorded_latency_seconds=result.recorded_latency_seconds,
    )
    recommendations = [] if result.error_message else list(result.papers)
    return SemanticSeedExpansionOutput(
        recommendations=recommendations,
        record=record,
        warnings=record.warnings,
        diagnostics=result.diagnostics,
        recorded_diagnostics=recorded,
        latency_seconds=record.latency_seconds,
        recorded_latency_seconds=result.recorded_latency_seconds,
    )


def _select_seeds(
    ranked_papers: list[RankedPaper],
    *,
    max_seeds: int,
) -> list[SemanticSeedExpansionSeed]:
    seeds: list[SemanticSeedExpansionSeed] = []
    seen_ids: set[str] = set()
    for ranked in ranked_papers:
        value = ranked.paper.identifiers.semantic_scholar_id
        paper_id = str(value).strip() if value is not None else ""
        key = paper_id.casefold()
        if not paper_id or key in seen_ids:
            continue
        seeds.append(
            SemanticSeedExpansionSeed(
                semantic_scholar_id=paper_id,
                rank=ranked.rank,
                title=ranked.paper.title,
            )
        )
        seen_ids.add(key)
        if len(seeds) >= max(0, max_seeds):
            break
    return seeds


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))

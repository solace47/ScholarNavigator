"""Rule-based single-layer reference expansion."""

from __future__ import annotations

import time
from collections.abc import Callable

from scholar_agent.core.paper_schemas import Paper
from scholar_agent.core.search_schemas import (
    QueryAnalysis,
    RankedPaper,
    RefChainOptions,
    RefChainOutput,
    RefChainRecord,
    RefChainSeed,
    ReferenceEdge,
)


ReferenceFetcher = Callable[[Paper, int], list[Paper]]


class RefChainAgent:
    """Expand references for relevant ranked papers without recursion."""

    def expand(
        self,
        query_analysis: QueryAnalysis,
        ranked_papers: list[RankedPaper],
        fetch_references: ReferenceFetcher,
        options: RefChainOptions | None = None,
    ) -> RefChainOutput:
        del query_analysis  # Reserved for future domain-aware seed policies.
        start = time.perf_counter()
        options = options or RefChainOptions()
        warnings: list[str] = []
        skipped_reasons: list[str] = []
        references: list[Paper] = []
        reference_edges: list[ReferenceEdge] = []

        seeds = _select_seeds(ranked_papers, options)
        if not seeds:
            warnings.append("refchain_no_eligible_seed")
            skipped_reasons.append("refchain_no_eligible_seed")

        for ranked in seeds:
            if len(references) >= options.max_total_references:
                warnings.append("refchain_total_reference_limit_reached")
                break

            seed_id = _paper_identifier(ranked.paper)
            if seed_id is None:
                warning = f"refchain_seed_missing_supported_identifier:{ranked.rank}"
                warnings.append(warning)
                skipped_reasons.append(warning)
                continue

            remaining = options.max_total_references - len(references)
            per_seed_limit = min(options.max_references_per_seed, remaining)
            if per_seed_limit <= 0:
                warnings.append("refchain_total_reference_limit_reached")
                break

            try:
                fetched = fetch_references(ranked.paper, per_seed_limit)
            except Exception as exc:  # noqa: BLE001 - isolate one seed failure
                warning = f"refchain_seed_failed:{ranked.rank}:{exc}"
                warnings.append(warning)
                skipped_reasons.append(warning)
                continue

            for reference in fetched[:per_seed_limit]:
                if len(references) >= options.max_total_references:
                    warnings.append("refchain_total_reference_limit_reached")
                    break
                reference_id = _paper_identifier(reference)
                if reference_id is None:
                    warning = f"refchain_reference_missing_identifier:{ranked.rank}"
                    warnings.append(warning)
                    skipped_reasons.append(warning)
                    continue
                references.append(reference)
                reference_edges.append(
                    ReferenceEdge(
                        seed_paper_id=seed_id,
                        reference_paper_id=reference_id,
                        source="openalex",
                    )
                )

        latency_seconds = time.perf_counter() - start
        record = RefChainRecord(
            seeds=[_to_seed(seed) for seed in seeds],
            reference_edges=reference_edges,
            raw_reference_count=len(references),
            returned_reference_count=len(references),
            skipped_reasons=_dedupe(skipped_reasons),
            warnings=_dedupe(warnings),
            latency_seconds=latency_seconds,
        )
        return RefChainOutput(
            references=references,
            reference_edges=reference_edges,
            record=record,
            warnings=record.warnings,
            latency_seconds=latency_seconds,
        )


def expand_refchain(
    query_analysis: QueryAnalysis,
    ranked_papers: list[RankedPaper],
    fetch_references: ReferenceFetcher,
    options: RefChainOptions | None = None,
) -> RefChainOutput:
    """Expand one layer of references using an injected fetcher."""

    return RefChainAgent().expand(
        query_analysis=query_analysis,
        ranked_papers=ranked_papers,
        fetch_references=fetch_references,
        options=options,
    )


def _select_seeds(
    ranked_papers: list[RankedPaper],
    options: RefChainOptions,
) -> list[RankedPaper]:
    seeds: list[RankedPaper] = []
    for ranked in ranked_papers:
        if ranked.category == "highly_relevant":
            seeds.append(ranked)
        elif (
            ranked.category == "partially_relevant"
            and ranked.final_score >= options.min_seed_score
        ):
            seeds.append(ranked)
        if len(seeds) >= options.max_seed_papers:
            break
    return seeds


def _to_seed(ranked: RankedPaper) -> RefChainSeed:
    return RefChainSeed(
        paper=ranked.paper,
        rank=ranked.rank,
        score=ranked.final_score,
        reason=ranked.ranking_reason,
    )


def _paper_identifier(paper: Paper) -> str | None:
    identifiers = paper.identifiers
    if identifiers.openalex_id:
        return f"openalex:{identifiers.openalex_id.casefold()}"
    if identifiers.doi:
        return f"doi:{identifiers.doi.casefold()}"
    return None


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if not item or item in seen:
            continue
        deduped.append(item)
        seen.add(item)
    return deduped

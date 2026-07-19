"""Benchmark 阶段命中、丢失原因、来源贡献和瓶颈分析。"""

from __future__ import annotations

import statistics
from collections import Counter
from itertools import combinations
from typing import Any

from pydantic import BaseModel, Field

from scholar_agent.core.dedup import normalize_title
from scholar_agent.core.evaluation_schemas import EvalGoldPaper, EvalQuery
from scholar_agent.core.pipeline_diagnostics import (
    DiagnosticCandidate,
    StageCandidateSnapshot,
)
from scholar_agent.evaluation.metrics import (
    canonical_paper_id,
    evaluable_gold_count,
    matched_paper_ids,
    recall_at_k,
)
from scholar_agent.evaluation.selection import ResultPolicy, select_ranked_results
from scholar_agent.services.search_service import SearchServiceOutput


DIAGNOSTIC_K_VALUES = (5, 10, 20, 50)
RETURN_CATEGORIES = {"highly_relevant", "partially_relevant"}
FALSE_NEGATIVE_CATEGORIES = {
    "weakly_relevant",
    "irrelevant",
    "insufficient_evidence",
}
RETRIEVAL_STAGES = (
    "initial_retrieval",
    "query_evolution_retrieval",
    "refchain_retrieval",
)
STAGE_ORDER = (
    "initial_retrieval",
    "initial_deduplicated",
    "initial_judged",
    "initial_reranked",
    "query_evolution_retrieval",
    "post_evolution_deduplicated",
    "post_evolution_judged",
    "post_evolution_reranked",
    "refchain_retrieval",
    "post_refchain_deduplicated",
    "post_refchain_judged",
    "post_refchain_reranked",
    "final_ranked",
    "final_returned",
)


class GoldStageDiagnostic(BaseModel):
    case_id: str
    query: str
    gold_id: str | None
    gold_title: str | None
    found: bool
    first_found_stage: str | None = None
    sources: list[str] = Field(default_factory=list)
    initial_rank: int | None = None
    final_rank: int | None = None
    final_category: str | None = None
    drop_reason: str


def analyze_search_stages(
    eval_query: EvalQuery,
    output: SearchServiceOutput,
    *,
    result_policy: ResultPolicy,
) -> dict[str, Any]:
    snapshots = list(output.stage_snapshots)
    snapshots.append(_final_returned_snapshot(output, result_policy))
    by_stage = {snapshot.stage: snapshot for snapshot in snapshots}
    gold_diagnostics = [
        _analyze_gold(eval_query, gold, by_stage, output)
        for gold in eval_query.gold_papers
        if gold.relevance_grade > 0
    ]
    stage_metrics = _case_stage_metrics(eval_query, by_stage)
    judgement = _judgement_metrics(gold_diagnostics)
    reranking = _reranking_metrics(gold_diagnostics)
    source_contribution = _source_contribution(
        eval_query,
        snapshots,
        output,
    )
    retrieval_diagnostics = _retrieval_query_diagnostics(output)
    query_strategy_contribution = _query_strategy_gold_contribution(
        eval_query,
        snapshots,
        output,
    )
    return {
        "snapshots": [item.model_dump(mode="json") for item in snapshots],
        "gold_diagnostics": [item.model_dump(mode="json") for item in gold_diagnostics],
        "stage_metrics": stage_metrics,
        "judgement": judgement,
        "reranking": reranking,
        "source_contribution": source_contribution,
        "retrieval_diagnostics": retrieval_diagnostics,
        "query_strategy_contribution": query_strategy_contribution,
        "budget_stopped": bool(output.budget_status.stop_reasons),
        "budget_stop_reasons": list(output.budget_status.stop_reasons),
    }


def aggregate_stage_diagnostics(
    case_diagnostics: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    gold_rows = [
        row
        for case in case_diagnostics
        for row in case.get("gold_diagnostics", [])
    ]
    candidate_names = sorted(
        {
            name
            for case in case_diagnostics
            for name in case.get("stage_metrics", {}).get("candidate_recall", {})
        }
    )
    ranked_names = sorted(
        {
            name
            for case in case_diagnostics
            for name in case.get("stage_metrics", {}).get("recall_at_k", {})
        }
    )
    candidate_recall = {
        name: _average_optional(
            [
                case.get("stage_metrics", {})
                .get("candidate_recall", {})
                .get(name)
                for case in case_diagnostics
            ]
        )
        for name in candidate_names
    }
    recall_at_k = {
        name: {
            str(k): _average_optional(
                [
                    case.get("stage_metrics", {})
                    .get("recall_at_k", {})
                    .get(name, {})
                    .get(str(k))
                    for case in case_diagnostics
                ]
            )
            for k in DIAGNOSTIC_K_VALUES
        }
        for name in ranked_names
    }
    judgement = _aggregate_judgement(case_diagnostics)
    reranking = _aggregate_reranking(case_diagnostics)
    sources = _aggregate_sources(case_diagnostics, len(gold_rows))
    retrieval_diagnostics = _aggregate_retrieval_diagnostics(case_diagnostics)
    query_strategy_contribution = _aggregate_strategy_contribution(
        case_diagnostics
    )
    drop_reasons = dict(
        sorted(Counter(str(row.get("drop_reason")) for row in gold_rows).items())
    )
    budget_stop_rate = (
        sum(bool(case.get("budget_stopped")) for case in case_diagnostics)
        / len(case_diagnostics)
        if case_diagnostics
        else 0.0
    )
    stage_metrics = {
        "case_count": len(case_diagnostics),
        "gold_count": len(gold_rows),
        "candidate_recall": candidate_recall,
        "recall_at_k": recall_at_k,
        "initial_retrieval_recall": candidate_recall.get("initial_retrieval"),
        "initial_deduplicated_recall": candidate_recall.get(
            "initial_deduplicated"
        ),
        "post_judgement_recall": candidate_recall.get(
            "post_judgement_retained"
        ),
        "post_rerank_recall": recall_at_k.get("initial_reranked", {}),
        "post_evolution_recall": candidate_recall.get(
            "post_evolution_deduplicated"
        ),
        "post_refchain_recall": candidate_recall.get(
            "post_refchain_deduplicated"
        ),
        "final_returned_recall": recall_at_k.get("final_returned", {}),
        "judgement": judgement,
        "reranking": reranking,
        "source_contribution": sources,
        "retrieval_diagnostics": retrieval_diagnostics,
        "query_strategy_contribution": query_strategy_contribution,
        "budget_stop_rate": budget_stop_rate,
    }
    bottlenecks = classify_bottlenecks(stage_metrics)
    stage_metrics["bottleneck_labels"] = bottlenecks
    stage_metrics["sample_warning"] = "small_sample_diagnostic_only"
    error_analysis = {
        "drop_reasons": drop_reasons,
        "bottleneck_labels": bottlenecks,
        "sample_warning": "small_sample_diagnostic_only",
    }
    return stage_metrics, error_analysis, gold_rows


def classify_bottlenecks(stage_metrics: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    case_count = int(stage_metrics.get("case_count") or 0)
    if case_count < 30:
        labels.append("insufficient_sample")
    initial_recall = stage_metrics.get("initial_retrieval_recall")
    if initial_recall is not None and float(initial_recall) < 0.5:
        labels.append("retrieval_recall_bottleneck")
    judgement = stage_metrics.get("judgement") or {}
    if float(judgement.get("gold_false_negative_rate") or 0.0) >= 0.25:
        labels.append("judgement_false_negative_bottleneck")
    reranking = stage_metrics.get("reranking") or {}
    if (
        int(reranking.get("eligible_gold_count") or 0) > 0
        and float(reranking.get("outside_top_20_rate") or 0.0) >= 0.25
    ):
        labels.append("reranking_bottleneck")
    sources = stage_metrics.get("source_contribution") or {}
    if float(sources.get("source_error_rate") or 0.0) >= 0.2:
        labels.append("source_reliability_bottleneck")
    if float(stage_metrics.get("budget_stop_rate") or 0.0) >= 0.2:
        labels.append("budget_bottleneck")
    return labels


def _final_returned_snapshot(
    output: SearchServiceOutput,
    result_policy: ResultPolicy,
) -> StageCandidateSnapshot:
    selected = select_ranked_results(output, policy=result_policy)
    final_snapshot = _snapshot_by_name(output.stage_snapshots, "final_ranked")
    final_candidates = final_snapshot.candidates if final_snapshot else []
    candidates: list[DiagnosticCandidate] = []
    for ranked in selected:
        matched = _matching_candidate(final_candidates, ranked)
        if matched is not None:
            candidates.append(matched.model_copy(deep=True))
            continue
        candidates.append(
            DiagnosticCandidate(
                identifiers=ranked.paper.identifiers,
                title=ranked.paper.title,
                year=ranked.paper.year,
                sources=list(ranked.paper.sources),
                rank=ranked.rank,
                category=ranked.category,
                final_score=ranked.final_score,
                matched_terms=list(ranked.matched_terms),
                warnings=list(ranked.warnings),
            )
        )
    return StageCandidateSnapshot(stage="final_returned", candidates=candidates)


def _analyze_gold(
    eval_query: EvalQuery,
    gold: EvalGoldPaper,
    snapshots: dict[str, StageCandidateSnapshot],
    output: SearchServiceOutput,
) -> GoldStageDiagnostic:
    stage_matches = {
        stage: _matches(snapshot.candidates, gold)
        for stage, snapshot in snapshots.items()
        if snapshot.status == "completed"
    }
    retrieval_matches = {
        stage: stage_matches.get(stage, []) for stage in RETRIEVAL_STAGES
    }
    found = any(retrieval_matches.values())
    first_found = next(
        (stage for stage in STAGE_ORDER if stage_matches.get(stage)),
        None,
    )
    sources = _stable_strings(
        source
        for matches in retrieval_matches.values()
        for candidate in matches
        for source in candidate.sources
    )
    initial_rank = _first_rank(stage_matches.get("initial_reranked", []))
    final_matches = stage_matches.get("final_ranked", [])
    final_rank = _first_rank(final_matches)
    judgement_match = _latest_match(
        stage_matches,
        (
            "post_refchain_judged",
            "post_evolution_judged",
            "initial_judged",
        ),
    )
    final_category = (
        judgement_match.category
        if judgement_match is not None
        else (final_matches[0].category if final_matches else None)
    )
    drop_reason = _drop_reason(
        gold,
        found=found,
        stage_matches=stage_matches,
        final_category=final_category,
        final_rank=final_rank,
        output=output,
        snapshots=snapshots,
    )
    return GoldStageDiagnostic(
        case_id=eval_query.query_id,
        query=eval_query.query,
        gold_id=canonical_paper_id(gold),
        gold_title=gold.title,
        found=found,
        first_found_stage=first_found,
        sources=sources,
        initial_rank=initial_rank,
        final_rank=final_rank,
        final_category=final_category,
        drop_reason=drop_reason,
    )


def _drop_reason(
    gold: EvalGoldPaper,
    *,
    found: bool,
    stage_matches: dict[str, list[DiagnosticCandidate]],
    final_category: str | None,
    final_rank: int | None,
    output: SearchServiceOutput,
    snapshots: dict[str, StageCandidateSnapshot],
) -> str:
    if stage_matches.get("final_returned"):
        return "returned"
    if not found:
        initial = snapshots.get("initial_retrieval")
        if initial and initial.skipped_reason == "budget_stopped_before_retrieval":
            return "budget_stopped_before_retrieval"
        if _all_sources_failed(output):
            return "source_failed"
        if _title_seen_without_identifier_match(gold, snapshots):
            return "identifier_not_matched"
        return "not_retrieved"
    first_retrieval = next(
        (stage for stage in RETRIEVAL_STAGES if stage_matches.get(stage)),
        None,
    )
    expected_dedup = {
        "initial_retrieval": "initial_deduplicated",
        "query_evolution_retrieval": "post_evolution_deduplicated",
        "refchain_retrieval": "post_refchain_deduplicated",
    }.get(first_retrieval or "")
    if expected_dedup and not stage_matches.get(expected_dedup):
        return "removed_or_merged_by_dedup"
    if final_category == "irrelevant":
        return "judged_irrelevant"
    if final_category == "weakly_relevant":
        return "judged_weakly_relevant"
    if final_category == "insufficient_evidence":
        return "insufficient_evidence"
    if final_category in RETURN_CATEGORIES:
        if final_rank is None or final_rank > output.search_plan.top_k:
            return "outside_final_top_k"
    return "not_in_return_categories"


def _case_stage_metrics(
    eval_query: EvalQuery,
    snapshots: dict[str, StageCandidateSnapshot],
) -> dict[str, Any]:
    candidate_stage_names = (
        "initial_retrieval",
        "initial_deduplicated",
        "query_evolution_retrieval",
        "post_evolution_deduplicated",
        "refchain_retrieval",
        "post_refchain_deduplicated",
    )
    candidate_recall = {
        stage: _candidate_recall(snapshot, eval_query.gold_papers)
        for stage in candidate_stage_names
        if (snapshot := snapshots.get(stage)) is not None
    }
    latest_judged = _latest_snapshot(
        snapshots,
        ("post_refchain_judged", "post_evolution_judged", "initial_judged"),
    )
    candidate_recall["post_judgement_retained"] = (
        _candidate_recall(
            latest_judged.model_copy(
                update={
                    "candidates": [
                        item
                        for item in latest_judged.candidates
                        if item.category in RETURN_CATEGORIES
                    ]
                }
            ),
            eval_query.gold_papers,
        )
        if latest_judged is not None
        else None
    )
    ranked_stages = (
        "initial_reranked",
        "post_evolution_reranked",
        "post_refchain_reranked",
        "final_ranked",
        "final_returned",
    )
    recall_maps: dict[str, dict[str, float | None]] = {}
    for stage in ranked_stages:
        snapshot = snapshots.get(stage)
        recall_maps[stage] = {
            str(k): (
                recall_at_k(snapshot.candidates, eval_query.gold_papers, k)
                if snapshot is not None and snapshot.status == "completed"
                else None
            )
            for k in DIAGNOSTIC_K_VALUES
        }
    return {
        "candidate_recall": candidate_recall,
        "recall_at_k": recall_maps,
    }


def _judgement_metrics(
    gold_diagnostics: list[GoldStageDiagnostic],
) -> dict[str, Any]:
    retrieved = [item for item in gold_diagnostics if item.found]
    counts = Counter(item.final_category for item in retrieved if item.final_category)
    false_negative_count = sum(
        item.final_category in FALSE_NEGATIVE_CATEGORIES
        and item.drop_reason != "returned"
        for item in retrieved
    )
    retained = sum(item.final_category in RETURN_CATEGORIES for item in retrieved)
    denominator = len(retrieved)
    return {
        "retrieved_gold_count": denominator,
        "gold_judged_highly_relevant": counts["highly_relevant"],
        "gold_judged_partially_relevant": counts["partially_relevant"],
        "gold_judged_weakly_relevant": counts["weakly_relevant"],
        "gold_judged_irrelevant": counts["irrelevant"],
        "gold_judged_insufficient_evidence": counts["insufficient_evidence"],
        "gold_retention_after_judgement": (
            retained / denominator if denominator else 0.0
        ),
        "gold_false_negative_count": false_negative_count,
        "gold_false_negative_rate": (
            false_negative_count / denominator if denominator else 0.0
        ),
    }


def _reranking_metrics(
    gold_diagnostics: list[GoldStageDiagnostic],
) -> dict[str, Any]:
    eligible = [
        item
        for item in gold_diagnostics
        if item.found and item.final_category in RETURN_CATEGORIES
    ]
    ranks = [item.final_rank for item in eligible if item.final_rank is not None]
    outside = sum(item.final_rank is None or item.final_rank > 20 for item in eligible)
    return {
        "eligible_gold_count": len(eligible),
        "gold_in_top_5": sum(rank <= 5 for rank in ranks),
        "gold_in_top_10": sum(rank <= 10 for rank in ranks),
        "gold_in_top_20": sum(rank <= 20 for rank in ranks),
        "gold_outside_top_20": outside,
        "outside_top_20_rate": outside / len(eligible) if eligible else 0.0,
        "average_gold_rank": statistics.mean(ranks) if ranks else None,
        "median_gold_rank": statistics.median(ranks) if ranks else None,
        "gold_ranks": ranks,
    }


def _source_contribution(
    eval_query: EvalQuery,
    snapshots: list[StageCandidateSnapshot],
    output: SearchServiceOutput,
) -> dict[str, Any]:
    candidates = [
        candidate
        for snapshot in snapshots
        if snapshot.stage in RETRIEVAL_STAGES and snapshot.status == "completed"
        for candidate in snapshot.candidates
    ]
    sources = sorted(
        {
            _normalize_source(source)
            for candidate in candidates
            for source in candidate.sources
            if _normalize_source(source)
        }.union(output.search_plan.selected_sources)
    )
    candidate_ids_by_source: dict[str, set[str]] = {source: set() for source in sources}
    gold_ids_by_source: dict[str, set[str]] = {source: set() for source in sources}
    for candidate in candidates:
        candidate_id = canonical_paper_id(candidate)
        candidate_sources = {
            _normalize_source(source) for source in candidate.sources
        } - {""}
        for source in candidate_sources:
            candidate_ids_by_source.setdefault(source, set())
            gold_ids_by_source.setdefault(source, set())
            if candidate_id:
                candidate_ids_by_source[source].add(candidate_id)
            for index, gold in enumerate(eval_query.gold_papers):
                if _candidate_matches(candidate, gold):
                    gold_ids_by_source[source].add(f"{eval_query.query_id}:{index}")

    stats = {source: _empty_source_stats() for source in sources}
    for source_stat in output.source_stats:
        source = "openalex" if source_stat.source == "refchain" else source_stat.source
        if source not in stats:
            stats[source] = _empty_source_stats()
        diagnostics = source_stat.diagnostics
        stats[source]["request_count"] += diagnostics.request_count
        stats[source]["success_count"] += int(
            source_stat.error_message is None
            and source_stat.source_skipped_reason is None
        )
        stats[source]["error_count"] += diagnostics.error_count
        stats[source]["returned_candidate_count"] += source_stat.returned_count
        stats[source]["latency_seconds"] += source_stat.latency_seconds

    total_gold = max(1, evaluable_gold_count(eval_query.gold_papers))
    for source, item in stats.items():
        source_candidates = candidate_ids_by_source.get(source, set())
        source_gold = gold_ids_by_source.get(source, set())
        other_gold = set().union(
            *(
                values
                for other, values in gold_ids_by_source.items()
                if other != source
            )
        ) if len(gold_ids_by_source) > 1 else set()
        item["unique_candidate_count"] = len(source_candidates)
        item["gold_hit_count"] = len(source_gold)
        item["unique_gold_hit_count"] = len(source_gold - other_gold)
        item["gold_recall_contribution"] = len(source_gold - other_gold) / total_gold

    overlap: dict[str, dict[str, int]] = {}
    for left, right in combinations(sorted(stats), 2):
        overlap[f"{left} ∩ {right}"] = {
            "candidate_count": len(
                candidate_ids_by_source.get(left, set())
                & candidate_ids_by_source.get(right, set())
            ),
            "gold_hit_count": len(
                gold_ids_by_source.get(left, set())
                & gold_ids_by_source.get(right, set())
            ),
        }
    request_count = sum(item["request_count"] for item in stats.values())
    error_count = sum(item["error_count"] for item in stats.values())
    return {
        "sources": stats,
        "overlap": overlap,
        "source_error_rate": error_count / request_count if request_count else 0.0,
    }


def _retrieval_query_diagnostics(output: SearchServiceOutput) -> dict[str, Any]:
    stats = list(output.source_stats)
    adapted = [item.adapted_query for item in stats if item.adapted_query]
    actual = [item for item in stats if item.diagnostics.request_count > 0]
    empty = [
        item
        for item in actual
        if item.returned_count == 0 and item.error_message is None
    ]
    errors: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    by_source: dict[str, dict[str, Any]] = {}
    for item in stats:
        source = item.source
        bucket = by_source.setdefault(
            source,
            {
                "logical_call_count": 0,
                "request_count": 0,
                "empty_result_count": 0,
                "error_count": 0,
                "skipped_count": 0,
                "strategies": {},
            },
        )
        bucket["logical_call_count"] += int(item.logical_call_executed)
        bucket["request_count"] += item.diagnostics.request_count
        bucket["empty_result_count"] += int(
            item.diagnostics.request_count > 0
            and item.returned_count == 0
            and item.error_message is None
        )
        bucket["error_count"] += item.diagnostics.error_count
        bucket["skipped_count"] += int(item.source_skipped_reason is not None)
        if item.adaptation_strategy:
            strategies = bucket["strategies"]
            strategies[item.adaptation_strategy] = (
                int(strategies.get(item.adaptation_strategy, 0)) + 1
            )
        if item.source_skipped_reason:
            skipped[item.source_skipped_reason] += 1
            continue
        message = (item.error_message or "").casefold()
        if "400" in message:
            errors["http_400"] += 1
        elif "429" in message:
            errors["http_429"] += 1
        elif "timed out" in message or "timeout" in message:
            errors["timeout"] += 1
        elif item.error_message:
            errors["other"] += 1
    return {
        "logical_call_count": sum(item.logical_call_executed for item in stats),
        "actual_request_count": sum(
            item.diagnostics.request_count for item in stats
        ),
        "unique_adapted_query_count": len(
            {_normalized_query(item) for item in adapted}
        ),
        "average_adapted_query_length": (
            sum(len(item) for item in adapted) / len(adapted) if adapted else 0.0
        ),
        "average_adapted_keyword_count": (
            sum(len(item.split()) for item in adapted) / len(adapted)
            if adapted
            else 0.0
        ),
        "empty_result_count": len(empty),
        "empty_result_rate": len(empty) / len(actual) if actual else 0.0,
        "errors": dict(sorted(errors.items())),
        "skipped": dict(sorted(skipped.items())),
        "adaptive": _adaptive_retrieval_diagnostics(stats),
        "by_source": by_source,
    }


def _adaptive_retrieval_diagnostics(stats: list[Any]) -> dict[str, Any]:
    decisions = [
        item
        for item in stats
        if item.adaptation_strategy == "compact_core"
        and item.compact_query_executed is not None
    ]
    executed = [item for item in decisions if item.compact_query_executed]
    safe_keys = {
        _paper_identity_key(paper)
        for item in stats
        if item.adaptation_strategy in {"safe_original", "fallback_original"}
        for paper in item.diagnostic_papers
    }
    compact_keys = {
        _paper_identity_key(paper)
        for item in executed
        for paper in item.diagnostic_papers
    }
    return {
        "compact_decision_count": len(decisions),
        "compact_executed_count": len(executed),
        "compact_execution_ratio": (
            len(executed) / len(decisions) if decisions else 0.0
        ),
        "compact_added_unique_candidate_count": len(compact_keys - safe_keys),
        "skip_reasons": dict(
            sorted(
                Counter(
                    item.compact_query_skipped_reason
                    for item in decisions
                    if item.compact_query_skipped_reason
                ).items()
            )
        ),
    }


def _paper_identity_key(paper: Any) -> str:
    identifiers = paper.identifiers
    for value in (
        identifiers.doi,
        identifiers.arxiv_id,
        identifiers.semantic_scholar_id,
        identifiers.openalex_id,
        identifiers.pubmed_id,
    ):
        if value:
            return str(value).casefold()
    return f"{paper.title.casefold()}::{paper.year}"


def _query_strategy_gold_contribution(
    eval_query: EvalQuery,
    snapshots: list[StageCandidateSnapshot],
    output: SearchServiceOutput,
) -> dict[str, Any]:
    purposes = {
        item.query: item.purpose for item in output.search_plan.subqueries
    }
    purpose_hits: dict[str, set[str]] = {}
    adaptation_hits: dict[str, set[str]] = {}
    compact_only_hits: set[str] = set()
    initial = next(
        (item for item in snapshots if item.stage == "initial_retrieval"),
        None,
    )
    if initial is None:
        return {"subquery_purposes": {}, "adaptation_strategies": {}}
    for candidate in initial.candidates:
        matched = {
            f"{eval_query.query_id}:{index}"
            for index, gold in enumerate(eval_query.gold_papers)
            if _candidate_matches(candidate, gold)
        }
        if not matched:
            continue
        strategies = {
            provenance.adaptation_strategy or "unadapted"
            for provenance in candidate.provenance
        }
        if "compact_core" in strategies and not strategies.intersection(
            {"safe_original", "fallback_original"}
        ):
            compact_only_hits.update(matched)
        for provenance in candidate.provenance:
            purpose = purposes.get(provenance.origin_subquery, "unknown")
            purpose_hits.setdefault(purpose, set()).update(matched)
            strategy = provenance.adaptation_strategy or "unadapted"
            adaptation_hits.setdefault(strategy, set()).update(matched)
    return {
        "subquery_purposes": {
            key: len(value) for key, value in sorted(purpose_hits.items())
        },
        "adaptation_strategies": {
            key: len(value) for key, value in sorted(adaptation_hits.items())
        },
        "compact_gold_increment": len(compact_only_hits),
    }


def _aggregate_retrieval_diagnostics(
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    diagnostics = [case.get("retrieval_diagnostics", {}) for case in cases]
    logical = sum(int(item.get("logical_call_count") or 0) for item in diagnostics)
    actual = sum(int(item.get("actual_request_count") or 0) for item in diagnostics)
    empty = sum(int(item.get("empty_result_count") or 0) for item in diagnostics)
    errors = Counter()
    skipped = Counter()
    compact_decisions = 0
    compact_executed = 0
    compact_added = 0
    adaptive_skips: Counter[str] = Counter()
    for item in diagnostics:
        errors.update(item.get("errors") or {})
        skipped.update(item.get("skipped") or {})
        adaptive = item.get("adaptive") or {}
        compact_decisions += int(adaptive.get("compact_decision_count") or 0)
        compact_executed += int(adaptive.get("compact_executed_count") or 0)
        compact_added += int(
            adaptive.get("compact_added_unique_candidate_count") or 0
        )
        adaptive_skips.update(adaptive.get("skip_reasons") or {})
    return {
        "logical_call_count": logical,
        "actual_request_count": actual,
        "average_actual_requests_per_case": actual / len(cases) if cases else 0.0,
        "empty_result_count": empty,
        "empty_result_rate": empty / actual if actual else 0.0,
        "errors": dict(sorted(errors.items())),
        "skipped": dict(sorted(skipped.items())),
        "adaptive": {
            "compact_decision_count": compact_decisions,
            "compact_executed_count": compact_executed,
            "compact_execution_ratio": (
                compact_executed / compact_decisions if compact_decisions else 0.0
            ),
            "compact_added_unique_candidate_count": compact_added,
            "compact_average_added_unique_candidates": (
                compact_added / compact_executed if compact_executed else 0.0
            ),
            "skip_reasons": dict(sorted(adaptive_skips.items())),
        },
        "average_adapted_query_length": _average_optional(
            [item.get("average_adapted_query_length") for item in diagnostics]
        ),
        "average_adapted_keyword_count": _average_optional(
            [item.get("average_adapted_keyword_count") for item in diagnostics]
        ),
    }


def _aggregate_strategy_contribution(
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    purposes: Counter[str] = Counter()
    adaptations: Counter[str] = Counter()
    compact_gold_increment = 0
    for case in cases:
        contribution = case.get("query_strategy_contribution") or {}
        purposes.update(contribution.get("subquery_purposes") or {})
        adaptations.update(contribution.get("adaptation_strategies") or {})
        compact_gold_increment += int(
            contribution.get("compact_gold_increment") or 0
        )
    return {
        "subquery_purposes": dict(sorted(purposes.items())),
        "adaptation_strategies": dict(sorted(adaptations.items())),
        "compact_gold_increment": compact_gold_increment,
    }


def _normalized_query(value: str) -> str:
    return " ".join(value.casefold().split())


def _aggregate_judgement(cases: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "retrieved_gold_count",
        "gold_judged_highly_relevant",
        "gold_judged_partially_relevant",
        "gold_judged_weakly_relevant",
        "gold_judged_irrelevant",
        "gold_judged_insufficient_evidence",
        "gold_false_negative_count",
    )
    totals = {
        key: sum(int(case.get("judgement", {}).get(key) or 0) for case in cases)
        for key in keys
    }
    retained = (
        totals["gold_judged_highly_relevant"]
        + totals["gold_judged_partially_relevant"]
    )
    denominator = totals["retrieved_gold_count"]
    return {
        **totals,
        "gold_retention_after_judgement": (
            retained / denominator if denominator else 0.0
        ),
        "gold_false_negative_rate": (
            totals["gold_false_negative_count"] / denominator
            if denominator
            else 0.0
        ),
    }


def _aggregate_reranking(cases: list[dict[str, Any]]) -> dict[str, Any]:
    ranks = [
        int(rank)
        for case in cases
        for rank in case.get("reranking", {}).get("gold_ranks", [])
    ]
    eligible = sum(
        int(case.get("reranking", {}).get("eligible_gold_count") or 0)
        for case in cases
    )
    outside = sum(
        int(case.get("reranking", {}).get("gold_outside_top_20") or 0)
        for case in cases
    )
    return {
        "eligible_gold_count": eligible,
        "gold_in_top_5": sum(rank <= 5 for rank in ranks),
        "gold_in_top_10": sum(rank <= 10 for rank in ranks),
        "gold_in_top_20": sum(rank <= 20 for rank in ranks),
        "gold_outside_top_20": outside,
        "outside_top_20_rate": outside / eligible if eligible else 0.0,
        "average_gold_rank": statistics.mean(ranks) if ranks else None,
        "median_gold_rank": statistics.median(ranks) if ranks else None,
    }


def _aggregate_sources(
    cases: list[dict[str, Any]],
    gold_count: int,
) -> dict[str, Any]:
    sources: dict[str, dict[str, float | int]] = {}
    overlaps: dict[str, dict[str, int]] = {}
    for case in cases:
        contribution = case.get("source_contribution", {})
        for source, values in contribution.get("sources", {}).items():
            target = sources.setdefault(source, _empty_source_stats())
            for key in target:
                target[key] += values.get(key, 0)
        for pair, values in contribution.get("overlap", {}).items():
            target_overlap = overlaps.setdefault(
                pair,
                {"candidate_count": 0, "gold_hit_count": 0},
            )
            target_overlap["candidate_count"] += int(values["candidate_count"])
            target_overlap["gold_hit_count"] += int(values["gold_hit_count"])
    for values in sources.values():
        values["gold_recall_contribution"] = (
            values["unique_gold_hit_count"] / gold_count if gold_count else 0.0
        )
    requests = sum(int(item["request_count"]) for item in sources.values())
    errors = sum(int(item["error_count"]) for item in sources.values())
    return {
        "sources": sources,
        "overlap": overlaps,
        "source_error_rate": errors / requests if requests else 0.0,
    }


def _candidate_recall(
    snapshot: StageCandidateSnapshot,
    gold_papers: list[EvalGoldPaper],
) -> float | None:
    if snapshot.status != "completed":
        return None
    gold_count = evaluable_gold_count(gold_papers)
    if not gold_count:
        return 0.0
    return len(matched_paper_ids(snapshot.candidates, gold_papers)) / gold_count


def _matches(
    candidates: list[DiagnosticCandidate],
    gold: EvalGoldPaper,
) -> list[DiagnosticCandidate]:
    return [candidate for candidate in candidates if _candidate_matches(candidate, gold)]


def _candidate_matches(candidate: Any, gold: EvalGoldPaper) -> bool:
    return bool(matched_paper_ids([candidate], [gold]))


def _matching_candidate(
    candidates: list[DiagnosticCandidate],
    ranked: Any,
) -> DiagnosticCandidate | None:
    for candidate in candidates:
        if matched_paper_ids([candidate], [ranked.paper]):
            return candidate
    return None


def _snapshot_by_name(
    snapshots: list[StageCandidateSnapshot],
    name: str,
) -> StageCandidateSnapshot | None:
    return next((item for item in snapshots if item.stage == name), None)


def _latest_snapshot(
    snapshots: dict[str, StageCandidateSnapshot],
    names: tuple[str, ...],
) -> StageCandidateSnapshot | None:
    return next(
        (
            snapshots[name]
            for name in names
            if name in snapshots and snapshots[name].status == "completed"
        ),
        None,
    )


def _latest_match(
    stage_matches: dict[str, list[DiagnosticCandidate]],
    names: tuple[str, ...],
) -> DiagnosticCandidate | None:
    return next(
        (
            stage_matches[name][0]
            for name in names
            if stage_matches.get(name)
        ),
        None,
    )


def _first_rank(candidates: list[DiagnosticCandidate]) -> int | None:
    return next((item.rank for item in candidates if item.rank is not None), None)


def _title_seen_without_identifier_match(
    gold: EvalGoldPaper,
    snapshots: dict[str, StageCandidateSnapshot],
) -> bool:
    if not gold.title:
        return False
    normalized_gold = normalize_title(gold.title)
    return any(
        normalize_title(candidate.title) == normalized_gold
        and (
            gold.year is None
            or candidate.year is None
            or abs(gold.year - candidate.year) <= 1
        )
        for stage in RETRIEVAL_STAGES
        for candidate in snapshots.get(
            stage,
            StageCandidateSnapshot(stage=stage, status="skipped"),
        ).candidates
    )


def _all_sources_failed(output: SearchServiceOutput) -> bool:
    selected = set(output.search_plan.selected_sources)
    relevant = [item for item in output.source_stats if item.source in selected]
    if not relevant:
        return False
    successful_sources = {
        item.source for item in relevant if item.error_message is None
    }
    observed_sources = {item.source for item in relevant}
    return selected.issubset(observed_sources) and not successful_sources


def _normalize_source(source: str) -> str:
    normalized = source.strip().casefold().replace(" ", "_")
    aliases = {
        "semantic": "semantic_scholar",
        "semanticscholar": "semantic_scholar",
        "refchain": "openalex",
    }
    return aliases.get(normalized, normalized)


def _empty_source_stats() -> dict[str, float | int]:
    return {
        "request_count": 0,
        "success_count": 0,
        "error_count": 0,
        "returned_candidate_count": 0,
        "unique_candidate_count": 0,
        "gold_hit_count": 0,
        "unique_gold_hit_count": 0,
        "gold_recall_contribution": 0.0,
        "latency_seconds": 0.0,
    }


def _average_optional(values: list[Any]) -> float | None:
    numeric = [float(value) for value in values if value is not None]
    return statistics.mean(numeric) if numeric else None


def _stable_strings(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_source(str(value))
        if not normalized or normalized in seen:
            continue
        result.append(normalized)
        seen.add(normalized)
    return result

"""Offline SearchService evaluator using injected fixtures."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from scholar_agent.agents.refchain import ReferenceFetcher
from scholar_agent.core.evaluation_schemas import (
    EvalGroupName,
    EvalGroupResult,
    EvalMetricSet,
    EvalQuery,
    EvalQueryResult,
    EvalSuiteResult,
)
from scholar_agent.evaluation.metrics import (
    candidate_count_metrics,
    canonical_paper_id,
    error_rate_metrics,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from scholar_agent.services.search_service import (
    RetrieverFn,
    SearchService,
    SearchServiceOutput,
)


DEFAULT_GROUPS: tuple[EvalGroupName, ...] = (
    "baseline",
    "query_evolution",
    "refchain",
)


class OfflineSearchEvaluator:
    """Run SearchService against offline fixtures and compute metrics."""

    def __init__(
        self,
        *,
        retriever: RetrieverFn,
        reference_fetcher: ReferenceFetcher | None = None,
        max_workers: int = 4,
        groups: Sequence[EvalGroupName] = DEFAULT_GROUPS,
    ) -> None:
        self._retriever = retriever
        self._reference_fetcher = reference_fetcher or _empty_reference_fetcher
        self._max_workers = max_workers
        self._groups = tuple(groups)

    def evaluate(self, eval_queries: Iterable[EvalQuery]) -> EvalSuiteResult:
        """Evaluate all queries for all configured groups."""

        query_results: list[EvalQueryResult] = []
        for eval_query in eval_queries:
            group_results: dict[EvalGroupName, EvalGroupResult] = {}
            for group in self._groups:
                group_results[group] = self._evaluate_group(eval_query, group)
            query_results.append(
                EvalQueryResult(
                    query_id=eval_query.query_id,
                    query=eval_query.query,
                    group_results=group_results,
                )
            )

        return EvalSuiteResult(
            query_results=query_results,
            aggregate_metrics=_aggregate_metrics(query_results, self._groups),
        )

    def _evaluate_group(
        self,
        eval_query: EvalQuery,
        group: EvalGroupName,
    ) -> EvalGroupResult:
        top_k = max(eval_query.top_k_values or [20])
        try:
            output = SearchService(
                retriever=self._retriever,
                reference_fetcher=self._reference_fetcher,
                max_workers=self._max_workers,
            ).run_search(
                eval_query.query,
                top_k=top_k,
                run_profile=eval_query.run_profile,
                enable_query_evolution=group in {"query_evolution", "refchain"},
                enable_refchain=group == "refchain",
                current_year=eval_query.current_year,
            )
        except Exception as exc:  # noqa: BLE001 - record failed case
            return _failed_group_result(eval_query, group, exc)

        metrics = _score_output(eval_query, output)
        ranked_ids = [
            paper_id
            for paper_id in (canonical_paper_id(item) for item in output.ranked_papers)
            if paper_id is not None
        ]
        return EvalGroupResult(
            query_id=eval_query.query_id,
            group=group,
            metrics=metrics,
            ranked_paper_ids=ranked_ids,
            warnings=list(output.warnings),
            source_stats=[_model_to_dict(item) for item in output.source_stats],
            raw_count=output.raw_count,
            deduplicated_count=output.deduplicated_count,
            latency_seconds=output.latency_seconds,
        )


def evaluate_search_service_offline(
    eval_queries: Iterable[EvalQuery],
    *,
    retriever: RetrieverFn,
    reference_fetcher: ReferenceFetcher | None = None,
    max_workers: int = 4,
    groups: Sequence[EvalGroupName] = DEFAULT_GROUPS,
) -> EvalSuiteResult:
    """Convenience wrapper for offline SearchService evaluation."""

    return OfflineSearchEvaluator(
        retriever=retriever,
        reference_fetcher=reference_fetcher,
        max_workers=max_workers,
        groups=groups,
    ).evaluate(eval_queries)


def _score_output(eval_query: EvalQuery, output: SearchServiceOutput) -> EvalMetricSet:
    top_k_values = _normalize_top_k_values(eval_query.top_k_values)
    ranked = output.ranked_papers
    gold = eval_query.gold_papers
    count_metrics = candidate_count_metrics(
        output.raw_count,
        output.deduplicated_count,
        ranked_count=len(ranked),
        source_stats=output.source_stats,
    )
    error_metrics = error_rate_metrics(
        output.source_stats,
        output.warnings,
        failed_case_count=0,
        total_case_count=1,
    )

    return EvalMetricSet(
        recall_at_k={k: recall_at_k(ranked, gold, k) for k in top_k_values},
        precision_at_k={k: precision_at_k(ranked, gold, k) for k in top_k_values},
        ndcg_at_k={k: ndcg_at_k(ranked, gold, k) for k in top_k_values},
        mrr=mrr(ranked, gold),
        raw_count=count_metrics["raw_count"],
        deduplicated_count=count_metrics["deduplicated_count"],
        ranked_count=count_metrics["ranked_count"],
        duplicate_count=count_metrics["duplicate_count"],
        duplicate_ratio=count_metrics["duplicate_ratio"],
        per_source_returned_count=count_metrics["per_source_returned_count"],
        source_call_count=error_metrics["source_call_count"],
        source_error_count=error_metrics["source_error_count"],
        source_error_rate=error_metrics["source_error_rate"],
        warning_count=error_metrics["warning_count"],
        query_warning_rate=error_metrics["query_warning_rate"],
        failed_case_count=error_metrics["failed_case_count"],
        failed_case_rate=error_metrics["failed_case_rate"],
    )


def _failed_group_result(
    eval_query: EvalQuery,
    group: EvalGroupName,
    exc: Exception,
) -> EvalGroupResult:
    error_message = f"{type(exc).__name__}: {exc}"
    metrics = EvalMetricSet(
        recall_at_k={k: 0.0 for k in _normalize_top_k_values(eval_query.top_k_values)},
        precision_at_k={
            k: 0.0 for k in _normalize_top_k_values(eval_query.top_k_values)
        },
        ndcg_at_k={k: 0.0 for k in _normalize_top_k_values(eval_query.top_k_values)},
        failed_case_count=1,
        failed_case_rate=1.0,
        warning_count=1,
        query_warning_rate=1.0,
    )
    return EvalGroupResult(
        query_id=eval_query.query_id,
        group=group,
        metrics=metrics,
        warnings=[error_message],
        failed=True,
        error_message=error_message,
    )


def _aggregate_metrics(
    query_results: list[EvalQueryResult],
    groups: Sequence[EvalGroupName],
) -> dict[EvalGroupName, EvalMetricSet]:
    aggregate: dict[EvalGroupName, EvalMetricSet] = {}
    for group in groups:
        metrics = [
            query_result.group_results[group].metrics
            for query_result in query_results
            if group in query_result.group_results
        ]
        aggregate[group] = _aggregate_metric_set(metrics)
    return aggregate


def _aggregate_metric_set(metrics: list[EvalMetricSet]) -> EvalMetricSet:
    if not metrics:
        return EvalMetricSet()

    case_count = len(metrics)
    raw_count = sum(item.raw_count for item in metrics)
    deduplicated_count = sum(item.deduplicated_count for item in metrics)
    duplicate_count = sum(item.duplicate_count for item in metrics)
    source_call_count = sum(item.source_call_count for item in metrics)
    source_error_count = sum(item.source_error_count for item in metrics)
    warning_count = sum(item.warning_count for item in metrics)
    failed_case_count = sum(item.failed_case_count for item in metrics)

    return EvalMetricSet(
        recall_at_k=_average_metric_maps([item.recall_at_k for item in metrics]),
        precision_at_k=_average_metric_maps([item.precision_at_k for item in metrics]),
        ndcg_at_k=_average_metric_maps([item.ndcg_at_k for item in metrics]),
        mrr=sum(item.mrr for item in metrics) / case_count,
        raw_count=raw_count,
        deduplicated_count=deduplicated_count,
        ranked_count=sum(item.ranked_count for item in metrics),
        duplicate_count=duplicate_count,
        duplicate_ratio=duplicate_count / raw_count if raw_count else 0.0,
        per_source_returned_count=_sum_source_counts(metrics),
        source_call_count=source_call_count,
        source_error_count=source_error_count,
        source_error_rate=source_error_count / source_call_count
        if source_call_count
        else 0.0,
        warning_count=warning_count,
        query_warning_rate=sum(
            1 for item in metrics if item.warning_count > 0
        )
        / case_count,
        failed_case_count=failed_case_count,
        failed_case_rate=failed_case_count / case_count,
    )


def _average_metric_maps(metric_maps: list[dict[int, float]]) -> dict[int, float]:
    keys = sorted({key for metric_map in metric_maps for key in metric_map})
    averaged: dict[int, float] = {}
    for key in keys:
        values = [metric_map.get(key, 0.0) for metric_map in metric_maps]
        averaged[key] = sum(values) / len(values) if values else 0.0
    return averaged


def _sum_source_counts(metrics: list[EvalMetricSet]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for metric_set in metrics:
        for source, count in metric_set.per_source_returned_count.items():
            totals[source] = totals.get(source, 0) + count
    return totals


def _normalize_top_k_values(top_k_values: Sequence[int]) -> list[int]:
    values = sorted({int(value) for value in top_k_values if int(value) > 0})
    return values or [5, 10, 20]


def _model_to_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return dict(item)


def _empty_reference_fetcher(*_: Any, **__: Any) -> list[Any]:
    return []

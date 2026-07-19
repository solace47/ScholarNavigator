#!/usr/bin/env python3
"""串行消费离线快照计划，并在保守上限内补齐缺失条目。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (REPO_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from scholar_agent.connectors import (  # noqa: E402
    fetch_openalex_references_detailed,
    search_arxiv_detailed,
    search_openalex_detailed,
    search_pubmed_detailed,
    search_semantic_scholar_detailed,
)
from scholar_agent.connectors.schemas import ConnectorSearchResult  # noqa: E402
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers  # noqa: E402
from scholar_agent.evaluation.snapshots import SnapshotRuntime, SnapshotStore  # noqa: E402
from scholar_agent.evaluation.snapshots.planning import (  # noqa: E402
    SnapshotCollectionLimits,
    atomic_write_json,
    plan_round_root,
    write_coverage_artifacts,
)
from scholar_agent.evaluation.snapshots.schemas import (  # noqa: E402
    SnapshotPlanEntry,
    SnapshotPlanRound,
)
from scholar_agent.evaluation.snapshots.store import SnapshotMissingError  # noqa: E402


DEFAULT_LIMITS = SnapshotCollectionLimits()
DEFAULT_MAX_NEW_REQUESTS = DEFAULT_LIMITS.max_new_requests
DEFAULT_MAX_NEW_FAILED_ENTRIES = DEFAULT_LIMITS.max_new_failed_entries
DEFAULT_MAX_COLLECTION_SECONDS = DEFAULT_LIMITS.max_collection_seconds
DEFAULT_SOURCE_FAILURE_LIMIT = DEFAULT_LIMITS.source_failure_limit


def collect_plan(
    plan_path: Path | str,
    snapshot_dir: Path | str,
    *,
    max_new_requests: int = DEFAULT_MAX_NEW_REQUESTS,
    max_new_failed_entries: int = DEFAULT_MAX_NEW_FAILED_ENTRIES,
    max_collection_seconds: float = DEFAULT_MAX_COLLECTION_SECONDS,
    retry_failed_snapshots: bool = False,
    source_failure_limit: int = DEFAULT_SOURCE_FAILURE_LIMIT,
    searchers: dict[str, Callable[[str, int], ConnectorSearchResult]] | None = None,
    reference_fetcher: Callable[[Paper, int], ConnectorSearchResult] = (
        fetch_openalex_references_detailed
    ),
    clock: Callable[[], float] = time.monotonic,
    cancel_check: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    path = Path(plan_path).expanduser().resolve()
    plan = SnapshotPlanRound.model_validate_json(path.read_text(encoding="utf-8"))
    store = SnapshotStore(snapshot_dir)
    query_evolution_policy = _plan_query_evolution_policy(plan, store)
    runtime = SnapshotRuntime(
        store,
        mode="record-missing",
        group_name=plan.group,
        retry_failed_snapshots=retry_failed_snapshots,
        plan_round=plan.round_index,
        query_evolution_policy=query_evolution_policy,
    )
    registry = searchers or {
        "arxiv": search_arxiv_detailed,
        "openalex": search_openalex_detailed,
        "semantic_scholar": search_semantic_scholar_detailed,
        "pubmed": search_pubmed_detailed,
    }
    started = clock()
    request_count = 0
    failed_count = 0
    collected_count = 0
    skipped_present_count = 0
    source_failures: dict[str, int] = {}
    blocked_sources: set[str] = set()
    completed_keys = _prior_completed_keys(
        plan_round_root(snapshot_dir, plan.group, plan.round_index)
        / "collection_result.json"
    )
    stop_reason: str | None = None
    round_root = plan_round_root(snapshot_dir, plan.group, plan.round_index)
    result_path = round_root / "collection_result.json"
    entries = sorted(plan.entries, key=lambda entry: (entry.priority, entry.key))
    runtime.begin_case(f"collection:{plan.group}:{plan.round_index}")

    for entry in entries:
        if cancel_check():
            stop_reason = "snapshot_collection_cancelled"
            break
        existing_status = _existing_status(store, entry)
        if existing_status == "success" or (
            existing_status == "failed" and not retry_failed_snapshots
        ):
            skipped_present_count += 1
            continue
        if entry.source in blocked_sources:
            continue
        if request_count >= max_new_requests:
            stop_reason = "snapshot_collection_request_limit"
            break
        if failed_count >= max_new_failed_entries:
            stop_reason = "snapshot_collection_failure_limit"
            break
        if clock() - started >= max_collection_seconds:
            stop_reason = "snapshot_collection_time_limit"
            break

        result = _collect_entry(
            runtime,
            entry,
            registry,
            reference_fetcher,
        )
        diagnostics = result.recorded_diagnostics or result.diagnostics
        request_count += diagnostics.request_count
        collected_count += 1
        if entry.key not in completed_keys:
            completed_keys.append(entry.key)
        if result.error_message:
            failed_count += 1
            source_failures[entry.source] = source_failures.get(entry.source, 0) + 1
            if source_failures[entry.source] >= source_failure_limit:
                blocked_sources.add(entry.source)
        atomic_write_json(
            result_path,
            _collection_result(
                plan,
                request_count=request_count,
                failed_count=failed_count,
                collected_count=collected_count,
                skipped_present_count=skipped_present_count,
                completed_keys=completed_keys,
                blocked_sources=blocked_sources,
                source_failures=source_failures,
                stop_reason=stop_reason,
                elapsed_seconds=clock() - started,
            ),
        )

    remaining_sources = {
        entry.source
        for entry in entries
        if _existing_status(store, entry) is None
    }
    if stop_reason is None and remaining_sources.intersection(blocked_sources):
        stop_reason = "snapshot_collection_source_cooldown"
    observation = runtime.finish_group(
        completed=stop_reason is None,
        stop_reason=stop_reason,
    )
    result = _collection_result(
        plan,
        request_count=request_count,
        failed_count=failed_count,
        collected_count=collected_count,
        skipped_present_count=skipped_present_count,
        completed_keys=completed_keys,
        blocked_sources=blocked_sources,
        source_failures=source_failures,
        stop_reason=stop_reason,
        elapsed_seconds=clock() - started,
    )
    result["coverage"] = observation.model_dump(mode="json")
    result["covered_success"] = observation.success_key_count
    result["covered_failed"] = observation.failed_key_count
    result["missing_entries"] = observation.missing_key_count
    atomic_write_json(result_path, result)
    write_coverage_artifacts(
        snapshot_dir,
        group=plan.group,
        round_index=plan.round_index,
    )
    return result


def _collect_entry(
    runtime: SnapshotRuntime,
    entry: SnapshotPlanEntry,
    registry: dict[str, Callable[[str, int], ConnectorSearchResult]],
    reference_fetcher: Callable[[Paper, int], ConnectorSearchResult],
) -> ConnectorSearchResult:
    if entry.entry_type == "retrieval":
        search = registry.get(entry.source)
        if search is None or entry.adapted_query is None or entry.adapter_policy is None:
            raise ValueError(f"snapshot_plan_entry_invalid:{entry.key}")
        return runtime.search(
            entry.source,
            entry.adapted_query,
            entry.limit,
            entry.adapter_policy,  # type: ignore[arg-type]
            search,
            stage=entry.stage,
            origin_subquery=entry.origin_subquery,
            generated_by=entry.generated_by,
            query_evolution_policy=entry.query_evolution_policy,
        )
    if entry.seed_identifier is None:
        raise ValueError(f"snapshot_plan_seed_missing:{entry.key}")
    return runtime.fetch_references(
        _seed_paper(entry.seed_identifier),
        entry.limit,
        reference_fetcher,
    )


def _seed_paper(identifier: str) -> Paper:
    prefix, _, value = identifier.partition(":")
    identifiers = (
        PaperIdentifiers(openalex_id=value)
        if prefix == "openalex"
        else PaperIdentifiers(doi=value) if prefix == "doi" else PaperIdentifiers()
    )
    return Paper(title=f"snapshot-seed:{prefix}", identifiers=identifiers)


def _plan_query_evolution_policy(
    plan: SnapshotPlanRound,
    store: SnapshotStore,
) -> str:
    policies = {
        entry.query_evolution_policy
        for entry in plan.entries
        if entry.query_evolution_policy is not None
    }
    if len(policies) > 1:
        raise ValueError("snapshot_plan_mixed_query_evolution_policy")
    if policies:
        return policies.pop()
    prior = store.read_manifest().groups.get(plan.group)
    if prior is not None and prior.query_evolution_policy is not None:
        return prior.query_evolution_policy
    return "off"


def _existing_status(store: SnapshotStore, entry: SnapshotPlanEntry) -> str | None:
    try:
        stored = (
            store.read_retrieval(entry.key)
            if entry.entry_type == "retrieval"
            else store.read_reference(entry.key)
        )
    except SnapshotMissingError:
        return None
    return stored.status


def _prior_completed_keys(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    values = payload.get("completed_keys") if isinstance(payload, dict) else None
    return [str(value) for value in values or []]


def _collection_result(
    plan: SnapshotPlanRound,
    *,
    request_count: int,
    failed_count: int,
    collected_count: int,
    skipped_present_count: int,
    completed_keys: list[str],
    blocked_sources: set[str],
    source_failures: dict[str, int],
    stop_reason: str | None,
    elapsed_seconds: float,
) -> dict[str, Any]:
    return {
        "group": plan.group,
        "round_index": plan.round_index,
        "request_count": request_count,
        "failed_entry_count": failed_count,
        "collected_entry_count": collected_count,
        "skipped_present_count": skipped_present_count,
        "completed_keys": list(completed_keys),
        "blocked_sources": sorted(blocked_sources),
        "source_failure_counts": dict(sorted(source_failures.items())),
        "stop_reason": stop_reason,
        "elapsed_seconds": elapsed_seconds,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按离线计划有界补齐 Benchmark 快照。")
    parser.add_argument("--collect-plan", required=True)
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--max-new-requests", type=int, default=DEFAULT_MAX_NEW_REQUESTS)
    parser.add_argument(
        "--max-new-failed-entries",
        type=int,
        default=DEFAULT_MAX_NEW_FAILED_ENTRIES,
    )
    parser.add_argument(
        "--max-collection-seconds",
        type=float,
        default=DEFAULT_MAX_COLLECTION_SECONDS,
    )
    parser.add_argument("--retry-failed-snapshots", action="store_true")
    parser.add_argument(
        "--source-failure-limit",
        type=int,
        default=DEFAULT_SOURCE_FAILURE_LIMIT,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = collect_plan(
            args.collect_plan,
            args.snapshot_dir,
            max_new_requests=args.max_new_requests,
            max_new_failed_entries=args.max_new_failed_entries,
            max_collection_seconds=args.max_collection_seconds,
            retry_failed_snapshots=args.retry_failed_snapshots,
            source_failure_limit=args.source_failure_limit,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("stop_reason") is None else 2


if __name__ == "__main__":
    raise SystemExit(main())

"""在连接器边界集中执行 Benchmark 响应的 Record/Replay。"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from threading import RLock
from typing import Literal

from scholar_agent.agents.retriever import (
    RetrievalOutput,
    clear_retrieval_cache,
    clear_source_cooldowns,
    retrieve_papers,
)
from scholar_agent.connectors.schemas import ConnectorSearchResult
from scholar_agent.core.diagnostics_schemas import ConnectorDiagnostics
from scholar_agent.core.paper_schemas import Paper
from scholar_agent.evaluation.snapshots.schemas import (
    ReferenceSnapshotEntry,
    RetrievalSnapshotEntry,
    SnapshotCostReport,
    SnapshotGroupObservation,
    SnapshotPlanEntry,
)
from scholar_agent.evaluation.snapshots.store import (
    SnapshotError,
    SnapshotMissingError,
    SnapshotStore,
    canonical_seed_identifier,
    connector_version,
    entry_content_hash,
    reference_snapshot_key,
    retrieval_snapshot_key,
    utc_now,
)
from scholar_agent.retrieval.query_adapter import QueryAdapterPolicy


RetrievalMode = Literal["live", "record", "replay", "record-missing", "plan"]
LiveSearch = Callable[[str, int], ConnectorSearchResult]
LiveReferenceFetcher = Callable[[Paper, int], list[Paper] | ConnectorSearchResult]


def _stable_append(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _sanitize_text(value: str | None) -> str | None:
    if value is None:
        return None
    sanitized = re.sub(
        r"(?i)(authorization|api[_-]?key|token)(\s*[:=]\s*)[^\s&,;]+",
        r"\1\2[REDACTED]",
        value,
    )
    return sanitized[:2000]


class SnapshotRuntime:
    """管理一次 Benchmark 进程内的快照读取、写入和双口径成本。"""

    def __init__(
        self,
        store: SnapshotStore,
        *,
        mode: RetrievalMode,
        group_name: str,
        retry_failed_snapshots: bool = False,
        overwrite_snapshots: bool = False,
        plan_round: int = 1,
        query_evolution_policy: Literal[
            "off", "seed_expansion", "coverage_gap"
        ] = "off",
        query_planning_policy: Literal[
            "current_rules", "facet_balanced", "llm_semantic"
        ] = "current_rules",
        query_planner_version: str | None = None,
    ) -> None:
        if mode == "live":
            raise ValueError("SnapshotRuntime does not handle live mode")
        self.store = store
        self.mode = mode
        self.group_name = group_name
        self.retry_failed_snapshots = retry_failed_snapshots
        self.overwrite_snapshots = overwrite_snapshots
        self.plan_round = plan_round
        self.query_evolution_policy = query_evolution_policy
        self.query_planning_policy = query_planning_policy
        self.query_planner_version = query_planner_version
        self._lock = RLock()
        self._case = SnapshotCostReport(mode=mode)
        self._case_id = ""
        self._plan_entries: list[SnapshotPlanEntry] = []
        self._group_write_count = 0
        manifest = store.read_manifest()
        existing = manifest.groups.get(group_name)
        self._prior_group = existing
        # Plan 每轮都重新走真实流水线，required key 只来自本轮实际路径，
        # 避免保留因上游结果变化而不再可达的动态键。
        self._group_retrieval = (
            []
            if mode == "plan"
            else list(existing.retrieval_keys) if existing else []
        )
        self._group_references = (
            []
            if mode == "plan"
            else list(existing.reference_keys) if existing else []
        )
        self._group_missing_retrieval = (
            []
            if mode in {"replay", "plan"}
            else list(existing.missing_retrieval_keys) if existing else []
        )
        self._group_missing_references = (
            []
            if mode in {"replay", "plan"}
            else list(existing.missing_reference_keys) if existing else []
        )

    def begin_case(self, case_id: str = "") -> None:
        with self._lock:
            self._case = SnapshotCostReport(mode=self.mode)
            self._case_id = case_id

    def plan_entries(self) -> list[SnapshotPlanEntry]:
        with self._lock:
            return [entry.model_copy(deep=True) for entry in self._plan_entries]

    def finish_case(self) -> SnapshotCostReport:
        with self._lock:
            return self._case.model_copy(deep=True)

    def assert_case_complete(self) -> None:
        with self._lock:
            failures = [
                *self._case.fatal_errors,
                *(
                    f"snapshot_missing:retrieval:{key}"
                    for key in self._case.missing_retrieval_keys
                ),
                *(
                    f"snapshot_missing:references:{key}"
                    for key in self._case.missing_reference_keys
                ),
            ]
        if failures:
            raise SnapshotError(";".join(failures))

    def budget_elapsed_seconds(self) -> float:
        """返回记录成本用于 Replay 的预算路径判断，不代表实际等待。"""

        with self._lock:
            return self._case.recorded_latency_seconds

    def finish_group(
        self,
        *,
        completed: bool,
        stop_reason: str | None = None,
    ) -> SnapshotGroupObservation:
        with self._lock:
            run_completed = bool(
                completed
                and not self._group_missing_retrieval
                and not self._group_missing_references
            )
            prior_collection_completed = bool(
                self._prior_group
                and (
                    self._prior_group.collection_completed
                    or self._prior_group.completed
                )
            )
            collection_completed = (
                prior_collection_completed
                if self.mode == "replay"
                else run_completed
            )
            replay_verified = (
                run_completed
                if self.mode == "replay"
                else bool(
                    self.mode == "record-missing"
                    and self._group_write_count == 0
                    and self._prior_group
                    and self._prior_group.replay_verified
                )
            )
            success_count, failed_count = self._coverage_counts()
            required_count = len(self._group_retrieval) + len(self._group_references)
            missing_count = len(self._group_missing_retrieval) + len(
                self._group_missing_references
            )
            replay_ready = bool(required_count and missing_count == 0)
            observation = SnapshotGroupObservation(
                query_planning_policy=self.query_planning_policy,
                query_planner_version=self.query_planner_version,
                query_evolution_policy=self.query_evolution_policy,
                retrieval_keys=list(self._group_retrieval),
                reference_keys=list(self._group_references),
                missing_retrieval_keys=list(self._group_missing_retrieval),
                missing_reference_keys=list(self._group_missing_references),
                collection_started=True,
                collection_completed=collection_completed,
                replay_ready=replay_ready,
                replay_verified=replay_verified,
                required_key_count=required_count,
                success_key_count=success_count,
                failed_key_count=failed_count,
                missing_key_count=missing_count,
                last_plan_round=(
                    self.plan_round
                    if self.mode == "plan"
                    else (self._prior_group.last_plan_round if self._prior_group else 0)
                ),
                plan_rounds=(
                    max(
                        self.plan_round,
                        self._prior_group.plan_rounds if self._prior_group else 0,
                    )
                    if self.mode == "plan"
                    else (self._prior_group.plan_rounds if self._prior_group else 0)
                ),
                stop_reason=(stop_reason or (None if replay_ready else "snapshot_missing")),
                completed=(replay_verified if self.mode == "replay" else run_completed),
                updated_at=utc_now(),
            )
        self.store.update_group(self.group_name, observation)
        return observation

    def search(
        self,
        source: str,
        adapted_query: str,
        limit: int,
        adapter_policy: QueryAdapterPolicy,
        live_search: LiveSearch,
        *,
        stage: str = "initial_retrieval",
        origin_subquery: str | None = None,
        generated_by: Literal[
            "initial_retrieval", "query_evolution", "refchain"
        ] = "initial_retrieval",
        query_evolution_policy: Literal[
            "off", "seed_expansion", "coverage_gap"
        ] | None = None,
        query_planning_policy: Literal[
            "current_rules", "facet_balanced", "llm_semantic"
        ] | None = None,
        query_planner_version: str | None = None,
    ) -> ConnectorSearchResult:
        effective_planning_policy = (
            query_planning_policy or self.query_planning_policy
        )
        effective_planner_version = (
            query_planner_version or self.query_planner_version
        )
        version = connector_version(source)
        key, normalized_query = retrieval_snapshot_key(
            source=source,
            adapted_query=adapted_query,
            limit=limit,
            adapter_policy=adapter_policy,
            connector_version=version,
            query_evolution_policy=query_evolution_policy,
            query_planning_policy=effective_planning_policy,
            query_planner_version=effective_planner_version,
        )
        self._observe("retrieval", key)
        try:
            existing = self._read_optional("retrieval", key)
        except SnapshotError as exc:
            self._mark_fatal(str(exc))
            raise
        if self.mode == "plan":
            self._record_plan_entry(
                SnapshotPlanEntry(
                    key=key,
                    entry_type="retrieval",
                    source=source,
                    adapted_query=adapted_query,
                    limit=limit,
                    adapter_policy=adapter_policy,
                    connector_version=version,
                    required_by_group=self.group_name,
                    case_id=self._case_id,
                    stage=stage,
                    origin_subquery=origin_subquery,
                    generated_by=generated_by,
                    query_evolution_policy=query_evolution_policy,
                    query_planning_policy=effective_planning_policy,
                    query_planner_version=effective_planner_version,
                    dependency_keys=self._dependency_keys(key, generated_by),
                    priority=1 if source == "arxiv" else 2,
                    already_present=existing is not None,
                    existing_status=existing.status if existing is not None else None,
                )
            )
            if existing is None:
                self._mark_missing("retrieval", key)
                return ConnectorSearchResult(
                    error_message=f"snapshot_plan_missing:retrieval:{key}",
                    warnings=[f"snapshot_plan_missing:retrieval:{key}"],
                    snapshot_provenance="snapshot_plan",
                    snapshot_key=key,
                )
            return self._replay_retrieval(existing)
        if self.mode == "replay":
            if existing is None:
                self._mark_missing("retrieval", key)
                raise SnapshotMissingError(f"snapshot_missing:retrieval:{key}")
            return self._replay_retrieval(existing)
        if self.mode == "record-missing" and existing is not None:
            if existing.status == "success" or not self.retry_failed_snapshots:
                return self._replay_retrieval(existing)

        started = time.perf_counter()
        try:
            result = live_search(adapted_query, limit)
        except Exception as exc:  # noqa: BLE001 - final failures must be recordable
            elapsed = time.perf_counter() - started
            result = ConnectorSearchResult(
                error_message=_sanitize_text(str(exc)),
                warnings=[f"connector_failed:{source}"],
                latency_seconds=elapsed,
                diagnostics=ConnectorDiagnostics(
                    error_count=1,
                    latency_seconds=elapsed,
                ),
            )
        elapsed = max(result.latency_seconds, time.perf_counter() - started)
        entry = RetrievalSnapshotEntry(
            key=key,
            source=source,
            adapted_query=adapted_query,
            normalized_query=normalized_query,
            limit=limit,
            adapter_policy=adapter_policy,
            connector_version=version,
            status="failed" if result.error_message else "success",
            papers=[paper.model_copy(deep=True) for paper in result.papers],
            error_message=_sanitize_text(result.error_message),
            warnings=[_sanitize_text(item) or "" for item in result.warnings],
            diagnostics=result.diagnostics.model_copy(deep=True),
            recorded_latency_seconds=elapsed,
            recorded_at=utc_now(),
            content_hash="0" * 64,
        )
        entry = entry.model_copy(update={"content_hash": entry_content_hash(entry)})
        overwrite = self.overwrite_snapshots or bool(
            existing is not None
            and existing.status == "failed"
            and self.retry_failed_snapshots
        )
        try:
            wrote = self.store.write_retrieval(entry, overwrite=overwrite)
        except SnapshotError as exc:
            self._mark_fatal(str(exc))
            raise
        self._mark_available("retrieval", key)
        if wrote:
            self.store.invalidate_verified_groups(key)
        self._record_cost("retrieval", entry.diagnostics, elapsed, wrote=wrote)
        return result.model_copy(
            update={
                "snapshot_provenance": "snapshot_record",
                "snapshot_key": key,
                "snapshot_hit": False,
                "recorded_diagnostics": entry.diagnostics,
                "recorded_latency_seconds": elapsed,
            },
            deep=True,
        )

    def fetch_references(
        self,
        paper: Paper,
        limit: int,
        live_fetcher: LiveReferenceFetcher,
    ) -> ConnectorSearchResult:
        seed_identifier = canonical_seed_identifier(paper)
        if seed_identifier is None:
            return ConnectorSearchResult(
                error_message="snapshot_seed_missing_supported_identifier",
                diagnostics=ConnectorDiagnostics(error_count=1),
            )
        if self.mode == "plan":
            unresolved_retrieval = [
                entry.key
                for entry in self.plan_entries()
                if entry.entry_type == "retrieval" and not entry.already_present
            ]
            if unresolved_retrieval:
                return ConnectorSearchResult(
                    error_message="snapshot_plan_dependency_missing:refchain",
                    warnings=["snapshot_plan_dependency_missing:refchain"],
                    snapshot_provenance="snapshot_plan",
                )
        version = connector_version("openalex_references")
        key = reference_snapshot_key(
            seed_identifier=seed_identifier,
            limit=limit,
            connector_version=version,
        )
        self._observe("references", key)
        try:
            existing = self._read_optional("references", key)
        except SnapshotError as exc:
            self._mark_fatal(str(exc))
            raise
        if self.mode == "plan":
            self._record_plan_entry(
                SnapshotPlanEntry(
                    key=key,
                    entry_type="reference",
                    source="openalex",
                    seed_identifier=seed_identifier,
                    limit=limit,
                    connector_version=version,
                    required_by_group=self.group_name,
                    case_id=self._case_id,
                    stage="refchain",
                    origin_subquery=None,
                    generated_by="refchain",
                    dependency_keys=self._dependency_keys(key, "refchain"),
                    priority=3,
                    already_present=existing is not None,
                    existing_status=existing.status if existing is not None else None,
                )
            )
            if existing is None:
                self._mark_missing("references", key)
                return ConnectorSearchResult(
                    error_message=f"snapshot_plan_missing:references:{key}",
                    warnings=[f"snapshot_plan_missing:references:{key}"],
                    snapshot_provenance="snapshot_plan",
                    snapshot_key=key,
                )
            return self._replay_reference(existing)
        if self.mode == "replay":
            if existing is None:
                self._mark_missing("references", key)
                raise SnapshotMissingError(f"snapshot_missing:references:{key}")
            return self._replay_reference(existing)
        if self.mode == "record-missing" and existing is not None:
            if existing.status == "success" or not self.retry_failed_snapshots:
                return self._replay_reference(existing)

        started = time.perf_counter()
        try:
            fetched = live_fetcher(paper, limit)
            result = (
                fetched
                if isinstance(fetched, ConnectorSearchResult)
                else ConnectorSearchResult(papers=list(fetched))
            )
        except Exception as exc:  # noqa: BLE001 - final failures must be recordable
            elapsed = time.perf_counter() - started
            result = ConnectorSearchResult(
                error_message=_sanitize_text(str(exc)),
                warnings=["connector_failed:openalex_references"],
                latency_seconds=elapsed,
                diagnostics=ConnectorDiagnostics(
                    error_count=1,
                    latency_seconds=elapsed,
                ),
            )
        elapsed = max(result.latency_seconds, time.perf_counter() - started)
        entry = ReferenceSnapshotEntry(
            key=key,
            seed_identifier=seed_identifier,
            limit=limit,
            connector_version=version,
            status="failed" if result.error_message else "success",
            papers=[paper.model_copy(deep=True) for paper in result.papers],
            error_message=_sanitize_text(result.error_message),
            warnings=[_sanitize_text(item) or "" for item in result.warnings],
            diagnostics=result.diagnostics.model_copy(deep=True),
            recorded_latency_seconds=elapsed,
            recorded_at=utc_now(),
            content_hash="0" * 64,
        )
        entry = entry.model_copy(update={"content_hash": entry_content_hash(entry)})
        overwrite = self.overwrite_snapshots or bool(
            existing is not None
            and existing.status == "failed"
            and self.retry_failed_snapshots
        )
        try:
            wrote = self.store.write_reference(entry, overwrite=overwrite)
        except SnapshotError as exc:
            self._mark_fatal(str(exc))
            raise
        self._mark_available("references", key)
        if wrote:
            self.store.invalidate_verified_groups(key)
        self._record_cost("references", entry.diagnostics, elapsed, wrote=wrote)
        return result.model_copy(
            update={
                "snapshot_provenance": "snapshot_record",
                "snapshot_key": key,
                "snapshot_hit": False,
                "recorded_diagnostics": entry.diagnostics,
                "recorded_latency_seconds": elapsed,
            },
            deep=True,
        )

    def _read_optional(
        self,
        kind: Literal["retrieval", "references"],
        key: str,
    ) -> RetrievalSnapshotEntry | ReferenceSnapshotEntry | None:
        try:
            return (
                self.store.read_retrieval(key)
                if kind == "retrieval"
                else self.store.read_reference(key)
            )
        except SnapshotMissingError:
            return None

    def _replay_retrieval(
        self,
        entry: RetrievalSnapshotEntry | ReferenceSnapshotEntry,
    ) -> ConnectorSearchResult:
        assert isinstance(entry, RetrievalSnapshotEntry)
        self._mark_available("retrieval", entry.key)
        self._snapshot_hit("retrieval", entry.diagnostics, entry.recorded_latency_seconds)
        return self._entry_result(entry)

    def _replay_reference(
        self,
        entry: RetrievalSnapshotEntry | ReferenceSnapshotEntry,
    ) -> ConnectorSearchResult:
        assert isinstance(entry, ReferenceSnapshotEntry)
        self._mark_available("references", entry.key)
        self._snapshot_hit("references", entry.diagnostics, entry.recorded_latency_seconds)
        return self._entry_result(entry)

    @staticmethod
    def _entry_result(
        entry: RetrievalSnapshotEntry | ReferenceSnapshotEntry,
    ) -> ConnectorSearchResult:
        return ConnectorSearchResult(
            papers=[paper.model_copy(deep=True) for paper in entry.papers],
            error_message=entry.error_message,
            warnings=list(entry.warnings),
            latency_seconds=0.0,
            diagnostics=ConnectorDiagnostics(),
            snapshot_provenance="snapshot_replay",
            snapshot_key=entry.key,
            snapshot_hit=True,
            recorded_diagnostics=entry.diagnostics.model_copy(deep=True),
            recorded_latency_seconds=entry.recorded_latency_seconds,
        )

    def _observe(self, kind: str, key: str) -> None:
        with self._lock:
            target = (
                self._group_retrieval if kind == "retrieval" else self._group_references
            )
            _stable_append(target, key)
            case_target = (
                self._case.observed_retrieval_keys
                if kind == "retrieval"
                else self._case.observed_reference_keys
            )
            _stable_append(case_target, key)

    def _mark_missing(self, kind: str, key: str) -> None:
        with self._lock:
            group_target = (
                self._group_missing_retrieval
                if kind == "retrieval"
                else self._group_missing_references
            )
            case_target = (
                self._case.missing_retrieval_keys
                if kind == "retrieval"
                else self._case.missing_reference_keys
            )
            _stable_append(group_target, key)
            _stable_append(case_target, key)

    def _mark_available(self, kind: str, key: str) -> None:
        with self._lock:
            target = (
                self._group_missing_retrieval
                if kind == "retrieval"
                else self._group_missing_references
            )
            if key in target:
                target.remove(key)

    def _mark_fatal(self, message: str) -> None:
        with self._lock:
            _stable_append(self._case.fatal_errors, message)

    def _record_plan_entry(self, entry: SnapshotPlanEntry) -> None:
        with self._lock:
            for index, existing in enumerate(self._plan_entries):
                if existing.key != entry.key or existing.case_id != entry.case_id:
                    continue
                dependencies = list(existing.dependency_keys)
                for key in entry.dependency_keys:
                    _stable_append(dependencies, key)
                self._plan_entries[index] = existing.model_copy(
                    update={"dependency_keys": dependencies}
                )
                return
            self._plan_entries.append(entry)

    def _dependency_keys(self, current_key: str, generated_by: str) -> list[str]:
        if generated_by == "initial_retrieval":
            return []
        return [
            key
            for key in self._case.observed_retrieval_keys
            if key != current_key and key not in self._case.missing_retrieval_keys
        ]

    def _coverage_counts(self) -> tuple[int, int]:
        success = 0
        failed = 0
        for key in self._group_retrieval:
            try:
                status = self.store.read_retrieval(key).status
            except SnapshotMissingError:
                continue
            success += status == "success"
            failed += status == "failed"
        for key in self._group_references:
            try:
                status = self.store.read_reference(key).status
            except SnapshotMissingError:
                continue
            success += status == "success"
            failed += status == "failed"
        return success, failed

    def _snapshot_hit(
        self,
        kind: str,
        diagnostics: ConnectorDiagnostics,
        latency_seconds: float,
    ) -> None:
        with self._lock:
            if kind == "retrieval":
                self._case.retrieval_snapshot_hits += 1
            else:
                self._case.reference_snapshot_hits += 1
            self._add_recorded_cost(kind, diagnostics, latency_seconds)

    def _record_cost(
        self,
        kind: str,
        diagnostics: ConnectorDiagnostics,
        latency_seconds: float,
        *,
        wrote: bool,
    ) -> None:
        with self._lock:
            if wrote:
                self._group_write_count += 1
                if kind == "retrieval":
                    self._case.retrieval_snapshot_writes += 1
                else:
                    self._case.reference_snapshot_writes += 1
            self._add_recorded_cost(kind, diagnostics, latency_seconds)

    def _add_recorded_cost(
        self,
        kind: str,
        diagnostics: ConnectorDiagnostics,
        latency_seconds: float,
    ) -> None:
        if kind == "retrieval":
            self._case.recorded_search_request_count += diagnostics.request_count
        else:
            self._case.recorded_reference_request_count += diagnostics.request_count
        self._case.recorded_retry_count += diagnostics.retry_count
        self._case.recorded_error_count += diagnostics.error_count
        self._case.recorded_rate_limit_wait_seconds += (
            diagnostics.rate_limit_wait_seconds
        )
        self._case.recorded_latency_seconds += latency_seconds


class SnapshotAwareRetriever:
    """保持 SearchService 检索算法不变，只替换连接器结果提供者。"""

    emits_connector_events = True

    def __init__(self, runtime: SnapshotRuntime) -> None:
        self.runtime = runtime
        clear_retrieval_cache()
        clear_source_cooldowns()

    def __call__(self, query: str, **kwargs: object) -> RetrievalOutput:
        purpose = str(kwargs.get("query_purpose") or "")
        generated_by = (
            "query_evolution"
            if purpose.startswith("query_evolution")
            else "initial_retrieval"
        )
        stage = (
            "query_evolution"
            if generated_by == "query_evolution"
            else "initial_retrieval"
        )
        query_evolution_policy = (
            "coverage_gap"
            if purpose.startswith("query_evolution_coverage_gap")
            else "seed_expansion" if generated_by == "query_evolution" else None
        )
        provider = lambda source, adapted_query, limit, policy, live_search: (
            self.runtime.search(
                source,
                adapted_query,
                limit,
                policy,
                live_search,
                stage=stage,
                origin_subquery=query,
                generated_by=generated_by,
                query_evolution_policy=query_evolution_policy,
                query_planning_policy=self.runtime.query_planning_policy,
                query_planner_version=self.runtime.query_planner_version,
            )
        )
        return retrieve_papers(
            query,
            **kwargs,
            connector_result_provider=provider,
        )

    def budget_elapsed_seconds(self) -> float:
        return self.runtime.budget_elapsed_seconds()


class SnapshotAwareReferenceFetcher:
    def __init__(
        self,
        runtime: SnapshotRuntime,
        live_fetcher: LiveReferenceFetcher,
    ) -> None:
        self.runtime = runtime
        self.live_fetcher = live_fetcher

    def __call__(self, paper: Paper, limit: int = 20) -> ConnectorSearchResult:
        return self.runtime.fetch_references(paper, limit, self.live_fetcher)

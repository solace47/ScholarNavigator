"""原子快照存储、稳定键、完整性校验与覆盖率检查。"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from scholar_agent.core.paper_schemas import Paper
from scholar_agent.evaluation.metrics import paper_identifier_set
from scholar_agent.evaluation.snapshots.schemas import (
    CONNECTOR_VERSIONS,
    QUERY_ADAPTER_VERSION,
    SNAPSHOT_SCHEMA_VERSION,
    ReferenceSnapshotEntry,
    RetrievalSnapshotEntry,
    SnapshotGroupObservation,
    SnapshotManifest,
)


class SnapshotError(RuntimeError):
    """快照错误基类。"""


class SnapshotMissingError(SnapshotError):
    """Replay 请求缺少快照。"""


class SnapshotConflictError(SnapshotError):
    """同一键对应不同内容且未允许覆盖。"""


class SnapshotIntegrityError(SnapshotError):
    """快照 Schema、键或内容哈希无效。"""


EntryT = TypeVar("EntryT", RetrievalSnapshotEntry, ReferenceSnapshotEntry)
EntryKind = Literal["retrieval", "references"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_snapshot_query(query: str) -> str:
    """只规范 Unicode、控制字符和空白，保留字段语法、引号与词序。"""

    normalized = unicodedata.normalize("NFKC", str(query))
    visible = "".join(
        character
        for character in normalized
        if character in "\t\n\r" or unicodedata.category(character) != "Cc"
    )
    return " ".join(visible.split())


def retrieval_snapshot_key(
    *,
    source: str,
    adapted_query: str,
    limit: int,
    adapter_policy: str,
    query_adapter_version: str = QUERY_ADAPTER_VERSION,
    connector_version: str,
    schema_version: str = SNAPSHOT_SCHEMA_VERSION,
) -> tuple[str, str]:
    normalized_query = normalize_snapshot_query(adapted_query)
    payload = {
        "adapter_policy": adapter_policy,
        "adapted_query": normalized_query,
        "connector_version": connector_version,
        "limit": int(limit),
        "query_adapter_version": query_adapter_version,
        "schema_version": schema_version,
        "source": source.strip().casefold(),
    }
    return _stable_hash(payload), normalized_query


def canonical_seed_identifier(paper: Paper) -> str | None:
    identifiers = paper_identifier_set(paper)
    for prefix in ("openalex:", "doi:"):
        matches = sorted(value for value in identifiers if value.startswith(prefix))
        if matches:
            return matches[0]
    return None


def reference_snapshot_key(
    *,
    seed_identifier: str,
    limit: int,
    connector_version: str,
    source: str = "openalex",
    schema_version: str = SNAPSHOT_SCHEMA_VERSION,
) -> str:
    payload = {
        "connector_version": connector_version,
        "limit": int(limit),
        "schema_version": schema_version,
        "seed_identifier": seed_identifier.strip().casefold(),
        "source": source.strip().casefold(),
    }
    return _stable_hash(payload)


def entry_content_hash(entry: BaseModel | dict[str, Any]) -> str:
    payload = (
        entry.model_dump(mode="json") if isinstance(entry, BaseModel) else dict(entry)
    )
    payload.pop("content_hash", None)
    payload.pop("recorded_at", None)
    return _stable_hash(payload)


class SnapshotStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.retrieval_dir = self.root / "retrieval"
        self.reference_dir = self.root / "references"
        self.manifest_path = self.root / "manifest.json"

    def read_retrieval(self, key: str) -> RetrievalSnapshotEntry:
        return self._read_entry("retrieval", key, RetrievalSnapshotEntry)

    def read_reference(self, key: str) -> ReferenceSnapshotEntry:
        return self._read_entry("references", key, ReferenceSnapshotEntry)

    def write_retrieval(
        self,
        entry: RetrievalSnapshotEntry,
        *,
        overwrite: bool = False,
    ) -> bool:
        return self._write_entry("retrieval", entry, overwrite=overwrite)

    def write_reference(
        self,
        entry: ReferenceSnapshotEntry,
        *,
        overwrite: bool = False,
    ) -> bool:
        return self._write_entry("references", entry, overwrite=overwrite)

    def ensure_manifest(self, manifest: SnapshotManifest) -> SnapshotManifest:
        if self.manifest_path.is_file():
            existing = self.read_manifest()
            mismatched = [
                field
                for field in (
                    "snapshot_name",
                    "schema_version",
                    "dataset",
                    "split",
                    "offset",
                    "limit",
                    "sources",
                    "adapter_policy",
                    "query_adapter_version",
                    "run_profile",
                    "budgets",
                    "llm_enabled",
                    "query_understanding_prompt",
                    "judgement_prompt",
                    "connector_versions",
                )
                if getattr(existing, field) != getattr(manifest, field)
            ]
            if mismatched:
                raise SnapshotConflictError(
                    "snapshot_manifest_incompatible:" + ",".join(mismatched)
                )
            return existing
        self._write_manifest(self._with_entry_counts(manifest))
        return manifest

    def read_manifest(self) -> SnapshotManifest:
        try:
            return SnapshotManifest.model_validate_json(
                self.manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise SnapshotIntegrityError("snapshot_manifest_invalid") from exc

    def update_group(
        self,
        group_name: str,
        observation: SnapshotGroupObservation,
    ) -> SnapshotManifest:
        manifest = self.read_manifest()
        groups = dict(manifest.groups)
        groups[group_name] = observation
        updated = self._with_entry_counts(
            manifest.model_copy(
                update={"groups": groups, "updated_at": utc_now()}
            )
        )
        self._write_manifest(updated)
        return updated

    def inspect(self) -> dict[str, Any]:
        manifest = self.read_manifest()
        retrieval = self._inspect_kind("retrieval", RetrievalSnapshotEntry)
        references = self._inspect_kind("references", ReferenceSnapshotEntry)
        retrieval_keys = {entry.key for entry in retrieval["entries"]}
        reference_keys = {entry.key for entry in references["entries"]}
        groups = {
            name: {
                "completed": observation.completed,
                "collection_completed": observation.collection_completed,
                "replay_verified": observation.replay_verified,
                "retrieval_key_count": len(observation.retrieval_keys),
                "reference_key_count": len(observation.reference_keys),
                "missing_retrieval_keys": sorted(
                    set(observation.missing_retrieval_keys)
                    | (set(observation.retrieval_keys) - retrieval_keys)
                ),
                "missing_reference_keys": sorted(
                    set(observation.missing_reference_keys)
                    | (set(observation.reference_keys) - reference_keys)
                ),
                "replay_ready": bool(
                    observation.collection_completed
                    and observation.replay_verified
                    and not (
                        set(observation.missing_retrieval_keys)
                        | (set(observation.retrieval_keys) - retrieval_keys)
                    )
                    and not (
                        set(observation.missing_reference_keys)
                        | (set(observation.reference_keys) - reference_keys)
                    )
                ),
            }
            for name, observation in sorted(manifest.groups.items())
        }
        entries = [*retrieval.pop("entries"), *references.pop("entries")]
        diagnostics = [entry.diagnostics for entry in entries]
        return {
            "snapshot_name": manifest.snapshot_name,
            "schema_version": manifest.schema_version,
            "retrieval_entries": retrieval["entry_count"],
            "reference_entries": references["entry_count"],
            "successful_entries": sum(entry.status == "success" for entry in entries),
            "failed_entries": sum(entry.status == "failed" for entry in entries),
            "sources": sorted({entry.source for entry in entries}),
            "request_count_recorded": sum(item.request_count for item in diagnostics),
            "retry_count_recorded": sum(item.retry_count for item in diagnostics),
            "missing_keys_observed": sorted(
                {
                    key
                    for observation in manifest.groups.values()
                    for key in [
                        *observation.missing_retrieval_keys,
                        *observation.missing_reference_keys,
                    ]
                }
            ),
            "duplicate_keys": retrieval["duplicate_keys"] + references["duplicate_keys"],
            "invalid_entries": retrieval["invalid_entries"] + references["invalid_entries"],
            "hash_mismatch_entries": retrieval["hash_mismatch_entries"]
            + references["hash_mismatch_entries"],
            "groups": groups,
        }

    def _read_entry(
        self,
        kind: EntryKind,
        key: str,
        model: type[EntryT],
    ) -> EntryT:
        path = self._entry_path(kind, key)
        if not path.is_file():
            raise SnapshotMissingError(f"snapshot_missing:{kind}:{key}")
        try:
            entry = model.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise SnapshotIntegrityError(f"snapshot_invalid:{kind}:{key}") from exc
        if entry.schema_version != SNAPSHOT_SCHEMA_VERSION:
            raise SnapshotIntegrityError(f"snapshot_schema_incompatible:{kind}:{key}")
        if entry.key != key:
            raise SnapshotIntegrityError(f"snapshot_key_mismatch:{kind}:{key}")
        if entry.content_hash != entry_content_hash(entry):
            raise SnapshotIntegrityError(f"snapshot_hash_mismatch:{kind}:{key}")
        return entry

    def _write_entry(
        self,
        kind: EntryKind,
        entry: EntryT,
        *,
        overwrite: bool,
    ) -> bool:
        if entry.schema_version != SNAPSHOT_SCHEMA_VERSION:
            raise SnapshotIntegrityError("snapshot_schema_incompatible")
        expected_hash = entry_content_hash(entry)
        if entry.content_hash != expected_hash:
            raise SnapshotIntegrityError("snapshot_content_hash_invalid")
        path = self._entry_path(kind, entry.key)
        if path.is_file():
            existing = self._read_entry(
                kind,
                entry.key,
                RetrievalSnapshotEntry if kind == "retrieval" else ReferenceSnapshotEntry,
            )
            if existing.content_hash == entry.content_hash:
                return False
            if not overwrite:
                raise SnapshotConflictError(f"snapshot_content_conflict:{kind}:{entry.key}")
        self._atomic_write_json(path, entry.model_dump(mode="json"))
        return True

    def _inspect_kind(self, kind: EntryKind, model: type[EntryT]) -> dict[str, Any]:
        directory = self.retrieval_dir if kind == "retrieval" else self.reference_dir
        entries: list[EntryT] = []
        seen: set[str] = set()
        duplicate_keys = 0
        invalid_entries = 0
        hash_mismatch_entries = 0
        for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
            key = path.stem
            if key in seen:
                duplicate_keys += 1
            seen.add(key)
            try:
                entries.append(self._read_entry(kind, key, model))
            except SnapshotIntegrityError as exc:
                invalid_entries += 1
                if "hash_mismatch" in str(exc):
                    hash_mismatch_entries += 1
        return {
            "entry_count": len(entries),
            "entries": entries,
            "duplicate_keys": duplicate_keys,
            "invalid_entries": invalid_entries,
            "hash_mismatch_entries": hash_mismatch_entries,
        }

    def _with_entry_counts(self, manifest: SnapshotManifest) -> SnapshotManifest:
        retrieval_count = len(list(self.retrieval_dir.glob("*.json"))) if self.retrieval_dir.is_dir() else 0
        reference_count = len(list(self.reference_dir.glob("*.json"))) if self.reference_dir.is_dir() else 0
        return manifest.model_copy(
            update={
                "retrieval_entry_count": retrieval_count,
                "reference_entry_count": reference_count,
            }
        )

    def _write_manifest(self, manifest: SnapshotManifest) -> None:
        self._atomic_write_json(
            self.manifest_path,
            manifest.model_dump(mode="json"),
        )

    def _entry_path(self, kind: EntryKind, key: str) -> Path:
        if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
            raise SnapshotIntegrityError("snapshot_key_invalid")
        directory = self.retrieval_dir if kind == "retrieval" else self.reference_dir
        return directory / f"{key}.json"

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def connector_version(source: str, *, references: bool = False) -> str:
    key = "openalex_references" if references else source
    try:
        return CONNECTOR_VERSIONS[key]
    except KeyError as exc:
        raise ValueError(f"snapshot_unsupported_source:{source}") from exc


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

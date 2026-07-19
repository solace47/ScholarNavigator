"""Benchmark 检索与引用响应快照 Schema。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from scholar_agent.core.diagnostics_schemas import ConnectorDiagnostics
from scholar_agent.core.paper_schemas import Paper


SNAPSHOT_SCHEMA_VERSION = "1"
QUERY_ADAPTER_VERSION = "1"
CONNECTOR_VERSIONS = {
    "arxiv": "search-v1",
    "openalex": "search-v1",
    "semantic_scholar": "search-v1",
    "pubmed": "search-v1",
    "openalex_references": "references-v1",
}
SnapshotEntryStatus = Literal["success", "failed"]


class RetrievalSnapshotEntry(BaseModel):
    schema_version: str = SNAPSHOT_SCHEMA_VERSION
    key: str = Field(min_length=64, max_length=64)
    source: str
    adapted_query: str
    normalized_query: str
    limit: int = Field(ge=0)
    adapter_policy: str
    query_adapter_version: str = QUERY_ADAPTER_VERSION
    connector_version: str
    status: SnapshotEntryStatus
    papers: list[Paper] = Field(default_factory=list)
    error_message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    diagnostics: ConnectorDiagnostics = Field(default_factory=ConnectorDiagnostics)
    recorded_latency_seconds: float = Field(default=0.0, ge=0.0)
    recorded_at: str
    content_hash: str = Field(min_length=64, max_length=64)


class ReferenceSnapshotEntry(BaseModel):
    schema_version: str = SNAPSHOT_SCHEMA_VERSION
    key: str = Field(min_length=64, max_length=64)
    source: str = "openalex"
    seed_identifier: str
    limit: int = Field(ge=0)
    connector_version: str
    status: SnapshotEntryStatus
    papers: list[Paper] = Field(default_factory=list)
    error_message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    diagnostics: ConnectorDiagnostics = Field(default_factory=ConnectorDiagnostics)
    recorded_latency_seconds: float = Field(default=0.0, ge=0.0)
    recorded_at: str
    content_hash: str = Field(min_length=64, max_length=64)


class SnapshotGroupObservation(BaseModel):
    retrieval_keys: list[str] = Field(default_factory=list)
    reference_keys: list[str] = Field(default_factory=list)
    missing_retrieval_keys: list[str] = Field(default_factory=list)
    missing_reference_keys: list[str] = Field(default_factory=list)
    collection_completed: bool = False
    replay_verified: bool = False
    completed: bool = False
    updated_at: str


class SnapshotManifest(BaseModel):
    snapshot_name: str
    schema_version: str = SNAPSHOT_SCHEMA_VERSION
    dataset: str
    split: str
    offset: int = Field(ge=0)
    limit: int | None = Field(default=None, ge=1)
    sources: list[str]
    adapter_policy: str
    query_adapter_version: str = QUERY_ADAPTER_VERSION
    run_profile: str
    budgets: dict[str, object]
    llm_enabled: bool
    query_understanding_prompt: dict[str, str | int | None]
    judgement_prompt: dict[str, str | int | None]
    connector_versions: dict[str, str]
    code_hash: str
    git_commit: str | None = None
    dirty_worktree: bool
    retrieval_entry_count: int = Field(default=0, ge=0)
    reference_entry_count: int = Field(default=0, ge=0)
    groups: dict[str, SnapshotGroupObservation] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class SnapshotCostReport(BaseModel):
    mode: str
    retrieval_snapshot_hits: int = Field(default=0, ge=0)
    reference_snapshot_hits: int = Field(default=0, ge=0)
    retrieval_snapshot_writes: int = Field(default=0, ge=0)
    reference_snapshot_writes: int = Field(default=0, ge=0)
    missing_retrieval_keys: list[str] = Field(default_factory=list)
    missing_reference_keys: list[str] = Field(default_factory=list)
    fatal_errors: list[str] = Field(default_factory=list)
    observed_retrieval_keys: list[str] = Field(default_factory=list)
    observed_reference_keys: list[str] = Field(default_factory=list)
    replay_execution_request_count: int = Field(default=0, ge=0)
    replay_execution_retry_count: int = Field(default=0, ge=0)
    replay_execution_network_wait_seconds: float = Field(default=0.0, ge=0.0)
    recorded_search_request_count: int = Field(default=0, ge=0)
    recorded_reference_request_count: int = Field(default=0, ge=0)
    recorded_retry_count: int = Field(default=0, ge=0)
    recorded_error_count: int = Field(default=0, ge=0)
    recorded_rate_limit_wait_seconds: float = Field(default=0.0, ge=0.0)
    recorded_latency_seconds: float = Field(default=0.0, ge=0.0)

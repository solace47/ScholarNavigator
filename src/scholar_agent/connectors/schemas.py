"""Shared connector result schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from scholar_agent.core.diagnostics_schemas import ConnectorDiagnostics
from scholar_agent.core.paper_schemas import Paper


class ConnectorSearchResult(BaseModel):
    papers: list[Paper] = Field(default_factory=list)
    error_message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    latency_seconds: float = 0.0
    diagnostics: ConnectorDiagnostics = Field(default_factory=ConnectorDiagnostics)

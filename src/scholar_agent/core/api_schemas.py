"""Pydantic schemas for the FastAPI mock API.

The mock API mirrors the planned contract closely enough for frontend
integration while intentionally avoiding real search connectors and LLM calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
RunProfile = Literal["fast", "balanced", "high_recall", "evaluation"]
RelevanceCategory = Literal[
    "highly_relevant",
    "partially_relevant",
    "weakly_relevant",
    "irrelevant",
    "insufficient_evidence",
]


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    time: datetime


class LLMRuntimeConfig(BaseModel):
    provider: str
    model: str
    available: bool


class ConnectorRuntimeConfig(BaseModel):
    name: str
    available: bool
    requires_key: bool
    reason: str | None = None


class RuntimeLimits(BaseModel):
    max_top_k: int
    max_search_rounds: int
    max_candidate_papers: int
    max_latency_seconds: int


class RuntimeFeatures(BaseModel):
    query_evolution: bool
    refchain: bool
    evaluation: bool
    sse: bool


class RuntimeConfigResponse(BaseModel):
    mode: str = "mock"
    llm: LLMRuntimeConfig
    connectors: list[ConnectorRuntimeConfig]
    limits: RuntimeLimits
    features: RuntimeFeatures


class TimeRangeConstraint(BaseModel):
    start_year: int | None = None
    end_year: int | None = None


class SearchConstraints(BaseModel):
    time_range: TimeRangeConstraint | None = None
    venues: list[str] = Field(default_factory=list)
    must_have_terms: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    paper_types: list[str] = Field(default_factory=list)


class SearchBudgets(BaseModel):
    max_search_rounds: int = 2
    max_candidate_papers: int = 200
    max_llm_calls: int = 20
    max_total_tokens: int = 50_000
    max_latency_seconds: int = 90


class SearchOptions(BaseModel):
    enable_query_evolution: bool = True
    enable_refchain: bool = True
    refchain_depth: int = 1
    return_markdown: bool = True
    return_json: bool = True
    stream_events: bool = True


class SearchRunCreateRequest(BaseModel):
    query: str = Field(..., min_length=1)
    locale: str = "zh-CN"
    constraints: SearchConstraints = Field(default_factory=SearchConstraints)
    source_preferences: list[str] = Field(
        default_factory=lambda: ["openalex", "arxiv", "semantic_scholar"]
    )
    run_profile: RunProfile = "balanced"
    top_k: int = Field(default=20, ge=1, le=100)
    budgets: SearchBudgets = Field(default_factory=SearchBudgets)
    options: SearchOptions = Field(default_factory=SearchOptions)


class SearchRunCreateResponse(BaseModel):
    run_id: str
    status: RunStatus
    created_at: datetime
    links: dict[str, str]


class RunProgress(BaseModel):
    completed_stages: list[str] = Field(default_factory=list)
    candidate_paper_count: int = 0
    judged_paper_count: int = 0


class CostReport(BaseModel):
    api_call_count: int = 0
    search_api_call_count: int = 0
    llm_call_count: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_total_tokens: int = 0
    latency_seconds: float = 0.0
    cache_hit_count: int = 0
    search_rounds: int = 0
    judged_paper_count: int = 0


class SearchRunStatusResponse(BaseModel):
    run_id: str
    status: RunStatus
    current_stage: str
    progress: RunProgress
    cost_report: CostReport
    created_at: datetime
    updated_at: datetime


class PaperIdentifiers(BaseModel):
    doi: str | None = None
    arxiv_id: str | None = None
    semantic_scholar_id: str | None = None
    openalex_id: str | None = None
    pubmed_id: str | None = None


class PaperUrls(BaseModel):
    landing_page: str | None = None
    pdf: str | None = None


class Paper(BaseModel):
    title: str
    authors: list[str]
    year: int
    venue: str | None = None
    abstract: str
    identifiers: PaperIdentifiers = Field(default_factory=PaperIdentifiers)
    urls: PaperUrls = Field(default_factory=PaperUrls)
    sources: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    source: str
    text: str
    confidence: float = Field(ge=0.0, le=1.0)


class SynthesisEvidenceRow(BaseModel):
    row_id: str
    citation_key: str
    rank: int
    paper_title: str
    year: int | None = None
    venue: str | None = None
    sources: list[str] = Field(default_factory=list)
    identifiers: PaperIdentifiers = Field(default_factory=PaperIdentifiers)
    category: str
    final_score: float = Field(ge=0.0, le=1.0)
    evidence_source: str
    evidence_text: str
    supported_terms: list[str] = Field(default_factory=list)
    supported_claim: str


class SynthesisFinding(BaseModel):
    text: str
    citation_keys: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_row_ids: list[str] = Field(default_factory=list)


class CitationCoverage(BaseModel):
    ranked_paper_count: int = 0
    cited_paper_count: int = 0
    evidence_row_count: int = 0
    cited_evidence_row_count: int = 0
    missing_evidence_count: int = 0
    source_error_count: int = 0
    coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)


class SynthesisOutput(BaseModel):
    answer_summary: str
    status: str = "succeeded"
    key_findings: list[SynthesisFinding] = Field(default_factory=list)
    evidence_table: list[SynthesisEvidenceRow] = Field(default_factory=list)
    citation_coverage: CitationCoverage = Field(default_factory=CitationCoverage)
    limitations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RankedPaper(BaseModel):
    rank: int
    paper: Paper
    relevance_score: float = Field(ge=0.0, le=1.0)
    category: RelevanceCategory
    matched_constraints: list[str] = Field(default_factory=list)
    ranking_reason: str
    evidence: list[EvidenceItem] = Field(default_factory=list)


class QueryAnalysis(BaseModel):
    intent_type: str
    domain: str
    research_topics: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)


class SearchPlan(BaseModel):
    expanded_queries: list[str] = Field(default_factory=list)
    source_preferences: list[str] = Field(default_factory=list)
    max_rounds: int = 1


class MethodCluster(BaseModel):
    name: str
    paper_ranks: list[int] = Field(default_factory=list)
    summary: str


class TimelineItem(BaseModel):
    year: int
    paper_ranks: list[int] = Field(default_factory=list)
    summary: str


class CitationGraphNode(BaseModel):
    id: str
    label: str
    rank: int | None = None


class CitationGraphEdge(BaseModel):
    source: str
    target: str
    relation: str = "cites"


class CitationGraph(BaseModel):
    nodes: list[CitationGraphNode] = Field(default_factory=list)
    edges: list[CitationGraphEdge] = Field(default_factory=list)


class SearchRunResultResponse(BaseModel):
    run_id: str
    status: RunStatus
    partial: bool
    query_analysis: QueryAnalysis
    search_plan: SearchPlan
    highly_relevant_papers: list[RankedPaper]
    partially_relevant_papers: list[RankedPaper]
    method_clusters: list[MethodCluster]
    timeline: list[TimelineItem]
    citation_graph: CitationGraph = Field(default_factory=CitationGraph)
    missing_evidence: list[str] = Field(default_factory=list)
    synthesis: SynthesisOutput | None = None
    cost_report: CostReport

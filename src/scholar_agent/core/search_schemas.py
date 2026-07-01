"""Internal schemas for search planning and pipeline orchestration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from scholar_agent.core.paper_schemas import Paper


SourceName = Literal["openalex", "arxiv"]
RunProfile = Literal["fast", "balanced", "high_recall", "evaluation"]
QueryLanguage = Literal["zh", "en", "mixed", "unknown"]
QueryIntent = Literal[
    "survey",
    "recent_progress",
    "method_comparison",
    "benchmark_or_dataset",
    "application",
    "paper_finding",
    "general",
]
ResearchDomain = Literal[
    "computer_science",
    "machine_learning",
    "biomedical",
    "general_science",
]
EvidenceSource = Literal["title", "abstract", "venue", "metadata"]
JudgementCategory = Literal[
    "highly_relevant",
    "partially_relevant",
    "weakly_relevant",
    "irrelevant",
    "insufficient_evidence",
]

SUPPORTED_SEARCH_SOURCES: tuple[str, ...] = ("openalex", "arxiv")


class TimeRange(BaseModel):
    start_year: int | None = Field(default=None, ge=1800, le=2200)
    end_year: int | None = Field(default=None, ge=1800, le=2200)
    label: str | None = None

    @model_validator(mode="after")
    def validate_year_order(self) -> "TimeRange":
        if (
            self.start_year is not None
            and self.end_year is not None
            and self.end_year < self.start_year
        ):
            raise ValueError("end_year must be greater than or equal to start_year")
        return self


class QueryConstraint(BaseModel):
    time_range: TimeRange | None = None
    venues: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    must_include_terms: list[str] = Field(default_factory=list)
    exclude_terms: list[str] = Field(default_factory=list)


class QueryAnalysis(BaseModel):
    original_query: str
    language: QueryLanguage = "unknown"
    intent: QueryIntent = "general"
    domain: ResearchDomain = "general_science"
    constraints: QueryConstraint = Field(default_factory=QueryConstraint)
    needs_expansion: bool = False
    reasoning: list[str] = Field(default_factory=list)


class SearchSubquery(BaseModel):
    query: str = Field(..., min_length=1)
    source_hints: list[SourceName] = Field(
        default_factory=lambda: list(SUPPORTED_SEARCH_SOURCES)
    )
    priority: int = Field(default=1, ge=1, le=5)
    purpose: str = Field(..., min_length=1)

    @field_validator("source_hints", mode="before")
    @classmethod
    def normalize_source_hints(cls, value: object) -> list[str]:
        return _normalize_sources(value)


class SearchPlan(BaseModel):
    query_analysis: QueryAnalysis
    subqueries: list[SearchSubquery] = Field(default_factory=list)
    selected_sources: list[SourceName] = Field(
        default_factory=lambda: list(SUPPORTED_SEARCH_SOURCES)
    )
    limit_per_source: int = Field(default=20, ge=1, le=100)
    top_k: int = Field(default=20, ge=1, le=100)
    run_profile: RunProfile = "balanced"
    enable_refchain: bool = False
    enable_query_evolution: bool = False
    warnings: list[str] = Field(default_factory=list)

    @field_validator("selected_sources", mode="before")
    @classmethod
    def normalize_selected_sources(cls, value: object) -> list[str]:
        return _normalize_sources(value)


class QueryUnderstandingOptions(BaseModel):
    top_k: int = Field(default=20, ge=1, le=100)
    run_profile: RunProfile = "balanced"
    enable_refchain: bool = False
    enable_query_evolution: bool = False
    current_year: int | None = Field(default=None, ge=1900, le=2200)


class EvidenceItem(BaseModel):
    source: EvidenceSource
    text: str
    confidence: float = Field(ge=0.0, le=1.0)


class JudgementResult(BaseModel):
    paper: Paper
    score: float = Field(ge=0.0, le=1.0)
    category: JudgementCategory
    reasoning: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RerankScoreBreakdown(BaseModel):
    relevance_score: float = Field(ge=0.0, le=1.0)
    authority_score: float = Field(ge=0.0, le=1.0)
    timeliness_score: float = Field(ge=0.0, le=1.0)
    metadata_score: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)
    relevance_weight: float = Field(ge=0.0, le=1.0)
    authority_weight: float = Field(ge=0.0, le=1.0)
    timeliness_weight: float = Field(ge=0.0, le=1.0)
    metadata_weight: float = Field(ge=0.0, le=1.0)


class RankedPaper(BaseModel):
    rank: int = Field(ge=1)
    paper: Paper
    final_score: float = Field(ge=0.0, le=1.0)
    category: JudgementCategory
    score_breakdown: RerankScoreBreakdown
    ranking_reason: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class QueryEvolutionOptions(BaseModel):
    max_evolved_queries: int = Field(default=3, ge=0, le=10)
    max_seed_papers: int = Field(default=5, ge=0, le=20)
    min_seed_score: float = Field(default=0.45, ge=0.0, le=1.0)


class EvolvedSubquery(BaseModel):
    query: str = Field(..., min_length=1)
    source_hints: list[SourceName] = Field(
        default_factory=lambda: list(SUPPORTED_SEARCH_SOURCES)
    )
    priority: int = Field(default=1, ge=1, le=5)
    purpose: str = Field(..., min_length=1)
    seed_paper_titles: list[str] = Field(default_factory=list)
    generated_by: Literal["rules", "llm"] = "rules"
    warnings: list[str] = Field(default_factory=list)

    @field_validator("source_hints", mode="before")
    @classmethod
    def normalize_source_hints(cls, value: object) -> list[str]:
        return _normalize_sources(value)


class QueryEvolutionRecord(BaseModel):
    round_index: int = Field(default=1, ge=1)
    seed_count: int = Field(default=0, ge=0)
    generated_queries: list[EvolvedSubquery] = Field(default_factory=list)
    skipped_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    latency_seconds: float = Field(default=0.0, ge=0.0)


class RefChainOptions(BaseModel):
    max_seed_papers: int = Field(default=3, ge=0, le=20)
    max_references_per_seed: int = Field(default=15, ge=0, le=100)
    max_total_references: int = Field(default=50, ge=0, le=500)
    min_seed_score: float = Field(default=0.45, ge=0.0, le=1.0)


class RefChainSeed(BaseModel):
    paper: Paper
    rank: int = Field(ge=1)
    score: float = Field(ge=0.0, le=1.0)
    reason: str


class ReferenceEdge(BaseModel):
    seed_paper_id: str
    reference_paper_id: str
    source: SourceName = "openalex"
    relation: Literal["references"] = "references"


class RefChainRecord(BaseModel):
    seeds: list[RefChainSeed] = Field(default_factory=list)
    reference_edges: list[ReferenceEdge] = Field(default_factory=list)
    raw_reference_count: int = Field(default=0, ge=0)
    returned_reference_count: int = Field(default=0, ge=0)
    skipped_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    latency_seconds: float = Field(default=0.0, ge=0.0)


class RefChainOutput(BaseModel):
    references: list[Paper] = Field(default_factory=list)
    reference_edges: list[ReferenceEdge] = Field(default_factory=list)
    record: RefChainRecord
    warnings: list[str] = Field(default_factory=list)
    latency_seconds: float = Field(default=0.0, ge=0.0)


def _normalize_sources(value: object) -> list[str]:
    if value is None:
        return list(SUPPORTED_SEARCH_SOURCES)
    if isinstance(value, str):
        raw_sources = [value]
    else:
        raw_sources = list(value)  # type: ignore[arg-type]

    normalized: list[str] = []
    seen: set[str] = set()
    for source in raw_sources:
        key = str(source).strip().lower()
        if not key or key in seen:
            continue
        if key not in SUPPORTED_SEARCH_SOURCES:
            raise ValueError(f"unsupported search source: {key}")
        normalized.append(key)
        seen.add(key)
    return normalized

"""Internal schemas for search planning and pipeline orchestration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from scholar_agent.core.paper_schemas import Paper
from scholar_agent.core.diagnostics_schemas import ConnectorDiagnostics


SourceName = Literal["openalex", "arxiv", "semantic_scholar", "pubmed"]
PaperType = Literal[
    "survey",
    "review",
    "method",
    "benchmark",
    "dataset",
    "application",
    "comparison",
]
ConstraintField = Literal[
    "time_range",
    "venues",
    "methods",
    "datasets",
    "domains",
    "must_include_terms",
    "exclude_terms",
    "paper_types",
]
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

SUPPORTED_SEARCH_SOURCES: tuple[str, ...] = (
    "openalex",
    "arxiv",
    "semantic_scholar",
    "pubmed",
)
SUPPORTED_PAPER_TYPES: tuple[str, ...] = (
    "survey",
    "review",
    "method",
    "benchmark",
    "dataset",
    "application",
    "comparison",
)


class SearchBudget(BaseModel):
    """Execution limits shared by API, CLI, evaluators, and SearchService."""

    max_search_rounds: int = Field(default=2, ge=1, le=3)
    max_candidate_papers: int = Field(default=200, ge=1, le=300)
    max_llm_calls: int = Field(default=20, ge=0, le=100)
    max_total_tokens: int = Field(default=50_000, ge=0, le=1_000_000)
    max_latency_seconds: float = Field(default=90.0, gt=0.0, le=120.0)


DEFAULT_SEARCH_BUDGET = SearchBudget()


class CandidateTruncation(BaseModel):
    stage: str
    before_count: int = Field(ge=0)
    after_count: int = Field(ge=0)
    truncated_count: int = Field(ge=0)


class BudgetStatus(BaseModel):
    exhausted: bool = False
    stop_reasons: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    max_search_rounds: int = DEFAULT_SEARCH_BUDGET.max_search_rounds
    completed_search_rounds: int = 0
    max_candidate_papers: int = DEFAULT_SEARCH_BUDGET.max_candidate_papers
    candidate_limit_applied: bool = False
    candidate_truncations: list[CandidateTruncation] = Field(default_factory=list)
    max_llm_calls: int = DEFAULT_SEARCH_BUDGET.max_llm_calls
    used_llm_calls: int = 0
    max_total_tokens: int = DEFAULT_SEARCH_BUDGET.max_total_tokens
    used_total_tokens: int = 0
    token_usage_precise: bool = True
    max_latency_seconds: float = DEFAULT_SEARCH_BUDGET.max_latency_seconds
    elapsed_seconds: float = 0.0
PAPER_TYPE_ALIASES: dict[str, str] = {
    "survey": "survey",
    "review": "review",
    "literature_review": "review",
    "systematic_review": "review",
    "method": "method",
    "methods": "method",
    "methodology": "method",
    "benchmark": "benchmark",
    "benchmarking": "benchmark",
    "dataset": "dataset",
    "data_set": "dataset",
    "application": "application",
    "applied": "application",
    "comparison": "comparison",
    "comparative": "comparison",
}


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
    paper_types: list[PaperType] = Field(default_factory=list)
    explicit_fields: list[ConstraintField] = Field(
        default_factory=list,
        exclude=True,
    )

    @field_validator(
        "venues",
        "methods",
        "datasets",
        "domains",
        "must_include_terms",
        "exclude_terms",
        mode="before",
    )
    @classmethod
    def normalize_string_constraints(cls, value: object) -> list[str]:
        return normalize_constraint_values(value)

    @field_validator("paper_types", mode="before")
    @classmethod
    def normalize_paper_type_constraints(cls, value: object) -> list[str]:
        return normalize_paper_types(value)

    @field_validator("explicit_fields", mode="before")
    @classmethod
    def normalize_explicit_fields(cls, value: object) -> list[str]:
        return normalize_constraint_values(value)


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
    use_llm: bool | None = None
    explicit_constraints: QueryConstraint | None = None


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
    category_multiplier: float = Field(default=1.0, ge=0.0, le=1.0)
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


class RefChainSeedDiagnostic(BaseModel):
    seed_id: str | None = None
    seed_rank: int = Field(ge=1)
    seed_category: JudgementCategory
    seed_score: float = Field(ge=0.0, le=1.0)
    identifier_type: str | None = None
    snapshot_key: str | None = None
    request_count: int = Field(default=0, ge=0)
    recorded_request_count: int = Field(default=0, ge=0)
    recorded_retry_count: int = Field(default=0, ge=0)
    recorded_error_count: int = Field(default=0, ge=0)
    recorded_latency_seconds: float = Field(default=0.0, ge=0.0)
    references_returned: int = Field(default=0, ge=0)
    unique_references_returned: int = Field(default=0, ge=0)
    skip_reason: str | None = None


class ReferenceEdge(BaseModel):
    seed_paper_id: str
    reference_paper_id: str
    source: SourceName = "openalex"
    relation: Literal["references"] = "references"


class RefChainRecord(BaseModel):
    seeds: list[RefChainSeed] = Field(default_factory=list)
    seed_diagnostics: list[RefChainSeedDiagnostic] = Field(default_factory=list)
    reference_edges: list[ReferenceEdge] = Field(default_factory=list)
    raw_reference_count: int = Field(default=0, ge=0)
    returned_reference_count: int = Field(default=0, ge=0)
    skipped_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    latency_seconds: float = Field(default=0.0, ge=0.0)
    diagnostics: ConnectorDiagnostics = Field(default_factory=ConnectorDiagnostics)
    recorded_diagnostics: ConnectorDiagnostics = Field(
        default_factory=ConnectorDiagnostics
    )
    recorded_latency_seconds: float = Field(default=0.0, ge=0.0)


class RefChainOutput(BaseModel):
    references: list[Paper] = Field(default_factory=list)
    reference_edges: list[ReferenceEdge] = Field(default_factory=list)
    record: RefChainRecord
    warnings: list[str] = Field(default_factory=list)
    latency_seconds: float = Field(default=0.0, ge=0.0)
    diagnostics: ConnectorDiagnostics = Field(default_factory=ConnectorDiagnostics)
    recorded_diagnostics: ConnectorDiagnostics = Field(
        default_factory=ConnectorDiagnostics
    )
    recorded_latency_seconds: float = Field(default=0.0, ge=0.0)


def _normalize_sources(value: object) -> list[str]:
    return normalize_search_sources(value)


def normalize_search_sources(value: object) -> list[str]:
    """Normalize supported search sources with stable de-duplication."""

    if value is None:
        return list(SUPPORTED_SEARCH_SOURCES)
    if isinstance(value, str):
        raw_sources = [value]
    else:
        raw_sources = list(value)  # type: ignore[arg-type]

    normalized: list[str] = []
    seen: set[str] = set()
    for source in raw_sources:
        key = (
            str(source)
            .strip()
            .lower()
            .replace("-", "_")
            .replace(" ", "_")
        )
        if key == "semanticscholar":
            key = "semantic_scholar"
        if not key or key in seen:
            continue
        if key not in SUPPORTED_SEARCH_SOURCES:
            raise ValueError(f"unsupported search source: {key}")
        normalized.append(key)
        seen.add(key)
    return normalized


def normalize_constraint_values(value: object) -> list[str]:
    """Trim and case-insensitively de-duplicate constraint strings."""

    if value is None:
        return []
    raw_values = [value] if isinstance(value, str) else list(value)  # type: ignore[arg-type]
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        item = str(raw_value).strip()
        key = item.casefold()
        if not item or key in seen:
            continue
        normalized.append(item)
        seen.add(key)
    return normalized


def normalize_paper_types(value: object) -> list[str]:
    """Normalize supported paper types or reject unknown values."""

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in normalize_constraint_values(value):
        key = reformat_enum_value(raw_value)
        paper_type = PAPER_TYPE_ALIASES.get(key)
        if paper_type is None:
            raise ValueError(f"unsupported paper type: {raw_value}")
        if paper_type in seen:
            continue
        normalized.append(paper_type)
        seen.add(paper_type)
    return normalized


def reformat_enum_value(value: str) -> str:
    return "_".join(value.strip().casefold().replace("-", " ").split())

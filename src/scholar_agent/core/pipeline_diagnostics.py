"""SearchService 可选阶段快照与候选来源追踪。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from scholar_agent.agents.retriever import RetrievalOutput
from scholar_agent.core.dedup import deduplicate_papers
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers
from scholar_agent.core.search_schemas import JudgementResult, RankedPaper


SnapshotStatus = Literal["completed", "skipped"]
OriginKind = Literal[
    "initial_query",
    "initial_generated_subquery",
    "query_evolution",
    "refchain",
]


class CandidateProvenance(BaseModel):
    origin_kind: OriginKind
    origin_stage: str
    origin_subquery: str
    source: str
    adapted_query: str | None = None
    adaptation_strategy: str | None = None
    cache_hit: bool = False
    source_skipped_reason: str | None = None


class RetrievalCallTrace(BaseModel):
    origin_subquery: str
    source: str
    adapted_query: str | None = None
    adaptation_strategy: str | None = None
    cache_hit: bool = False
    run_dedupe_hit: bool = False
    source_skipped_reason: str | None = None
    remaining_subquery_count: int = 0
    returned_count: int = 0
    request_count: int = 0
    error_count: int = 0


class DiagnosticCandidate(BaseModel):
    identifiers: PaperIdentifiers = Field(default_factory=PaperIdentifiers)
    title: str
    year: int | None = None
    sources: list[str] = Field(default_factory=list)
    provenance: list[CandidateProvenance] = Field(default_factory=list)
    rank: int | None = None
    judgement_score: float | None = None
    category: str | None = None
    final_score: float | None = None
    matched_terms: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class StageCandidateSnapshot(BaseModel):
    stage: str
    status: SnapshotStatus = "completed"
    skipped_reason: str | None = None
    candidates: list[DiagnosticCandidate] = Field(default_factory=list)
    retrieval_calls: list[RetrievalCallTrace] = Field(default_factory=list)


@dataclass
class _TrackedCandidate:
    paper: Paper
    provenance: list[CandidateProvenance] = field(default_factory=list)


@dataclass
class _TrackedJudgement:
    paper: Paper
    score: float


class PipelineDiagnosticsCollector:
    """Collect compact snapshots without changing retrieval or ranking decisions."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.snapshots: list[StageCandidateSnapshot] = []
        self._tracked: list[_TrackedCandidate] = []
        self._judgements: list[_TrackedJudgement] = []

    def register_retrieval(
        self,
        stage: str,
        outputs: list[RetrievalOutput],
        *,
        origin_kind_by_query: dict[str, OriginKind],
    ) -> None:
        if not self.enabled:
            return
        papers: list[Paper] = []
        retrieval_calls: list[RetrievalCallTrace] = []
        for output in outputs:
            origin_kind = origin_kind_by_query.get(
                output.query,
                "initial_generated_subquery",
            )
            traced_paper = False
            for stats in output.source_stats:
                retrieval_calls.append(
                    RetrievalCallTrace(
                        origin_subquery=output.query,
                        source=stats.source,
                        adapted_query=stats.adapted_query,
                        adaptation_strategy=stats.adaptation_strategy,
                        cache_hit=stats.cache_hit,
                        run_dedupe_hit=stats.run_dedupe_hit,
                        source_skipped_reason=stats.source_skipped_reason,
                        remaining_subquery_count=stats.remaining_subquery_count,
                        returned_count=stats.returned_count,
                        request_count=stats.diagnostics.request_count,
                        error_count=stats.diagnostics.error_count,
                    )
                )
                for paper in stats.diagnostic_papers:
                    traced_paper = True
                    self._register(
                        paper,
                        CandidateProvenance(
                            origin_kind=origin_kind,
                            origin_stage=stage,
                            origin_subquery=output.query,
                            source=stats.source,
                            adapted_query=stats.adapted_query,
                            adaptation_strategy=stats.adaptation_strategy,
                            cache_hit=stats.cache_hit,
                            source_skipped_reason=stats.source_skipped_reason,
                        ),
                    )
                    papers.append(paper)
            if not traced_paper:
                for paper in output.papers:
                    sources = _stable_strings(
                        paper.sources or output.requested_sources
                    )
                    for source in sources:
                        self._register(
                            paper,
                            CandidateProvenance(
                                origin_kind=origin_kind,
                                origin_stage=stage,
                                origin_subquery=output.query,
                                source=source,
                            ),
                        )
                    papers.append(paper)
        self.snapshots.append(
            StageCandidateSnapshot(
                stage=stage,
                candidates=[self._paper_candidate(paper) for paper in papers],
                retrieval_calls=retrieval_calls,
            )
        )

    def register_refchain(
        self,
        stage: str,
        papers: list[Paper],
    ) -> None:
        if not self.enabled:
            return
        for paper in papers:
            for source in _stable_strings(paper.sources or ["openalex"]):
                self._register(
                    paper,
                    CandidateProvenance(
                        origin_kind="refchain",
                        origin_stage=stage,
                        origin_subquery="refchain",
                        source=source,
                    ),
                )
        self.snapshot_papers(stage, papers)

    def snapshot_papers(self, stage: str, papers: list[Paper]) -> None:
        if not self.enabled:
            return
        self.snapshots.append(
            StageCandidateSnapshot(
                stage=stage,
                candidates=[self._paper_candidate(paper) for paper in papers],
            )
        )

    def snapshot_judgements(
        self,
        stage: str,
        judgements: list[JudgementResult],
    ) -> None:
        if not self.enabled:
            return
        candidates: list[DiagnosticCandidate] = []
        for judgement in judgements:
            self._judgements.append(
                _TrackedJudgement(
                    paper=judgement.paper.model_copy(deep=True),
                    score=judgement.score,
                )
            )
            base = self._paper_candidate(judgement.paper)
            candidates.append(
                base.model_copy(
                    update={
                        "judgement_score": judgement.score,
                        "category": judgement.category,
                        "matched_terms": list(judgement.matched_terms),
                        "warnings": list(judgement.warnings),
                    }
                )
            )
        self.snapshots.append(StageCandidateSnapshot(stage=stage, candidates=candidates))

    def snapshot_ranked(
        self,
        stage: str,
        ranked_papers: list[RankedPaper],
    ) -> None:
        if not self.enabled:
            return
        candidates: list[DiagnosticCandidate] = []
        for ranked in ranked_papers:
            base = self._paper_candidate(ranked.paper)
            judgement_score = next(
                (
                    item.score
                    for item in reversed(self._judgements)
                    if _same_candidate(item.paper, ranked.paper)
                ),
                None,
            )
            candidates.append(
                base.model_copy(
                    update={
                        "rank": ranked.rank,
                        "judgement_score": judgement_score,
                        "category": ranked.category,
                        "final_score": ranked.final_score,
                        "matched_terms": list(ranked.matched_terms),
                        "warnings": list(ranked.warnings),
                    }
                )
            )
        self.snapshots.append(StageCandidateSnapshot(stage=stage, candidates=candidates))

    def skip(self, stage: str, reason: str) -> None:
        if not self.enabled:
            return
        self.snapshots.append(
            StageCandidateSnapshot(
                stage=stage,
                status="skipped",
                skipped_reason=reason,
            )
        )

    def _register(self, paper: Paper, provenance: CandidateProvenance) -> None:
        for tracked in self._tracked:
            if not _same_candidate(tracked.paper, paper):
                continue
            tracked.paper = deduplicate_papers([tracked.paper, paper])[0]
            if provenance not in tracked.provenance:
                tracked.provenance.append(provenance)
            return
        self._tracked.append(
            _TrackedCandidate(
                paper=paper.model_copy(deep=True),
                provenance=[provenance],
            )
        )

    def _paper_candidate(self, paper: Paper) -> DiagnosticCandidate:
        provenance: list[CandidateProvenance] = []
        for tracked in self._tracked:
            if _same_candidate(tracked.paper, paper):
                provenance.extend(tracked.provenance)
        provenance = _stable_provenance(provenance)
        return DiagnosticCandidate(
            identifiers=paper.identifiers.model_copy(deep=True),
            title=paper.title,
            year=paper.year,
            sources=_stable_strings(
                [*paper.sources, *(item.source for item in provenance)]
            ),
            provenance=provenance,
        )


def _same_candidate(left: Paper, right: Paper) -> bool:
    return len(deduplicate_papers([left, right])) == 1


def _stable_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.strip().casefold()
        if not key or key in seen:
            continue
        result.append(value)
        seen.add(key)
    return result


def _stable_provenance(
    values: list[CandidateProvenance],
) -> list[CandidateProvenance]:
    result: list[CandidateProvenance] = []
    seen: set[tuple[str, str, str, str, str | None, str | None, bool, str | None]] = set()
    for value in values:
        key = (
            value.origin_kind,
            value.origin_stage,
            value.origin_subquery,
            value.source,
            value.adapted_query,
            value.adaptation_strategy,
            value.cache_hit,
            value.source_skipped_reason,
        )
        if key in seen:
            continue
        result.append(value)
        seen.add(key)
    return result

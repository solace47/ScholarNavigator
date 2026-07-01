"""Rule-based reranking for judged papers."""

from __future__ import annotations

import math
from datetime import date

from scholar_agent.core.paper_schemas import Paper
from scholar_agent.core.search_schemas import (
    JudgementCategory,
    JudgementResult,
    QueryAnalysis,
    RankedPaper,
    RerankScoreBreakdown,
)


CATEGORY_TIER: dict[str, int] = {
    "highly_relevant": 0,
    "partially_relevant": 1,
    "weakly_relevant": 2,
    "irrelevant": 3,
    "insufficient_evidence": 4,
}

CATEGORY_MULTIPLIER: dict[str, float] = {
    "highly_relevant": 1.0,
    "partially_relevant": 0.92,
    "weakly_relevant": 0.82,
    "irrelevant": 0.45,
    "insufficient_evidence": 0.25,
}


class RerankerAgent:
    """Deterministic metadata-only reranker."""

    def rerank(
        self,
        query_analysis: QueryAnalysis,
        judged_papers: list[JudgementResult],
        *,
        top_k: int = 20,
    ) -> list[RankedPaper]:
        if top_k <= 0:
            return []

        scored = [
            _score_judgement(query_analysis, judgement, original_index)
            for original_index, judgement in enumerate(judged_papers)
        ]
        scored.sort(key=_sort_key)
        return [
            _to_ranked_paper(rank=index + 1, scored=scored_item)
            for index, scored_item in enumerate(scored[:top_k])
        ]


def rerank_papers(
    query_analysis: QueryAnalysis,
    judged_papers: list[JudgementResult],
    *,
    top_k: int = 20,
) -> list[RankedPaper]:
    """Rerank judged papers using deterministic metadata signals."""

    return RerankerAgent().rerank(query_analysis, judged_papers, top_k=top_k)


class _ScoredJudgement:
    def __init__(
        self,
        *,
        judgement: JudgementResult,
        original_index: int,
        score_breakdown: RerankScoreBreakdown,
        ranking_reason: str,
    ) -> None:
        self.judgement = judgement
        self.original_index = original_index
        self.score_breakdown = score_breakdown
        self.ranking_reason = ranking_reason


def _score_judgement(
    query_analysis: QueryAnalysis,
    judgement: JudgementResult,
    original_index: int,
) -> _ScoredJudgement:
    paper = judgement.paper
    weights = _weights_for_intent(query_analysis.intent)
    relevance_score = _clamp(judgement.score)
    authority_score = _authority_score(query_analysis, paper)
    timeliness_score = _timeliness_score(query_analysis, paper)
    metadata_score = _metadata_score(paper)
    base_score = (
        relevance_score * weights["relevance"]
        + authority_score * weights["authority"]
        + timeliness_score * weights["timeliness"]
        + metadata_score * weights["metadata"]
    )
    final_score = round(
        _clamp(base_score * CATEGORY_MULTIPLIER.get(judgement.category, 0.5)),
        4,
    )
    breakdown = RerankScoreBreakdown(
        relevance_score=round(relevance_score, 4),
        authority_score=round(authority_score, 4),
        timeliness_score=round(timeliness_score, 4),
        metadata_score=round(metadata_score, 4),
        final_score=final_score,
        relevance_weight=weights["relevance"],
        authority_weight=weights["authority"],
        timeliness_weight=weights["timeliness"],
        metadata_weight=weights["metadata"],
    )
    return _ScoredJudgement(
        judgement=judgement,
        original_index=original_index,
        score_breakdown=breakdown,
        ranking_reason=_ranking_reason(query_analysis, judgement, breakdown),
    )


def _weights_for_intent(intent: str) -> dict[str, float]:
    if intent == "recent_progress":
        return {
            "relevance": 0.65,
            "authority": 0.08,
            "timeliness": 0.22,
            "metadata": 0.05,
        }
    if intent == "survey":
        return {
            "relevance": 0.62,
            "authority": 0.25,
            "timeliness": 0.08,
            "metadata": 0.05,
        }
    return {
        "relevance": 0.72,
        "authority": 0.13,
        "timeliness": 0.10,
        "metadata": 0.05,
    }


def _authority_score(query_analysis: QueryAnalysis, paper: Paper) -> float:
    citation_component = min(
        math.log10(max(paper.citation_count, 0) + 1) / math.log10(1001),
        1.0,
    ) * 0.65
    source_component = min(len(set(paper.sources)) / 3, 1.0) * 0.15
    identifier_component = min(_identifier_count(paper) / 5, 1.0) * 0.12
    venue_component = _venue_component(query_analysis, paper)
    return _clamp(
        citation_component
        + source_component
        + identifier_component
        + venue_component
    )


def _timeliness_score(query_analysis: QueryAnalysis, paper: Paper) -> float:
    if paper.year is None:
        return 0.25

    time_range = query_analysis.constraints.time_range
    if time_range is not None:
        start_year = time_range.start_year
        end_year = time_range.end_year
        if start_year is not None and paper.year < start_year:
            distance = start_year - paper.year
            return _clamp(0.35 - min(distance, 10) * 0.03)
        if end_year is not None and paper.year > end_year:
            distance = paper.year - end_year
            return _clamp(0.65 - min(distance, 10) * 0.04)
        if start_year is not None and end_year is not None and end_year > start_year:
            position = (paper.year - start_year) / (end_year - start_year)
            return _clamp(0.7 + position * 0.3)
        return 0.85

    current_year = date.today().year
    age = max(0, current_year - paper.year)
    return _clamp(1.0 - min(age, 12) / 12)


def _metadata_score(paper: Paper) -> float:
    score = 0.0
    if paper.title.strip():
        score += 0.2
    if paper.abstract.strip():
        score += 0.25
    if paper.year is not None:
        score += 0.15
    if paper.venue and paper.venue.strip():
        score += 0.15
    if paper.sources:
        score += 0.1
    score += min(_identifier_count(paper) / 5, 1.0) * 0.15
    return _clamp(score)


def _venue_component(query_analysis: QueryAnalysis, paper: Paper) -> float:
    venue = (paper.venue or "").strip()
    if not venue:
        return 0.0
    requested = query_analysis.constraints.venues
    if requested and any(item.casefold() in venue.casefold() for item in requested):
        return 0.12
    return 0.08


def _identifier_count(paper: Paper) -> int:
    identifiers = paper.identifiers
    return sum(
        1
        for value in (
            identifiers.doi,
            identifiers.arxiv_id,
            identifiers.semantic_scholar_id,
            identifiers.openalex_id,
            identifiers.pubmed_id,
        )
        if value
    )


def _sort_key(scored: _ScoredJudgement) -> tuple[object, ...]:
    paper = scored.judgement.paper
    return (
        CATEGORY_TIER.get(scored.judgement.category, 5),
        -scored.score_breakdown.final_score,
        -scored.score_breakdown.relevance_score,
        -max(paper.citation_count, 0),
        -(paper.year or 0),
        paper.title.casefold(),
        scored.original_index,
    )


def _to_ranked_paper(rank: int, scored: _ScoredJudgement) -> RankedPaper:
    judgement = scored.judgement
    return RankedPaper(
        rank=rank,
        paper=judgement.paper,
        final_score=scored.score_breakdown.final_score,
        category=judgement.category,
        score_breakdown=scored.score_breakdown,
        ranking_reason=scored.ranking_reason,
        evidence=judgement.evidence,
        matched_terms=judgement.matched_terms,
        warnings=judgement.warnings,
    )


def _ranking_reason(
    query_analysis: QueryAnalysis,
    judgement: JudgementResult,
    breakdown: RerankScoreBreakdown,
) -> str:
    paper = judgement.paper
    details = [
        f"category={judgement.category}",
        f"judgement_score={breakdown.relevance_score:.4f}",
        f"citation_count={max(paper.citation_count, 0)}",
        f"sources={len(set(paper.sources))}",
        f"identifiers={_identifier_count(paper)}",
    ]
    if paper.venue:
        details.append(f"venue={paper.venue}")
    if paper.year is not None:
        details.append(f"year={paper.year}")
    if query_analysis.intent == "recent_progress":
        details.append("recent_progress weights timeliness more heavily")
    if query_analysis.intent == "survey":
        details.append("survey weights authority more heavily")
    details.append(
        "final_score combines relevance, authority, timeliness, and metadata"
    )
    return "; ".join(details)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


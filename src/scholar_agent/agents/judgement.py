"""Rule-based relevance judgement for retrieved paper metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass

from scholar_agent.core.paper_schemas import Paper
from scholar_agent.core.search_schemas import (
    EvidenceItem,
    JudgementResult,
    QueryAnalysis,
    QueryConstraint,
    ResearchDomain,
)


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "based",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "latest",
    "of",
    "on",
    "paper",
    "papers",
    "recent",
    "research",
    "scientific",
    "search",
    "study",
    "studies",
    "survey",
    "the",
    "to",
    "using",
    "with",
}

DOMAIN_TERMS: dict[str, tuple[str, ...]] = {
    "machine_learning": (
        "llm",
        "large language model",
        "rag",
        "retrieval",
        "reranking",
        "transformer",
        "nlp",
        "machine learning",
        "deep learning",
        "neural",
        "embedding",
    ),
    "computer_science": (
        "algorithm",
        "database",
        "information retrieval",
        "retrieval",
        "software",
        "systems",
        "indexing",
    ),
    "biomedical": (
        "biomedical",
        "clinical",
        "gene",
        "genomic",
        "medicine",
        "patient",
        "protein",
        "therapy",
    ),
    "general_science": (
        "evidence",
        "literature",
        "method",
        "research",
        "science",
    ),
}


@dataclass(frozen=True)
class _Signal:
    score: float
    matched_terms: list[str]
    evidence: list[EvidenceItem]
    reasons: list[str]
    penalty: float = 0.0


class JudgementAgent:
    """Deterministic metadata-only relevance judgement."""

    def judge(
        self,
        query_analysis: QueryAnalysis,
        papers: list[Paper],
        *,
        threshold_high: float = 0.72,
        threshold_partial: float = 0.45,
        threshold_weak: float = 0.25,
    ) -> list[JudgementResult]:
        _validate_thresholds(threshold_high, threshold_partial, threshold_weak)
        return [
            _judge_one_paper(
                query_analysis,
                paper,
                threshold_high=threshold_high,
                threshold_partial=threshold_partial,
                threshold_weak=threshold_weak,
            )
            for paper in papers
        ]


def judge_papers(
    query_analysis: QueryAnalysis,
    papers: list[Paper],
    *,
    threshold_high: float = 0.72,
    threshold_partial: float = 0.45,
    threshold_weak: float = 0.25,
) -> list[JudgementResult]:
    """Judge paper relevance using deterministic metadata rules."""

    return JudgementAgent().judge(
        query_analysis,
        papers,
        threshold_high=threshold_high,
        threshold_partial=threshold_partial,
        threshold_weak=threshold_weak,
    )


def _judge_one_paper(
    query_analysis: QueryAnalysis,
    paper: Paper,
    *,
    threshold_high: float,
    threshold_partial: float,
    threshold_weak: float,
) -> JudgementResult:
    warnings = _metadata_warnings(query_analysis.constraints, paper)
    if not paper.title.strip() and not paper.abstract.strip():
        return JudgementResult(
            paper=paper,
            score=0.0,
            category="insufficient_evidence",
            reasoning=(
                "Both title and abstract are empty; metadata is insufficient "
                "for relevance judgement."
            ),
            evidence=[],
            matched_terms=[],
            warnings=warnings,
        )

    constraints = query_analysis.constraints
    keyword_terms = _query_terms(query_analysis)
    keyword_signal = _term_signal(
        terms=keyword_terms,
        paper=paper,
        title_weight=0.12,
        abstract_weight=0.06,
        max_score=0.45,
        reason_label="query terms",
    )
    must_signal = _term_signal(
        terms=constraints.must_include_terms,
        paper=paper,
        title_weight=0.09,
        abstract_weight=0.045,
        max_score=0.24,
        reason_label="required terms",
    )
    method_signal = _term_signal(
        terms=constraints.methods,
        paper=paper,
        title_weight=0.08,
        abstract_weight=0.04,
        max_score=0.18,
        reason_label="method terms",
    )
    dataset_signal = _term_signal(
        terms=constraints.datasets,
        paper=paper,
        title_weight=0.07,
        abstract_weight=0.035,
        max_score=0.12,
        reason_label="dataset terms",
    )
    domain_signal = _term_signal(
        terms=list(DOMAIN_TERMS.get(query_analysis.domain, ())),
        paper=paper,
        title_weight=0.05,
        abstract_weight=0.025,
        max_score=0.12,
        reason_label="domain terms",
    )
    venue_signal = _venue_signal(constraints, paper)
    time_signal = _time_signal(constraints, paper)

    score = (
        keyword_signal.score
        + must_signal.score
        + method_signal.score
        + dataset_signal.score
        + domain_signal.score
        + venue_signal.score
        + time_signal.score
        - venue_signal.penalty
        - time_signal.penalty
    )
    score = round(_clamp(score), 4)
    evidence = _dedupe_evidence(
        keyword_signal.evidence
        + must_signal.evidence
        + method_signal.evidence
        + dataset_signal.evidence
        + domain_signal.evidence
        + venue_signal.evidence
        + time_signal.evidence
    )
    matched_terms = _dedupe_terms(
        keyword_signal.matched_terms
        + must_signal.matched_terms
        + method_signal.matched_terms
        + dataset_signal.matched_terms
        + domain_signal.matched_terms
    )
    reasons = (
        keyword_signal.reasons
        + must_signal.reasons
        + method_signal.reasons
        + dataset_signal.reasons
        + domain_signal.reasons
        + venue_signal.reasons
        + time_signal.reasons
    )

    category = _category(
        score,
        threshold_high=threshold_high,
        threshold_partial=threshold_partial,
        threshold_weak=threshold_weak,
    )
    return JudgementResult(
        paper=paper,
        score=score,
        category=category,
        reasoning=_reasoning(reasons, evidence, warnings),
        evidence=evidence,
        matched_terms=matched_terms,
        warnings=warnings,
    )


def _metadata_warnings(constraints: QueryConstraint, paper: Paper) -> list[str]:
    warnings: list[str] = []
    if not paper.title.strip():
        warnings.append("missing_title")
    if not paper.abstract.strip():
        warnings.append("missing_abstract")
    if constraints.time_range is not None and paper.year is None:
        warnings.append("missing_year_for_time_range")
    return warnings


def _query_terms(query_analysis: QueryAnalysis) -> list[str]:
    terms = _tokenize(query_analysis.original_query)
    constraints = query_analysis.constraints
    terms.extend(constraints.must_include_terms)
    terms.extend(constraints.methods)
    terms.extend(constraints.datasets)
    return _dedupe_terms(terms)


def _term_signal(
    *,
    terms: list[str],
    paper: Paper,
    title_weight: float,
    abstract_weight: float,
    max_score: float,
    reason_label: str,
) -> _Signal:
    title = paper.title or ""
    abstract = paper.abstract or ""
    title_text = title.casefold()
    abstract_text = abstract.casefold()
    score = 0.0
    matched_terms: list[str] = []
    evidence: list[EvidenceItem] = []

    for term in _dedupe_terms(terms):
        normalized_term = term.casefold()
        if not normalized_term:
            continue
        if _contains_term(title_text, normalized_term):
            score += title_weight
            matched_terms.append(term)
            evidence.append(
                EvidenceItem(source="title", text=_short_text(title), confidence=0.9)
            )
        elif _contains_term(abstract_text, normalized_term):
            score += abstract_weight
            matched_terms.append(term)
            evidence.append(
                EvidenceItem(
                    source="abstract",
                    text=_abstract_snippet(abstract, normalized_term),
                    confidence=0.72,
                )
            )

    capped_score = min(score, max_score)
    reasons = []
    if matched_terms:
        reasons.append(
            f"matched {reason_label}: {', '.join(_dedupe_terms(matched_terms)[:8])}"
        )
    return _Signal(
        score=capped_score,
        matched_terms=_dedupe_terms(matched_terms),
        evidence=evidence,
        reasons=reasons,
    )


def _venue_signal(constraints: QueryConstraint, paper: Paper) -> _Signal:
    if not constraints.venues:
        return _Signal(score=0.0, matched_terms=[], evidence=[], reasons=[])

    venue = (paper.venue or "").strip()
    if not venue:
        return _Signal(
            score=0.0,
            matched_terms=[],
            evidence=[],
            reasons=["venue constraint present but paper venue is missing"],
            penalty=0.03,
        )

    venue_key = venue.casefold()
    for expected in constraints.venues:
        if expected.casefold() in venue_key:
            return _Signal(
                score=0.1,
                matched_terms=[],
                evidence=[EvidenceItem(source="venue", text=venue, confidence=0.92)],
                reasons=[f"venue matches constraint: {expected}"],
            )
    return _Signal(
        score=0.0,
        matched_terms=[],
        evidence=[],
        reasons=["paper venue does not match requested venues"],
        penalty=0.03,
    )


def _time_signal(constraints: QueryConstraint, paper: Paper) -> _Signal:
    time_range = constraints.time_range
    if time_range is None:
        return _Signal(score=0.0, matched_terms=[], evidence=[], reasons=[])
    if paper.year is None:
        return _Signal(
            score=0.0,
            matched_terms=[],
            evidence=[],
            reasons=["time range present but paper year is missing"],
        )

    start_year = time_range.start_year
    end_year = time_range.end_year
    if start_year is not None and paper.year < start_year:
        distance = start_year - paper.year
        penalty = 0.15 if distance >= 2 else 0.08
        return _Signal(
            score=0.0,
            matched_terms=[],
            evidence=[EvidenceItem(source="metadata", text=f"year={paper.year}", confidence=0.85)],
            reasons=[f"paper year {paper.year} is earlier than requested start year {start_year}"],
            penalty=penalty,
        )
    if end_year is not None and paper.year > end_year:
        return _Signal(
            score=0.0,
            matched_terms=[],
            evidence=[EvidenceItem(source="metadata", text=f"year={paper.year}", confidence=0.85)],
            reasons=[f"paper year {paper.year} is later than requested end year {end_year}"],
            penalty=0.06,
        )
    return _Signal(
        score=0.08,
        matched_terms=[],
        evidence=[EvidenceItem(source="metadata", text=f"year={paper.year}", confidence=0.88)],
        reasons=[f"paper year {paper.year} satisfies time constraint"],
    )


def _category(
    score: float,
    *,
    threshold_high: float,
    threshold_partial: float,
    threshold_weak: float,
) -> str:
    if score >= threshold_high:
        return "highly_relevant"
    if score >= threshold_partial:
        return "partially_relevant"
    if score >= threshold_weak:
        return "weakly_relevant"
    return "irrelevant"


def _validate_thresholds(
    threshold_high: float,
    threshold_partial: float,
    threshold_weak: float,
) -> None:
    if not 0 <= threshold_weak <= threshold_partial <= threshold_high <= 1:
        raise ValueError(
            "thresholds must satisfy "
            "0 <= threshold_weak <= threshold_partial <= threshold_high <= 1"
        )


def _reasoning(
    reasons: list[str],
    evidence: list[EvidenceItem],
    warnings: list[str],
) -> str:
    if not evidence:
        base = "No title, abstract, venue, or metadata evidence matched the query."
    else:
        base = "; ".join(_dedupe_terms(reasons)) or "Metadata evidence matched the query."
    if warnings:
        return f"{base} Warnings: {', '.join(warnings)}."
    return base


def _tokenize(text: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9+.#-]*", text):
        normalized = token.strip(".,;:()[]{}").casefold()
        if not normalized or normalized in STOPWORDS:
            continue
        if len(normalized) <= 2 and normalized not in {"ai", "ml", "cv"}:
            continue
        terms.append(_canonical_term(token))

    if "大模型" in text:
        terms.append("LLM")
    if "检索增强" in text:
        terms.append("RAG")
    if "检索" in text:
        terms.append("retrieval")
    if "重排序" in text:
        terms.append("reranking")
    if "数据集" in text:
        terms.append("dataset")
    if "评测" in text:
        terms.append("benchmark")
    return _dedupe_terms(terms)


def _canonical_term(term: str) -> str:
    upper_terms = {"ai", "cv", "llm", "ml", "nlp", "rag"}
    clean = term.strip()
    if clean.casefold() in upper_terms:
        return clean.upper()
    return clean


def _contains_term(text: str, term: str) -> bool:
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9+.#-]+", term):
        return re.search(rf"(?<![a-z0-9+.#-]){re.escape(term)}(?![a-z0-9+.#-])", text) is not None
    return term in text


def _abstract_snippet(abstract: str, term: str) -> str:
    sentences = re.split(r"(?<=[.!?。！？])\s+", abstract.strip())
    for sentence in sentences:
        if term in sentence.casefold():
            return _short_text(sentence)
    return _short_text(abstract)


def _short_text(text: str, limit: int = 160) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip()


def _dedupe_terms(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        key = item.casefold()
        if not item or key in seen:
            continue
        deduped.append(item)
        seen.add(key)
    return deduped


def _dedupe_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    deduped: list[EvidenceItem] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.source, item.text.casefold())
        if key in seen:
            continue
        deduped.append(item)
        seen.add(key)
    return deduped[:12]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))

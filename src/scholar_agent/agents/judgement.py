"""Rule-based relevance judgement for retrieved paper metadata."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from scholar_agent.core.paper_schemas import Paper
from scholar_agent.core.search_schemas import (
    EvidenceItem,
    JudgementCategory,
    JudgementResult,
    QueryAnalysis,
    QueryConstraint,
    ResearchDomain,
)
from scholar_agent.llm.provider import chat_json as provider_chat_json
from scholar_agent.llm.provider import is_llm_enabled
from scholar_agent.prompts.loader import PromptLoadError, render_messages


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
PAPER_TYPE_TERMS: dict[str, tuple[str, ...]] = {
    "survey": ("survey",),
    "review": ("review", "literature review", "systematic review"),
    "method": ("method", "methodology", "approach", "framework"),
    "benchmark": ("benchmark", "evaluation"),
    "dataset": ("dataset", "data set", "corpus"),
    "application": ("application", "applied", "deployment"),
    "comparison": ("comparison", "comparative", "versus"),
}
JUDGEMENT_CATEGORY_ORDER = {
    "highly_relevant": 0,
    "partially_relevant": 1,
    "weakly_relevant": 2,
    "irrelevant": 3,
    "insufficient_evidence": 4,
}

LLM_JUDGEMENT_BATCH_SIZE_ENV = "SCHOLAR_AGENT_LLM_JUDGEMENT_BATCH_SIZE"
LLM_JUDGEMENT_MAX_PAPERS_ENV = "SCHOLAR_AGENT_LLM_JUDGEMENT_MAX_PAPERS"
LLM_JUDGEMENT_TIMEOUT_SECONDS_ENV = "SCHOLAR_AGENT_LLM_JUDGEMENT_TIMEOUT_SECONDS"
DEFAULT_LLM_JUDGEMENT_BATCH_SIZE = 8
DEFAULT_LLM_JUDGEMENT_MAX_PAPERS = 8
DEFAULT_LLM_JUDGEMENT_TIMEOUT_SECONDS = 25.0
MIN_LLM_JUDGEMENT_BATCH_SIZE = 1
MAX_LLM_JUDGEMENT_BATCH_SIZE = 20
JUDGEMENT_CATEGORIES: set[str] = {
    "highly_relevant",
    "partially_relevant",
    "weakly_relevant",
    "irrelevant",
    "insufficient_evidence",
}
EVIDENCE_SOURCES: set[str] = {"title", "abstract", "venue", "metadata"}


@dataclass(frozen=True)
class _Signal:
    score: float
    matched_terms: list[str]
    evidence: list[EvidenceItem]
    reasons: list[str]
    penalty: float = 0.0


class LLMJsonClient(Protocol):
    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        ...


class JudgementAgent:
    """Metadata-only relevance judgement with optional LLM JSON enhancement."""

    def __init__(self, llm_client: LLMJsonClient | None = None) -> None:
        self._llm_client = llm_client
        self.llm_call_count = 0

    def judge(
        self,
        query_analysis: QueryAnalysis,
        papers: list[Paper],
        *,
        threshold_high: float = 0.72,
        threshold_partial: float = 0.45,
        threshold_weak: float = 0.25,
        use_llm: bool | None = None,
        before_llm_batch: Callable[[], None] | None = None,
    ) -> list[JudgementResult]:
        _validate_thresholds(threshold_high, threshold_partial, threshold_weak)
        if use_llm:
            results = self._judge_with_optional_llm(
                query_analysis,
                papers,
                threshold_high=threshold_high,
                threshold_partial=threshold_partial,
                threshold_weak=threshold_weak,
                before_llm_batch=before_llm_batch,
            )
        else:
            results = _judge_papers_rules(
                query_analysis,
                papers,
                threshold_high=threshold_high,
                threshold_partial=threshold_partial,
                threshold_weak=threshold_weak,
            )
        return [
            _enforce_constraint_outcomes(
                query_analysis,
                result,
                threshold_high=threshold_high,
                threshold_partial=threshold_partial,
                threshold_weak=threshold_weak,
            )
            for result in results
        ]

    def _judge_with_optional_llm(
        self,
        query_analysis: QueryAnalysis,
        papers: list[Paper],
        *,
        threshold_high: float,
        threshold_partial: float,
        threshold_weak: float,
        before_llm_batch: Callable[[], None] | None,
    ) -> list[JudgementResult]:
        rule_results = _judge_papers_rules(
            query_analysis,
            papers,
            threshold_high=threshold_high,
            threshold_partial=threshold_partial,
            threshold_weak=threshold_weak,
        )
        if not papers:
            return []
        if self._llm_client is None and not is_llm_enabled():
            return _with_warning(rule_results, "llm_judgement_disabled")

        batch_size = llm_judgement_batch_size_from_env()
        llm_limit = min(len(papers), llm_judgement_max_papers_from_env())
        llm_timeout = llm_judgement_timeout_seconds_from_env()
        results: list[JudgementResult] = list(rule_results)
        for skipped_index in range(llm_limit, len(papers)):
            results[skipped_index] = _with_warning(
                [results[skipped_index]],
                f"llm_judgement_skipped_by_limit:{skipped_index}",
            )[0]

        for start in range(0, llm_limit, batch_size):
            if before_llm_batch is not None:
                before_llm_batch()
            end = min(start + batch_size, llm_limit)
            batch_papers = papers[start:end]
            batch_rule_results = rule_results[start:end]
            try:
                messages = _build_llm_judgement_messages(
                    query_analysis,
                    batch_papers,
                    start_index=start,
                )
            except PromptLoadError:
                results[start:end] = _with_warning(
                    batch_rule_results,
                    "llm_judgement_prompt_load_failed",
                )
                continue
            try:
                self.llm_call_count += 1
                raw_response = _chat_json(
                    self._llm_client,
                    messages,
                    timeout=llm_timeout,
                )
                batch_results = _judgement_results_from_llm_json(
                    raw_response,
                    query_analysis=query_analysis,
                    papers=batch_papers,
                    rule_results=batch_rule_results,
                    start_index=start,
                )
            except Exception as exc:  # noqa: BLE001 - isolate one failed LLM batch
                batch_results = _with_warning(
                    batch_rule_results,
                    f"llm_judgement_failed:{_diagnostic_message(exc)}",
                )
            results[start:end] = batch_results
        return results


def judge_papers(
    query_analysis: QueryAnalysis,
    papers: list[Paper],
    *,
    threshold_high: float = 0.72,
    threshold_partial: float = 0.45,
    threshold_weak: float = 0.25,
    use_llm: bool | None = None,
    llm_client: LLMJsonClient | None = None,
    before_llm_batch: Callable[[], None] | None = None,
) -> list[JudgementResult]:
    """Judge paper relevance using metadata rules or optional LLM JSON."""

    return JudgementAgent(llm_client=llm_client).judge(
        query_analysis,
        papers,
        threshold_high=threshold_high,
        threshold_partial=threshold_partial,
        threshold_weak=threshold_weak,
        use_llm=use_llm,
        before_llm_batch=before_llm_batch,
    )


def llm_judgement_batch_size_from_env() -> int:
    import os

    raw_value = os.getenv(LLM_JUDGEMENT_BATCH_SIZE_ENV)
    if raw_value is None:
        return DEFAULT_LLM_JUDGEMENT_BATCH_SIZE
    try:
        value = int(raw_value)
    except ValueError:
        return DEFAULT_LLM_JUDGEMENT_BATCH_SIZE
    return max(MIN_LLM_JUDGEMENT_BATCH_SIZE, min(MAX_LLM_JUDGEMENT_BATCH_SIZE, value))


def llm_judgement_max_papers_from_env() -> int:
    import os

    raw_value = os.getenv(LLM_JUDGEMENT_MAX_PAPERS_ENV)
    if raw_value is None:
        return DEFAULT_LLM_JUDGEMENT_MAX_PAPERS
    try:
        value = int(raw_value)
    except ValueError:
        return DEFAULT_LLM_JUDGEMENT_MAX_PAPERS
    return value if value >= 1 else DEFAULT_LLM_JUDGEMENT_MAX_PAPERS


def llm_judgement_timeout_seconds_from_env() -> float:
    import os

    raw_value = os.getenv(LLM_JUDGEMENT_TIMEOUT_SECONDS_ENV)
    if raw_value is None:
        return DEFAULT_LLM_JUDGEMENT_TIMEOUT_SECONDS
    try:
        value = float(raw_value)
    except ValueError:
        return DEFAULT_LLM_JUDGEMENT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_LLM_JUDGEMENT_TIMEOUT_SECONDS


def _judge_papers_rules(
    query_analysis: QueryAnalysis,
    papers: list[Paper],
    *,
    threshold_high: float,
    threshold_partial: float,
    threshold_weak: float,
) -> list[JudgementResult]:
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
    paper_type_signal = _paper_type_signal(constraints, paper)
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
        + paper_type_signal.score
        + domain_signal.score
        + venue_signal.score
        + time_signal.score
        - venue_signal.penalty
        - time_signal.penalty
        - paper_type_signal.penalty
    )
    evidence = _dedupe_evidence(
        keyword_signal.evidence
        + must_signal.evidence
        + method_signal.evidence
        + dataset_signal.evidence
        + paper_type_signal.evidence
        + domain_signal.evidence
        + venue_signal.evidence
        + time_signal.evidence
    )
    matched_terms = _dedupe_terms(
        keyword_signal.matched_terms
        + must_signal.matched_terms
        + method_signal.matched_terms
        + dataset_signal.matched_terms
        + paper_type_signal.matched_terms
        + domain_signal.matched_terms
    )
    reasons = (
        keyword_signal.reasons
        + must_signal.reasons
        + method_signal.reasons
        + dataset_signal.reasons
        + paper_type_signal.reasons
        + domain_signal.reasons
        + venue_signal.reasons
        + time_signal.reasons
    )
    score, coverage_reasons = _constraint_coverage_adjustment(
        query_analysis,
        paper,
        score,
    )
    reasons.extend(coverage_reasons)
    score = round(_clamp(score), 4)

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


def _constraint_coverage_adjustment(
    query_analysis: QueryAnalysis,
    paper: Paper,
    score: float,
) -> tuple[float, list[str]]:
    constraints = query_analysis.constraints
    topic_terms = _tokenize(query_analysis.original_query)
    topic_matches, _ = _matching_constraint_terms(topic_terms, paper)
    method_matches, _ = _matching_constraint_terms(constraints.methods, paper)
    dataset_matches, _ = _matching_constraint_terms(constraints.datasets, paper)
    must_matches, _ = _matching_constraint_terms(
        constraints.must_include_terms,
        paper,
    )
    paper_type_matches, _ = _matched_paper_types(constraints.paper_types, paper)
    excluded_matches, _ = _matching_constraint_terms(constraints.exclude_terms, paper)

    coverages: list[tuple[str, float]] = []
    if topic_terms:
        coverages.append(("topic", _coverage_ratio(topic_terms, topic_matches)))
    if constraints.methods:
        coverages.append(
            ("method", _coverage_ratio(constraints.methods, method_matches))
        )
    if constraints.datasets:
        coverages.append(
            ("dataset", _coverage_ratio(constraints.datasets, dataset_matches))
        )

    topic_keys = {term.casefold() for term in topic_terms}
    must_keys = {term.casefold() for term in constraints.must_include_terms}
    if constraints.must_include_terms and (
        "must_include_terms" in constraints.explicit_fields or must_keys != topic_keys
    ):
        coverages.append(
            (
                "must_have",
                _coverage_ratio(constraints.must_include_terms, must_matches),
            )
        )
    if constraints.paper_types:
        coverages.append(
            (
                "paper_type",
                _coverage_ratio(constraints.paper_types, paper_type_matches),
            )
        )
    if constraints.venues:
        venue_key = _normalize_venue(paper.venue or "")
        venue_match = bool(venue_key) and any(
            _normalize_venue(expected) in venue_key
            for expected in constraints.venues
        )
        coverages.append(("venue", 1.0 if venue_match else 0.0))
    if constraints.time_range is not None:
        time_match = paper.year is not None and not _outside_time_range(
            constraints,
            paper,
        )
        coverages.append(("time", 1.0 if time_match else 0.0))

    reasons = [
        "constraint_coverage:"
        + ",".join(f"{name}={coverage:.2f}" for name, coverage in coverages)
        + f",excluded_term_match={'true' if excluded_matches else 'false'}"
    ]
    if not coverages:
        return score, reasons

    strong_dimensions = sum(coverage >= 0.9 for _, coverage in coverages)
    mean_coverage = sum(coverage for _, coverage in coverages) / len(coverages)
    broad_topic_only = len(topic_terms) >= 3 and len(topic_matches) <= 1

    if len(coverages) >= 2 and strong_dimensions >= 2 and mean_coverage >= 0.6:
        score += min(0.08, strong_dimensions * 0.02)
        reasons.append("multi_dimension_constraint_coverage")
    elif len(coverages) >= 2 and strong_dimensions <= 1:
        score = min(score - 0.06, 0.5 + 0.25 * mean_coverage)
        reasons.append("insufficient_multi_dimension_coverage")

    if broad_topic_only and strong_dimensions <= 1:
        score = min(score, 0.68)
        reasons.append("broad_topic_match_only")
    return score, reasons


def _coverage_ratio(expected: list[str], matched: list[str]) -> float:
    expected_keys = {item.casefold() for item in expected if item.strip()}
    if not expected_keys:
        return 0.0
    matched_keys = {item.casefold() for item in matched if item.strip()}
    return len(expected_keys & matched_keys) / len(expected_keys)


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


def _paper_type_signal(constraints: QueryConstraint, paper: Paper) -> _Signal:
    if not constraints.paper_types:
        return _Signal(score=0.0, matched_terms=[], evidence=[], reasons=[])
    matched_types, evidence = _matched_paper_types(constraints.paper_types, paper)
    if not matched_types:
        return _Signal(
            score=0.0,
            matched_terms=[],
            evidence=[],
            reasons=[
                "paper type does not match requested types: "
                + ", ".join(constraints.paper_types)
            ],
            penalty=0.08,
        )
    return _Signal(
        score=min(0.08 * len(matched_types), 0.16),
        matched_terms=matched_types,
        evidence=evidence,
        reasons=["matched paper types: " + ", ".join(matched_types)],
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

    venue_key = _normalize_venue(venue)
    for expected in constraints.venues:
        if _normalize_venue(expected) in venue_key:
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


def _enforce_constraint_outcomes(
    query_analysis: QueryAnalysis,
    result: JudgementResult,
    *,
    threshold_high: float,
    threshold_partial: float,
    threshold_weak: float,
) -> JudgementResult:
    constraints = query_analysis.constraints
    score = result.score
    category = result.category
    warnings = list(result.warnings)
    evidence = list(result.evidence)
    matched_terms = list(result.matched_terms)
    reasoning_additions: list[str] = []

    excluded_matches, excluded_evidence = _matching_constraint_terms(
        constraints.exclude_terms,
        result.paper,
    )
    if excluded_matches:
        warning = "excluded_terms_matched:" + ",".join(excluded_matches)
        warnings.append(warning)
        reasoning_additions.append(
            "excluded terms matched: " + ", ".join(excluded_matches)
        )
        return result.model_copy(
            update={
                "score": 0.0,
                "category": "irrelevant",
                "reasoning": _append_reasoning(
                    result.reasoning,
                    reasoning_additions,
                ),
                "evidence": _dedupe_evidence([*evidence, *excluded_evidence]),
                "matched_terms": _dedupe_terms(
                    [*matched_terms, *excluded_matches]
                ),
                "warnings": _dedupe_terms(warnings),
            }
        )

    if "must_include_terms" in constraints.explicit_fields:
        matched_required, required_evidence = _matching_constraint_terms(
            constraints.must_include_terms,
            result.paper,
        )
        matched_keys = {item.casefold() for item in matched_required}
        missing_required = [
            term
            for term in constraints.must_include_terms
            if term.casefold() not in matched_keys
        ]
        evidence.extend(required_evidence)
        matched_terms.extend(matched_required)
        if missing_required:
            warnings.append(
                "missing_must_have_terms:" + ",".join(missing_required)
            )
            reasoning_additions.append(
                "missing required terms: " + ", ".join(missing_required)
            )
            score = min(score, max(0.0, threshold_high - 0.0001))
            category = _worse_category(category, "partially_relevant")

    if constraints.datasets:
        matched_datasets, dataset_evidence = _matching_constraint_terms(
            constraints.datasets,
            result.paper,
        )
        evidence.extend(dataset_evidence)
        matched_terms.extend(matched_datasets)
        if (
            "datasets" in constraints.explicit_fields
            and not matched_datasets
        ):
            warnings.append(
                "dataset_terms_not_matched:" + ",".join(constraints.datasets)
            )
            reasoning_additions.append(
                "requested datasets not matched: " + ", ".join(constraints.datasets)
            )
            score = max(0.0, score - 0.08)
            category = _worse_category(
                category,
                _category(
                    score,
                    threshold_high=threshold_high,
                    threshold_partial=threshold_partial,
                    threshold_weak=threshold_weak,
                ),
            )

    if constraints.paper_types:
        matched_types, type_evidence = _matched_paper_types(
            constraints.paper_types,
            result.paper,
        )
        evidence.extend(type_evidence)
        matched_terms.extend(matched_types)
        if not matched_types:
            warnings.append(
                "paper_types_not_matched:" + ",".join(constraints.paper_types)
            )
            reasoning_additions.append(
                "requested paper types not matched: "
                + ", ".join(constraints.paper_types)
            )
            score = max(0.0, score - 0.08)
            category = _worse_category(
                category,
                _category(
                    score,
                    threshold_high=threshold_high,
                    threshold_partial=threshold_partial,
                    threshold_weak=threshold_weak,
                ),
            )

    if _outside_time_range(constraints, result.paper):
        warnings.append(f"outside_time_range:{result.paper.year}")
        reasoning_additions.append(
            f"paper year {result.paper.year} is outside the requested time range"
        )
        score = min(score, max(0.0, threshold_high - 0.0001))
        category = _worse_category(category, "partially_relevant")

    return result.model_copy(
        update={
            "score": round(_clamp(score), 4),
            "category": category,
            "reasoning": _append_reasoning(result.reasoning, reasoning_additions),
            "evidence": _dedupe_evidence(evidence),
            "matched_terms": _dedupe_terms(matched_terms),
            "warnings": _dedupe_terms(warnings),
        }
    )


def _matching_constraint_terms(
    terms: list[str],
    paper: Paper,
) -> tuple[list[str], list[EvidenceItem]]:
    title = paper.title or ""
    abstract = paper.abstract or ""
    title_key = title.casefold()
    abstract_key = abstract.casefold()
    matched: list[str] = []
    evidence: list[EvidenceItem] = []
    for term in _dedupe_terms(terms):
        normalized = term.casefold()
        if _contains_term(title_key, normalized):
            matched.append(term)
            evidence.append(
                EvidenceItem(source="title", text=_short_text(title), confidence=0.92)
            )
        elif _contains_term(abstract_key, normalized):
            matched.append(term)
            evidence.append(
                EvidenceItem(
                    source="abstract",
                    text=_abstract_snippet(abstract, normalized),
                    confidence=0.8,
                )
            )
    return _dedupe_terms(matched), _dedupe_evidence(evidence)


def _matched_paper_types(
    paper_types: list[str],
    paper: Paper,
) -> tuple[list[str], list[EvidenceItem]]:
    matched: list[str] = []
    evidence: list[EvidenceItem] = []
    for paper_type in paper_types:
        aliases = list(PAPER_TYPE_TERMS.get(paper_type, (paper_type,)))
        alias_matches, alias_evidence = _matching_constraint_terms(aliases, paper)
        if not alias_matches:
            continue
        matched.append(paper_type)
        evidence.extend(alias_evidence)
    return _dedupe_terms(matched), _dedupe_evidence(evidence)


def _outside_time_range(constraints: QueryConstraint, paper: Paper) -> bool:
    time_range = constraints.time_range
    if time_range is None or paper.year is None:
        return False
    if time_range.start_year is not None and paper.year < time_range.start_year:
        return True
    return time_range.end_year is not None and paper.year > time_range.end_year


def _worse_category(current: str, candidate: str) -> str:
    if JUDGEMENT_CATEGORY_ORDER.get(candidate, 5) > JUDGEMENT_CATEGORY_ORDER.get(
        current,
        5,
    ):
        return candidate
    return current


def _append_reasoning(reasoning: str, additions: list[str]) -> str:
    if not additions:
        return reasoning
    base = reasoning.rstrip().rstrip(".")
    suffix = "; ".join(_dedupe_terms(additions))
    return f"{base}; {suffix}."


def _normalize_venue(value: str) -> str:
    return re.sub(r"[\s_-]+", "", value.casefold())


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


def _chat_json(
    llm_client: LLMJsonClient | None,
    messages: list[dict[str, str]],
    *,
    timeout: float,
) -> dict[str, Any]:
    if llm_client is not None:
        return llm_client.chat_json(messages, temperature=0, timeout=timeout)
    return provider_chat_json(messages, temperature=0, timeout=timeout)


def _build_llm_judgement_messages(
    query_analysis: QueryAnalysis,
    papers: list[Paper],
    *,
    start_index: int,
) -> list[dict[str, str]]:
    payload = {
        "query_analysis": {
            "original_query": query_analysis.original_query,
            "language": query_analysis.language,
            "intent": query_analysis.intent,
            "domain": query_analysis.domain,
            "constraints": query_analysis.constraints.model_dump(mode="json"),
        },
        "papers": [
            {
                "paper_index": start_index + index,
                "title": paper.title,
                "authors": paper.authors,
                "year": paper.year,
                "venue": paper.venue,
                "abstract": _short_text(paper.abstract, limit=1200),
                "identifiers": paper.identifiers.model_dump(mode="json"),
                "sources": paper.sources,
                "citation_count": paper.citation_count,
            }
            for index, paper in enumerate(papers)
        ],
    }
    return render_messages("relevance_judgement", payload)


def _judgement_results_from_llm_json(
    raw_response: dict[str, Any],
    *,
    query_analysis: QueryAnalysis,
    papers: list[Paper],
    rule_results: list[JudgementResult],
    start_index: int,
) -> list[JudgementResult]:
    raw_judgements = raw_response.get("judgements")
    if not isinstance(raw_judgements, list):
        raise ValueError("llm_judgement_missing_judgements")

    global_warnings = _coerce_string_list(raw_response.get("warnings"))
    by_global_index: dict[int, dict[str, Any]] = {}
    invalid_warnings_by_index: dict[int, list[str]] = {}
    for raw_item in raw_judgements:
        if not isinstance(raw_item, dict):
            _append_invalid_warning(
                invalid_warnings_by_index,
                start_index,
                "llm_judgement_invalid_item",
            )
            continue
        parsed_index = _parse_llm_paper_index(
            raw_item.get("paper_index"),
            start_index=start_index,
            batch_size=len(papers),
        )
        if parsed_index is None:
            _append_invalid_warning(
                invalid_warnings_by_index,
                start_index,
                "llm_judgement_missing_paper_index",
            )
            continue
        if parsed_index in by_global_index:
            _append_invalid_warning(
                invalid_warnings_by_index,
                parsed_index,
                f"llm_judgement_duplicate_paper_index:{parsed_index}",
            )
            continue
        by_global_index[parsed_index] = raw_item

    results: list[JudgementResult] = []
    for local_index, (paper, rule_result) in enumerate(zip(papers, rule_results)):
        global_index = start_index + local_index
        item_warnings = [
            "llm_judgement_used",
            *global_warnings,
            *invalid_warnings_by_index.get(global_index, []),
        ]
        raw_item = by_global_index.get(global_index)
        if raw_item is None:
            results.append(
                _with_warning(
                    [rule_result],
                    f"llm_judgement_missing_paper_index:{global_index}",
                    extra_warnings=item_warnings,
                )[0]
            )
            continue

        parsed = _parse_one_llm_judgement(
            raw_item,
            paper=paper,
            rule_result=rule_result,
            global_index=global_index,
            base_warnings=item_warnings,
        )
        results.append(parsed)
    return results


def _parse_one_llm_judgement(
    raw_item: dict[str, Any],
    *,
    paper: Paper,
    rule_result: JudgementResult,
    global_index: int,
    base_warnings: list[str],
) -> JudgementResult:
    warnings = list(base_warnings)
    category = str(raw_item.get("category", "")).strip()
    if category not in JUDGEMENT_CATEGORIES:
        return _with_warning(
            [rule_result],
            f"llm_judgement_invalid_category:{global_index}",
            extra_warnings=warnings,
        )[0]

    score = _parse_score(raw_item.get("score"), warnings, global_index)
    if score is None:
        return _with_warning(
            [rule_result],
            f"llm_judgement_invalid_score:{global_index}",
            extra_warnings=warnings,
        )[0]

    evidence = _normalize_llm_evidence(
        raw_item.get("evidence"),
        paper=paper,
        global_index=global_index,
        warnings=warnings,
    )
    reasoning = _short_text(str(raw_item.get("reasoning") or "").strip(), limit=320)
    if not reasoning:
        reasoning = "LLM judged relevance using candidate metadata only."
    matched_terms = _dedupe_terms(_coerce_string_list(raw_item.get("matched_terms")))
    warnings.extend(_coerce_string_list(raw_item.get("warnings")))
    warnings.extend(rule_result.warnings)

    return JudgementResult(
        paper=paper,
        score=round(score, 4),
        category=category,  # type: ignore[arg-type]
        reasoning=reasoning,
        evidence=_dedupe_evidence(evidence),
        matched_terms=matched_terms,
        warnings=_dedupe_terms(warnings),
    )


def _parse_score(
    raw_score: Any,
    warnings: list[str],
    global_index: int,
) -> float | None:
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        return None
    clamped = _clamp(score)
    if clamped != score:
        warnings.append(f"llm_judgement_score_clamped:{global_index}")
    return clamped


def _normalize_llm_evidence(
    raw_evidence: Any,
    *,
    paper: Paper,
    global_index: int,
    warnings: list[str],
) -> list[EvidenceItem]:
    if raw_evidence is None:
        warnings.append(f"llm_judgement_missing_evidence:{global_index}")
        return []
    if isinstance(raw_evidence, dict):
        raw_evidence = [raw_evidence]
    if not isinstance(raw_evidence, list):
        warnings.append(f"llm_judgement_invalid_evidence:{global_index}")
        return []

    evidence: list[EvidenceItem] = []
    for item in raw_evidence:
        if not isinstance(item, dict):
            warnings.append(f"llm_judgement_invalid_evidence_item:{global_index}")
            continue
        source = str(item.get("source") or "").strip().lower()
        if source not in EVIDENCE_SOURCES:
            warnings.append(
                f"llm_judgement_bad_evidence_source:{global_index}:{source or 'unknown'}"
            )
            continue
        text = _short_text(str(item.get("text") or ""), limit=200)
        grounded_text = _ground_evidence_text(
            source,
            text,
            paper,
            global_index=global_index,
            warnings=warnings,
        )
        if not grounded_text:
            continue
        try:
            confidence = float(item.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
            warnings.append(f"llm_judgement_invalid_evidence_confidence:{global_index}")
        evidence.append(
            EvidenceItem(
                source=source,  # type: ignore[arg-type]
                text=grounded_text,
                confidence=_clamp(confidence),
            )
        )
    return evidence


def _ground_evidence_text(
    source: str,
    text: str,
    paper: Paper,
    *,
    global_index: int,
    warnings: list[str],
) -> str:
    if source == "title":
        return _ground_against_field(
            source,
            text,
            paper.title,
            global_index=global_index,
            warnings=warnings,
        )
    if source == "abstract":
        return _ground_against_field(
            source,
            text,
            paper.abstract,
            global_index=global_index,
            warnings=warnings,
        )
    if source == "venue":
        return _ground_against_field(
            source,
            text,
            paper.venue or "",
            global_index=global_index,
            warnings=warnings,
        )
    metadata_text = _short_text(
        "; ".join(
            item
            for item in (
                f"year={paper.year}" if paper.year is not None else "",
                f"sources={','.join(paper.sources)}" if paper.sources else "",
                (
                    f"citation_count={paper.citation_count}"
                    if paper.citation_count is not None
                    else ""
                ),
            )
            if item
        )
        or text,
        limit=160,
    )
    if text and text.casefold() not in metadata_text.casefold():
        warnings.append(f"llm_judgement_evidence_regrounded:{global_index}:metadata")
    return metadata_text


def _ground_against_field(
    source: str,
    text: str,
    field_value: str,
    *,
    global_index: int,
    warnings: list[str],
) -> str:
    field_text = _short_text(field_value, limit=200)
    if not field_text:
        warnings.append(f"llm_judgement_evidence_missing_metadata:{global_index}:{source}")
        return ""
    if not text:
        return field_text
    if text.casefold() in field_text.casefold():
        return text
    warnings.append(f"llm_judgement_evidence_regrounded:{global_index}:{source}")
    return field_text


def _parse_llm_paper_index(
    raw_index: Any,
    *,
    start_index: int,
    batch_size: int,
) -> int | None:
    try:
        index = int(raw_index)
    except (TypeError, ValueError):
        return None
    if start_index <= index < start_index + batch_size:
        return index
    if 0 <= index < batch_size:
        return start_index + index
    return None


def _with_warning(
    results: list[JudgementResult],
    warning: str,
    *,
    extra_warnings: list[str] | None = None,
) -> list[JudgementResult]:
    warnings_to_add = [*(extra_warnings or []), warning]
    return [
        result.model_copy(
            update={
                "warnings": _dedupe_terms([*result.warnings, *warnings_to_add]),
                "reasoning": _append_warning_to_reasoning(result.reasoning, warning),
            }
        )
        for result in results
    ]


def _append_warning_to_reasoning(reasoning: str, warning: str) -> str:
    if warning in reasoning:
        return reasoning
    return f"{reasoning} Warning: {warning}."


def _append_invalid_warning(
    warnings_by_index: dict[int, list[str]],
    global_index: int,
    warning: str,
) -> None:
    warnings_by_index.setdefault(global_index, []).append(warning)


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    try:
        return [str(item).strip() for item in value if str(item).strip()]
    except TypeError:
        return []


def _diagnostic_message(value: Any) -> str:
    message = str(value).replace("\n", " ").strip()
    return message[:160] or "unknown"

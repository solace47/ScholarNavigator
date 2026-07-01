"""Rule-based relevance judgement for retrieved paper metadata."""

from __future__ import annotations

import re
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

LLM_JUDGEMENT_BATCH_SIZE_ENV = "SCHOLAR_AGENT_LLM_JUDGEMENT_BATCH_SIZE"
DEFAULT_LLM_JUDGEMENT_BATCH_SIZE = 8
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
    ) -> list[JudgementResult]:
        _validate_thresholds(threshold_high, threshold_partial, threshold_weak)
        if use_llm:
            return self._judge_with_optional_llm(
                query_analysis,
                papers,
                threshold_high=threshold_high,
                threshold_partial=threshold_partial,
                threshold_weak=threshold_weak,
            )
        return _judge_papers_rules(
            query_analysis,
            papers,
            threshold_high=threshold_high,
            threshold_partial=threshold_partial,
            threshold_weak=threshold_weak,
        )

    def _judge_with_optional_llm(
        self,
        query_analysis: QueryAnalysis,
        papers: list[Paper],
        *,
        threshold_high: float,
        threshold_partial: float,
        threshold_weak: float,
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
        results: list[JudgementResult] = list(rule_results)
        for start in range(0, len(papers), batch_size):
            end = min(start + batch_size, len(papers))
            batch_papers = papers[start:end]
            batch_rule_results = rule_results[start:end]
            try:
                self.llm_call_count += 1
                raw_response = _chat_json(
                    self._llm_client,
                    _build_llm_judgement_messages(
                        query_analysis,
                        batch_papers,
                        start_index=start,
                    ),
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
) -> list[JudgementResult]:
    """Judge paper relevance using metadata rules or optional LLM JSON."""

    return JudgementAgent(llm_client=llm_client).judge(
        query_analysis,
        papers,
        threshold_high=threshold_high,
        threshold_partial=threshold_partial,
        threshold_weak=threshold_weak,
        use_llm=use_llm,
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


def _chat_json(
    llm_client: LLMJsonClient | None,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    if llm_client is not None:
        return llm_client.chat_json(messages, temperature=0)
    return provider_chat_json(messages, temperature=0)


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
    return [
        {
            "role": "system",
            "content": (
                "You are ScholarNavigator's relevance judgement agent. Return only "
                "one JSON object. Judge only the provided candidate papers. Do not "
                "invent papers, citations, full-text evidence, API keys, or external "
                "facts. Evidence must come from title, abstract, venue, or metadata."
            ),
        },
        {
            "role": "user",
            "content": (
                "For each paper, return JSON with key 'judgements'. Each judgement "
                "must include paper_index, score from 0 to 1, category "
                "(highly_relevant, partially_relevant, weakly_relevant, irrelevant, "
                "insufficient_evidence), reasoning, evidence, matched_terms, and "
                f"warnings. Input:\n{payload}"
            ),
        },
    ]


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

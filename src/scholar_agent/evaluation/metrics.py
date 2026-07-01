"""Pure metric helpers for offline search evaluation."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from scholar_agent.core.evaluation_schemas import EvalGoldPaper
from scholar_agent.core.paper_schemas import Paper
from scholar_agent.core.search_schemas import RankedPaper


def canonical_paper_id(
    paper: Any = None,
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    openalex_id: str | None = None,
    semantic_scholar_id: str | None = None,
    pubmed_id: str | None = None,
    title: str | None = None,
    year: int | None = None,
) -> str | None:
    """Return a stable paper identifier using the evaluation priority order."""

    paper = _unwrap_ranked_paper(paper)
    doi_value = doi or _extract_identifier(paper, "doi")
    if doi_value:
        return f"doi:{_normalize_doi(doi_value)}"

    arxiv_value = arxiv_id or _extract_identifier(paper, "arxiv_id")
    if arxiv_value:
        return f"arxiv:{_normalize_arxiv_id(arxiv_value)}"

    openalex_value = openalex_id or _extract_identifier(paper, "openalex_id")
    if openalex_value:
        return f"openalex:{_normalize_openalex_id(openalex_value)}"

    semantic_value = (
        semantic_scholar_id
        or _extract_identifier(paper, "semantic_scholar_id")
        or _extract_identifier(paper, "corpus_id")
        or _extract_identifier(paper, "paper_id")
    )
    if semantic_value:
        return f"s2:{_normalize_semantic_scholar_id(semantic_value)}"

    pubmed_value = pubmed_id or _extract_identifier(paper, "pubmed_id")
    if pubmed_value:
        return f"pubmed:{_normalize_pubmed_id(pubmed_value)}"

    title_value = title or _get_value(paper, "title")
    year_value = year if year is not None else _get_value(paper, "year")
    normalized_title = _normalize_title(str(title_value or ""))
    if normalized_title and year_value is not None:
        return f"title_year:{normalized_title}:{year_value}"
    return None


def recall_at_k(ranked_papers: Sequence[Any], gold_papers: Sequence[Any], k: int) -> float:
    """Compute set recall at K over positive-gold papers."""

    if k <= 0:
        return 0.0
    gold_ids = _gold_relevance(gold_papers)
    relevant_ids = {paper_id for paper_id, grade in gold_ids.items() if grade > 0}
    if not relevant_ids:
        return 0.0
    retrieved_ids = _unique_ranked_ids(ranked_papers[:k])
    return len(relevant_ids.intersection(retrieved_ids)) / len(relevant_ids)


def precision_at_k(
    ranked_papers: Sequence[Any], gold_papers: Sequence[Any], k: int
) -> float:
    """Compute precision at K with K as the denominator."""

    if k <= 0:
        return 0.0
    gold_ids = _gold_relevance(gold_papers)
    relevant_ids = {paper_id for paper_id, grade in gold_ids.items() if grade > 0}
    if not relevant_ids or not ranked_papers:
        return 0.0
    retrieved_ids = _unique_ranked_ids(ranked_papers[:k])
    return len(relevant_ids.intersection(retrieved_ids)) / k


def mrr(ranked_papers: Sequence[Any], gold_papers: Sequence[Any]) -> float:
    """Compute reciprocal rank of the first positive-gold paper."""

    gold_ids = _gold_relevance(gold_papers)
    relevant_ids = {paper_id for paper_id, grade in gold_ids.items() if grade > 0}
    if not relevant_ids:
        return 0.0

    seen: set[str] = set()
    for rank, paper in enumerate(ranked_papers, start=1):
        paper_id = canonical_paper_id(paper)
        if paper_id is None or paper_id in seen:
            continue
        seen.add(paper_id)
        if paper_id in relevant_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked_papers: Sequence[Any], gold_papers: Sequence[Any], k: int) -> float:
    """Compute nDCG@K with binary or graded relevance labels."""

    if k <= 0:
        return 0.0
    gold_ids = _gold_relevance(gold_papers)
    positive_grades = [grade for grade in gold_ids.values() if grade > 0]
    if not positive_grades:
        return 0.0

    seen: set[str] = set()
    gains: list[float] = []
    for paper in ranked_papers[:k]:
        paper_id = canonical_paper_id(paper)
        if paper_id is None or paper_id in seen:
            gains.append(0.0)
            continue
        seen.add(paper_id)
        gains.append(gold_ids.get(paper_id, 0.0))

    dcg = _dcg(gains)
    ideal_gains = sorted(positive_grades, reverse=True)[:k]
    idcg = _dcg(ideal_gains)
    if idcg <= 0:
        return 0.0
    return dcg / idcg


def candidate_count_metrics(
    raw_count: int,
    deduplicated_count: int,
    *,
    ranked_count: int | None = None,
    source_stats: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Summarize candidate volume and duplicate rate."""

    raw = max(0, int(raw_count))
    deduplicated = max(0, int(deduplicated_count))
    duplicate_count = max(0, raw - deduplicated)
    duplicate_ratio = duplicate_count / raw if raw else 0.0

    per_source_returned_count: dict[str, int] = {}
    for stat in source_stats or []:
        source = str(_get_value(stat, "source") or "unknown")
        returned_count = int(_get_value(stat, "returned_count") or 0)
        per_source_returned_count[source] = (
            per_source_returned_count.get(source, 0) + returned_count
        )

    return {
        "raw_count": raw,
        "deduplicated_count": deduplicated,
        "ranked_count": max(0, int(ranked_count or 0)),
        "duplicate_count": duplicate_count,
        "duplicate_ratio": duplicate_ratio,
        "per_source_returned_count": per_source_returned_count,
    }


def error_rate_metrics(
    source_stats: Sequence[Any] | None = None,
    warnings: Sequence[str] | None = None,
    *,
    failed_case_count: int = 0,
    total_case_count: int = 1,
    warning_case_count: int | None = None,
) -> dict[str, float | int]:
    """Summarize source, warning, and case-level error rates."""

    stats = list(source_stats or [])
    source_error_count = sum(
        1 for stat in stats if str(_get_value(stat, "error_message") or "").strip()
    )
    source_call_count = len(stats)
    warning_count = sum(1 for warning in warnings or [] if str(warning).strip())
    total_cases = max(0, int(total_case_count))
    warning_cases = (
        max(0, int(warning_case_count))
        if warning_case_count is not None
        else int(warning_count > 0)
    )
    failed_cases = max(0, int(failed_case_count))

    return {
        "source_call_count": source_call_count,
        "source_error_count": source_error_count,
        "source_error_rate": source_error_count / source_call_count
        if source_call_count
        else 0.0,
        "warning_count": warning_count,
        "query_warning_rate": warning_cases / total_cases if total_cases else 0.0,
        "failed_case_count": failed_cases,
        "failed_case_rate": failed_cases / total_cases if total_cases else 0.0,
    }


def _gold_relevance(gold_papers: Sequence[Any]) -> dict[str, float]:
    relevance: dict[str, float] = {}
    for paper in gold_papers:
        paper_id = canonical_paper_id(paper)
        if paper_id is None:
            continue
        relevance[paper_id] = max(relevance.get(paper_id, 0.0), _relevance_grade(paper))
    return relevance


def _unique_ranked_ids(ranked_papers: Sequence[Any]) -> set[str]:
    ids: set[str] = set()
    for paper in ranked_papers:
        paper_id = canonical_paper_id(paper)
        if paper_id:
            ids.add(paper_id)
    return ids


def _relevance_grade(paper: Any) -> float:
    grade = _get_value(paper, "relevance_grade")
    if grade is None:
        return 1.0
    try:
        return max(0.0, float(grade))
    except (TypeError, ValueError):
        return 0.0


def _dcg(gains: Sequence[float]) -> float:
    score = 0.0
    for index, gain in enumerate(gains, start=1):
        if gain <= 0:
            continue
        score += (math.pow(2.0, gain) - 1.0) / math.log2(index + 1)
    return score


def _unwrap_ranked_paper(paper: Any) -> Any:
    if isinstance(paper, RankedPaper):
        return paper.paper
    if isinstance(paper, Mapping) and "paper" in paper:
        return paper["paper"]
    if not isinstance(paper, (Paper, EvalGoldPaper)) and hasattr(paper, "paper"):
        return getattr(paper, "paper")
    return paper


def _extract_identifier(paper: Any, name: str) -> str | None:
    value = _get_value(paper, name)
    if value:
        return str(value)

    identifiers = _get_value(paper, "identifiers")
    value = _get_value(identifiers, name)
    if value:
        return str(value)
    return None


def _get_value(item: Any, key: str) -> Any:
    if item is None:
        return None
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def _normalize_doi(value: str) -> str:
    normalized = value.strip().casefold()
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized.strip()


def _normalize_arxiv_id(value: str) -> str:
    normalized = value.strip().casefold()
    normalized = normalized.split("?", 1)[0].rstrip("/")
    if "arxiv.org/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    for prefix in ("arxiv:", "abs/", "pdf/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    if normalized.endswith(".pdf"):
        normalized = normalized[:-4]
    return re.sub(r"v\d+$", "", normalized).strip()


def _normalize_openalex_id(value: str) -> str:
    normalized = value.strip().casefold().rstrip("/")
    if "openalex.org/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    if normalized.startswith("openalex:"):
        normalized = normalized[len("openalex:") :]
    return normalized.strip()


def _normalize_semantic_scholar_id(value: str) -> str:
    normalized = value.strip().casefold()
    for prefix in ("semantic_scholar:", "semantic-scholar:", "corpusid:", "s2:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized.strip()


def _normalize_pubmed_id(value: str) -> str:
    normalized = value.strip().casefold()
    for prefix in ("pubmed:", "pmid:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized.strip()


def _normalize_title(value: str) -> str:
    normalized = value.casefold()
    normalized = re.sub(r"\\[a-zA-Z]+\*?", " ", normalized)
    normalized = re.sub(r"[{}$^_~]", " ", normalized)
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())

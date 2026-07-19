"""将逻辑子查询确定性适配为各公开检索源可接受的短查询。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from pydantic import BaseModel, Field

from scholar_agent.core.search_schemas import QueryConstraint


MAX_ADAPTED_QUERIES_PER_SOURCE = 2
MAX_OPENALEX_QUERY_LENGTH = 180
MAX_OPENALEX_TERMS = 12
MAX_SEMANTIC_SCHOLAR_QUERY_LENGTH = 180
MAX_SEMANTIC_SCHOLAR_TERMS = 10
MAX_PUBMED_QUERY_LENGTH = 180
MAX_PUBMED_TERMS = 10
MAX_ARXIV_QUERY_LENGTH = 240
MAX_ARXIV_TERMS = 6

_ARXIV_FIELD_EXPRESSION = re.compile(
    r"(?:^|[\s(])(?:all|ti|abs|au|cat|jr|id):",
    re.IGNORECASE,
)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[+#.][A-Za-z0-9]+)*|[\u4e00-\u9fff]+")
_ACADEMIC_QUERY_STOPWORDS = {
    "a",
    "about",
    "an",
    "analysed",
    "analyzed",
    "and",
    "any",
    "are",
    "attempt",
    "attempts",
    "automatic",
    "automatically",
    "based",
    "can",
    "case",
    "cases",
    "could",
    "develop",
    "developed",
    "do",
    "field",
    "for",
    "focused",
    "give",
    "have",
    "identifying",
    "in",
    "information",
    "into",
    "is",
    "issue",
    "issues",
    "list",
    "literature",
    "me",
    "of",
    "on",
    "over",
    "paper",
    "papers",
    "part",
    "parts",
    "please",
    "present",
    "proposed",
    "provide",
    "providing",
    "related",
    "representative",
    "research",
    "resource",
    "resources",
    "scenario",
    "scenarios",
    "show",
    "some",
    "suitable",
    "studies",
    "study",
    "tell",
    "that",
    "the",
    "there",
    "through",
    "to",
    "use",
    "used",
    "using",
    "what",
    "where",
    "which",
    "with",
    "works",
    "you",
    "your",
}


class AdaptedQuery(BaseModel):
    original_query: str
    source: str
    query: str
    strategy: str
    dropped_terms: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def adapt_query_for_source(
    query: str,
    source: str,
    *,
    constraints: QueryConstraint | None = None,
) -> AdaptedQuery:
    """返回单个来源的主查询；不访问外网，也不使用评测答案。"""

    source_key = _normalize_source(source)
    original = _normalize_space(_strip_controls(query))
    if source_key == "arxiv" and _ARXIV_FIELD_EXPRESSION.search(original):
        return AdaptedQuery(
            original_query=query,
            source=source_key,
            query=original[:MAX_ARXIV_QUERY_LENGTH],
            strategy="arxiv_pre_adapted_expression",
            warnings=(
                ["adapted_query_truncated"]
                if len(original) > MAX_ARXIV_QUERY_LENGTH
                else []
            ),
        )

    terms, dropped = _core_terms(original, constraints)
    if source_key == "arxiv":
        return _adapt_arxiv_primary(query, terms, dropped)
    if source_key == "openalex":
        return _adapt_plain_query(
            query,
            source_key,
            terms,
            dropped,
            max_terms=MAX_OPENALEX_TERMS,
            max_length=MAX_OPENALEX_QUERY_LENGTH,
            strategy="openalex_sanitized_core_terms",
        )
    if source_key == "semantic_scholar":
        return _adapt_plain_query(
            query,
            source_key,
            terms,
            dropped,
            max_terms=MAX_SEMANTIC_SCHOLAR_TERMS,
            max_length=MAX_SEMANTIC_SCHOLAR_QUERY_LENGTH,
            strategy="semantic_scholar_core_terms",
        )
    if source_key == "pubmed":
        return _adapt_plain_query(
            query,
            source_key,
            terms,
            dropped,
            max_terms=MAX_PUBMED_TERMS,
            max_length=MAX_PUBMED_QUERY_LENGTH,
            strategy="pubmed_core_terms",
        )
    return _adapt_plain_query(
        query,
        source_key,
        terms,
        dropped,
        max_terms=MAX_OPENALEX_TERMS,
        max_length=MAX_OPENALEX_QUERY_LENGTH,
        strategy="generic_core_terms",
    )


def adapt_queries_for_source(
    query: str,
    source: str,
    *,
    constraints: QueryConstraint | None = None,
    max_queries: int = MAX_ADAPTED_QUERIES_PER_SOURCE,
) -> list[AdaptedQuery]:
    """生成有界查询变体；当前仅 arXiv 使用一个受控的补充形式。"""

    limit = max(0, min(int(max_queries), MAX_ADAPTED_QUERIES_PER_SOURCE))
    if limit == 0:
        return []
    primary = adapt_query_for_source(query, source, constraints=constraints)
    if not primary.query or limit == 1 or primary.source != "arxiv":
        return [primary]
    if primary.strategy == "arxiv_pre_adapted_expression":
        return [primary]

    terms, dropped = _core_terms(_normalize_space(_strip_controls(query)), constraints)
    fallback = _adapt_arxiv_fallback(query, terms, dropped)
    if not fallback.query or _query_key(fallback.query) == _query_key(primary.query):
        return [primary]
    return [primary, fallback][:limit]


def _adapt_arxiv_primary(
    original_query: str,
    terms: list[str],
    dropped: list[str],
) -> AdaptedQuery:
    selected, omitted = _bounded_terms(terms, MAX_ARXIV_TERMS)
    clauses = [
        f"(ti:{_arxiv_value(term)} OR abs:{_arxiv_value(term)})"
        for term in selected[:3]
        if _arxiv_value(term)
    ]
    adapted = " AND ".join(clauses)
    warnings = _adaptation_warnings(
        original_query,
        adapted,
        omitted,
        MAX_ARXIV_QUERY_LENGTH,
    )
    return AdaptedQuery(
        original_query=original_query,
        source="arxiv",
        query=adapted[:MAX_ARXIV_QUERY_LENGTH],
        strategy="arxiv_title_abstract_core_terms",
        dropped_terms=_stable([*dropped, *omitted]),
        warnings=warnings,
    )


def _adapt_arxiv_fallback(
    original_query: str,
    terms: list[str],
    dropped: list[str],
) -> AdaptedQuery:
    selected, omitted = _bounded_terms(terms, MAX_ARXIV_TERMS)
    pairs = [selected[index : index + 2] for index in range(0, 4, 2)]
    clauses = [
        "("
        + " AND ".join(
            f"all:{_arxiv_value(term)}"
            for term in pair
            if _arxiv_value(term)
        )
        + ")"
        for pair in pairs
        if pair
    ]
    adapted = " OR ".join(clause for clause in clauses if clause != "()")
    warnings = _adaptation_warnings(
        original_query,
        adapted,
        omitted,
        MAX_ARXIV_QUERY_LENGTH,
    )
    return AdaptedQuery(
        original_query=original_query,
        source="arxiv",
        query=adapted[:MAX_ARXIV_QUERY_LENGTH],
        strategy="arxiv_paired_core_terms",
        dropped_terms=_stable([*dropped, *omitted]),
        warnings=warnings,
    )


def _adapt_plain_query(
    original_query: str,
    source: str,
    terms: list[str],
    dropped: list[str],
    *,
    max_terms: int,
    max_length: int,
    strategy: str,
) -> AdaptedQuery:
    selected, omitted = _bounded_terms(terms, max_terms)
    adapted_terms: list[str] = []
    for term in selected:
        candidate = " ".join([*adapted_terms, term]).strip()
        if len(candidate) > max_length:
            omitted.append(term)
            continue
        adapted_terms.append(term)
    adapted = " ".join(adapted_terms)
    warnings = _adaptation_warnings(
        original_query,
        adapted,
        omitted,
        max_length,
    )
    return AdaptedQuery(
        original_query=original_query,
        source=source,
        query=adapted,
        strategy=strategy,
        dropped_terms=_stable([*dropped, *omitted]),
        warnings=warnings,
    )


def _core_terms(
    query: str,
    constraints: QueryConstraint | None,
) -> tuple[list[str], list[str]]:
    prioritized = _constraint_terms(constraints, explicit_only=True)
    supplemental = _constraint_terms(constraints, explicit_only=False)
    query_tokens = _TOKEN_PATTERN.findall(
        unicodedata.normalize("NFKC", query).replace("-", " ")
    )
    kept: list[str] = []
    dropped: list[str] = []
    for token in [*prioritized, *query_tokens, *supplemental]:
        normalized = _normalize_term(token)
        if not normalized:
            continue
        if normalized.casefold() in _ACADEMIC_QUERY_STOPWORDS:
            dropped.append(normalized)
            continue
        kept.append(normalized)
    return _stable(kept), _stable(dropped)


def _constraint_terms(
    constraints: QueryConstraint | None,
    *,
    explicit_only: bool,
) -> list[str]:
    if constraints is None:
        return []
    values: list[str] = []
    fields = set(constraints.explicit_fields)
    if not explicit_only or "methods" in fields:
        values.extend(constraints.methods)
    if not explicit_only or "datasets" in fields:
        values.extend(constraints.datasets)
    if not explicit_only or "venues" in fields:
        values.extend(constraints.venues)
    if not explicit_only or "paper_types" in fields:
        values.extend(constraints.paper_types)
    if not explicit_only or "domains" in fields:
        values.extend(
            domain.replace("_", " ")
            for domain in constraints.domains
            if domain != "general_science"
        )
    if "must_include_terms" in fields:
        values.extend(constraints.must_include_terms)
    if explicit_only:
        return values
    explicit_values = {
        value.casefold()
        for value in _constraint_terms(constraints, explicit_only=True)
    }
    return [value for value in values if value.casefold() not in explicit_values]


def _bounded_terms(terms: list[str], limit: int) -> tuple[list[str], list[str]]:
    return list(terms[:limit]), list(terms[limit:])


def _adaptation_warnings(
    original_query: str,
    adapted_query: str,
    omitted: list[str],
    max_length: int,
) -> list[str]:
    warnings: list[str] = []
    if omitted or len(original_query) > max_length:
        warnings.append("adapted_query_truncated")
    if not adapted_query:
        warnings.append("empty_adapted_query")
    return warnings


def _escape_arxiv_phrase(value: str) -> str:
    cleaned = re.sub(r"[(){}\[\]:!?*^~\\\"']+", " ", value)
    return _normalize_space(cleaned)


def _arxiv_value(value: str) -> str:
    safe = _escape_arxiv_phrase(value)
    if not safe:
        return ""
    return f'"{safe}"' if " " in safe else safe


def _strip_controls(value: str) -> str:
    return "".join(character for character in str(value) if character.isprintable())


def _normalize_term(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value))
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff+#.]+", " ", text, flags=re.UNICODE)
    return _normalize_space(text)


def _normalize_space(value: str) -> str:
    return " ".join(str(value).split()).strip()


def _normalize_source(source: str) -> str:
    return source.strip().casefold().replace("-", "_").replace(" ", "_")


def _stable(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _normalize_space(value)
        key = item.casefold()
        if not item or key in seen:
            continue
        result.append(item)
        seen.add(key)
    return result


def _query_key(value: str) -> str:
    return _normalize_space(value).casefold()

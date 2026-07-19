"""确定性的初始检索子查询规划，不包含来源适配或评测信息。"""

from __future__ import annotations

import re
from collections.abc import Iterable

from scholar_agent.core.search_schemas import (
    QUERY_PLANNER_VERSION,
    QueryAnalysis,
    QueryFacet,
    QueryFacetSource,
    QueryFacetType,
    QueryPlanningResult,
    SearchSubquery,
)


PLANNER_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "any",
    "as",
    "at",
    "based",
    "by",
    "can",
    "compare",
    "comparison",
    "could",
    "develop",
    "explored",
    "find",
    "for",
    "from",
    "give",
    "have",
    "in",
    "into",
    "is",
    "latest",
    "list",
    "looking",
    "me",
    "of",
    "on",
    "paper",
    "papers",
    "please",
    "present",
    "proposed",
    "provide",
    "recent",
    "research",
    "review",
    "search",
    "show",
    "some",
    "studies",
    "study",
    "survey",
    "tell",
    "that",
    "the",
    "there",
    "through",
    "to",
    "using",
    "want",
    "what",
    "where",
    "which",
    "with",
    "works",
    "you",
}
GENERIC_PAPER_TERMS = {
    "application",
    "benchmark",
    "comparison",
    "dataset",
    "evaluation",
    "method",
    "review",
    "survey",
}
TASK_TERMS = (
    "question answering",
    "causal inference",
    "information retrieval",
    "property prediction",
    "recommendation",
    "classification",
    "prediction",
    "detection",
    "segmentation",
    "generation",
    "retrieval",
    "ranking",
    "reranking",
    "translation",
    "summarization",
    "reconstruction",
    "forecasting",
    "optimization",
    "application",
    "问答",
    "因果推断",
    "信息检索",
    "推荐",
    "分类",
    "预测",
    "检测",
    "分割",
    "生成",
    "检索",
    "排序",
    "翻译",
    "摘要",
    "重建",
)
PURPOSE_FACETS: dict[str, tuple[QueryFacetType, ...]] = {
    "original_query": ("topic",),
    "normalized_keywords": ("topic",),
    "method_dimension": ("topic", "method"),
    "dataset_dimension": ("topic", "dataset"),
    "venue_dimension": ("topic", "venue"),
    "paper_type_dimension": ("topic", "paper_type"),
    "application_expansion": ("topic", "task"),
    "benchmark_dataset_expansion": ("topic", "dataset", "paper_type"),
    "method_comparison_expansion": ("topic", "method", "paper_type"),
    "survey_expansion": ("topic", "paper_type"),
    "recent_progress_expansion": ("topic", "temporal"),
}


def plan_facet_balanced(
    query_analysis: QueryAnalysis,
    *,
    selected_sources: list[str],
    max_subqueries: int,
) -> tuple[list[SearchSubquery], QueryPlanningResult]:
    """保留原查询，并在固定配额内选择互补 facet 查询。"""

    facets = identify_query_facets(query_analysis)
    topic = next((item for item in facets if item.facet_type == "topic"), None)
    topic_terms = list(topic.terms if topic is not None else [])
    required_terms = _explicit_must_have(query_analysis)
    original = SearchSubquery(
        query=query_analysis.original_query,
        source_hints=selected_sources,
        priority=1,
        purpose="original_query",
        facet_types=["topic"] if topic_terms else [],
        provenance=["original_query"],
    )
    candidates = _facet_candidates(
        facets,
        topic_terms=topic_terms,
        required_terms=required_terms,
        selected_sources=selected_sources,
    )
    selected = [original]
    duplicate_count = 0
    skipped_facets: list[str] = []
    for candidate in candidates:
        duplicate = _similar_subquery(selected, candidate)
        if duplicate is not None:
            duplicate_count += 1
            selected[selected.index(duplicate)] = _merge_subquery_provenance(
                duplicate,
                candidate,
            )
            skipped_facets.append(f"duplicate:{candidate.purpose}")
            continue
        if len(selected) >= max_subqueries:
            skipped_facets.append(f"budget:{candidate.purpose}")
            continue
        selected.append(candidate.model_copy(update={"priority": len(selected) + 1}))

    result = _planning_result(
        policy="facet_balanced",
        facets=facets,
        selected=selected,
        skipped_facets=skipped_facets,
        duplicate_count=duplicate_count,
    )
    return selected, result


def summarize_current_rules_planning(
    query_analysis: QueryAnalysis,
    subqueries: list[SearchSubquery],
) -> tuple[list[SearchSubquery], QueryPlanningResult]:
    """为旧规划补充诊断元数据，但不改变查询、顺序或配额。"""

    facets = identify_query_facets(query_analysis)
    annotated = [
        _annotate_current_subquery(item, facets)
        for item in subqueries
    ]
    result = _planning_result(
        policy="current_rules",
        facets=facets,
        selected=annotated,
        skipped_facets=[],
        duplicate_count=max(0, len(annotated) - len({_query_key(i.query) for i in annotated})),
    )
    return annotated, result


def identify_query_facets(query_analysis: QueryAnalysis) -> list[QueryFacet]:
    """从最终合并后的 QueryAnalysis 提取可追踪 facet。"""

    constraints = query_analysis.constraints
    llm_used = "llm_query_understanding" in query_analysis.reasoning
    structured_terms = _casefold_set(
        constraints.methods
        + constraints.datasets
        + constraints.venues
        + list(constraints.paper_types)
    )
    tasks = _task_terms(query_analysis.original_query)
    structured_terms.update(value.casefold() for value in tasks)
    structured_tokens = {
        token.casefold()
        for value in structured_terms
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+.#-]*", value)
    }
    topics = [
        term
        for term in _query_terms(query_analysis.original_query)
        if term.casefold() not in structured_terms
        and term.casefold() not in structured_tokens
        and term.casefold() not in GENERIC_PAPER_TERMS
    ]
    explicit_must = _explicit_must_have(query_analysis)
    topics = _dedupe([*explicit_must, *topics])[:12]
    facets: list[QueryFacet] = []
    if topics:
        source: QueryFacetSource = (
            "explicit"
            if explicit_must
            else "llm" if llm_used else "rules"
        )
        facets.append(
            QueryFacet(
                facet_type="topic",
                terms=topics,
                confidence=1.0 if explicit_must else 0.8,
                source=source,
                required=True,
            )
        )
    _append_constraint_facet(
        facets,
        query_analysis,
        "method",
        list(constraints.methods),
        "methods",
        llm_used,
    )
    _append_constraint_facet(
        facets,
        query_analysis,
        "dataset",
        list(constraints.datasets),
        "datasets",
        llm_used,
    )
    if tasks:
        facets.append(
            QueryFacet(
                facet_type="task",
                terms=tasks,
                confidence=0.75,
                source="llm" if llm_used else "rules",
            )
        )
    _append_constraint_facet(
        facets,
        query_analysis,
        "paper_type",
        list(constraints.paper_types),
        "paper_types",
        llm_used,
    )
    _append_constraint_facet(
        facets,
        query_analysis,
        "venue",
        list(constraints.venues),
        "venues",
        llm_used,
    )
    if constraints.time_range is not None:
        facets.append(
            QueryFacet(
                facet_type="temporal",
                terms=[_time_term(constraints.time_range)],
                confidence=1.0,
                source=_constraint_source(
                    query_analysis,
                    "time_range",
                    llm_used,
                ),
                required="time_range" in constraints.explicit_fields,
            )
        )
    return facets


def _append_constraint_facet(
    facets: list[QueryFacet],
    query_analysis: QueryAnalysis,
    facet_type: QueryFacetType,
    terms: list[str],
    field_name: str,
    llm_used: bool,
) -> None:
    terms = _dedupe(terms)
    if not terms:
        return
    source = _constraint_source(query_analysis, field_name, llm_used)
    facets.append(
        QueryFacet(
            facet_type=facet_type,
            terms=terms,
            confidence=1.0 if source == "explicit" else 0.8,
            source=source,
            required=source == "explicit",
        )
    )


def _constraint_source(
    query_analysis: QueryAnalysis,
    field_name: str,
    llm_used: bool,
) -> QueryFacetSource:
    if field_name in query_analysis.constraints.explicit_fields:
        return "explicit"
    return "llm" if llm_used else "rules"


def _facet_candidates(
    facets: list[QueryFacet],
    *,
    topic_terms: list[str],
    required_terms: list[str],
    selected_sources: list[str],
) -> list[SearchSubquery]:
    base = topic_terms[:10]
    if not base:
        return []
    candidates: list[tuple[int, SearchSubquery]] = []
    compact_terms = _dedupe([*base, *required_terms])
    if len(compact_terms) >= 2:
        candidates.append(
            (
                0,
                SearchSubquery(
                    query=" ".join(compact_terms),
                    source_hints=selected_sources,
                    priority=2,
                    purpose="facet_topic_compact",
                    facet_types=["topic"],
                    provenance=[
                        "topic:rules",
                        *(
                            ["must_have:explicit"]
                            if required_terms
                            else []
                        ),
                    ],
                ),
            )
        )
    priority = {
        "method": 1,
        "dataset": 2,
        "task": 3,
        "paper_type": 4,
        "venue": 5,
        "temporal": 6,
    }
    for facet in facets:
        if facet.facet_type == "topic":
            continue
        rank = priority.get(facet.facet_type, 9)
        if facet.required:
            rank -= 10
        terms = _dedupe([*base, *facet.terms, *required_terms])
        query = " ".join(terms).strip()
        if not query:
            continue
        candidates.append(
            (
                rank,
                SearchSubquery(
                    query=query,
                    source_hints=selected_sources,
                    priority=2,
                    purpose=f"facet_{facet.facet_type}",
                    facet_types=["topic", facet.facet_type],
                    provenance=[
                        "topic:rules",
                        f"{facet.facet_type}:{facet.source}",
                        *(
                            ["must_have:explicit"]
                            if required_terms
                            else []
                        ),
                    ],
                ),
            )
        )
    candidates.sort(
        key=lambda item: (
            item[0],
            item[1].purpose,
            _query_key(item[1].query),
        )
    )
    return [subquery for _, subquery in candidates]


def _annotate_current_subquery(
    subquery: SearchSubquery,
    facets: list[QueryFacet],
) -> SearchSubquery:
    facet_types = list(PURPOSE_FACETS.get(subquery.purpose, ()))
    if subquery.purpose == "constraint_expansion":
        facet_types = [facet.facet_type for facet in facets]
    available = {facet.facet_type: facet for facet in facets}
    facet_types = [item for item in facet_types if item in available]
    provenance = [
        f"{facet_type}:{available[facet_type].source}"
        for facet_type in facet_types
    ]
    return subquery.model_copy(
        update={"facet_types": facet_types, "provenance": provenance}
    )


def _planning_result(
    *,
    policy: str,
    facets: list[QueryFacet],
    selected: list[SearchSubquery],
    skipped_facets: list[str],
    duplicate_count: int,
) -> QueryPlanningResult:
    selected_types = {
        facet_type
        for subquery in selected
        for facet_type in subquery.facet_types
    }
    return QueryPlanningResult(
        policy=policy,  # type: ignore[arg-type]
        planner_version=QUERY_PLANNER_VERSION,
        facets=facets,
        selected_subqueries=selected,
        skipped_facets=_dedupe(skipped_facets),
        identified_facet_count=len(facets),
        selected_facet_count=sum(
            facet.facet_type in selected_types for facet in facets
        ),
        explicit_facet_count=sum(facet.source == "explicit" for facet in facets),
        selected_subquery_count=len(selected),
        duplicate_subquery_count=duplicate_count,
        skipped_by_budget_count=sum(
            item.startswith("budget:") for item in skipped_facets
        ),
        topic_coverage=_facet_coverage(facets, selected_types, "topic"),
        method_coverage=_facet_coverage(facets, selected_types, "method"),
        dataset_coverage=_facet_coverage(facets, selected_types, "dataset"),
        task_coverage=_facet_coverage(facets, selected_types, "task"),
        paper_type_coverage=_facet_coverage(
            facets,
            selected_types,
            "paper_type",
        ),
    )


def _facet_coverage(
    facets: list[QueryFacet],
    selected_types: set[QueryFacetType],
    facet_type: QueryFacetType,
) -> float:
    identified = any(item.facet_type == facet_type for item in facets)
    if not identified:
        return 1.0
    return 1.0 if facet_type in selected_types else 0.0


def _similar_subquery(
    selected: list[SearchSubquery],
    candidate: SearchSubquery,
) -> SearchSubquery | None:
    candidate_key = _query_key(candidate.query)
    candidate_primary = _primary_facet(candidate)
    for current in selected:
        if _query_key(current.query) == candidate_key:
            return current
        if (
            candidate_primary is not None
            and candidate_primary == _primary_facet(current)
            and _term_jaccard(current.query, candidate.query) >= 0.8
        ):
            return current
    return None


def _merge_subquery_provenance(
    preferred: SearchSubquery,
    duplicate: SearchSubquery,
) -> SearchSubquery:
    return preferred.model_copy(
        update={
            "facet_types": _dedupe([*preferred.facet_types, *duplicate.facet_types]),
            "provenance": _dedupe([*preferred.provenance, *duplicate.provenance]),
        }
    )


def _primary_facet(subquery: SearchSubquery) -> str | None:
    return next(
        (item for item in subquery.facet_types if item != "topic"),
        None,
    )


def _explicit_must_have(query_analysis: QueryAnalysis) -> list[str]:
    constraints = query_analysis.constraints
    if "must_include_terms" not in constraints.explicit_fields:
        return []
    return list(constraints.must_include_terms)


def _task_terms(query: str) -> list[str]:
    lowered = query.casefold()
    return _dedupe([term for term in TASK_TERMS if term.casefold() in lowered])


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9+.#-]*", query):
        cleaned = token.strip(".,;:()[]{}").strip()
        key = cleaned.casefold()
        if not cleaned or key in PLANNER_STOPWORDS:
            continue
        if len(key) <= 2 and key not in {"ai", "cv", "ml"}:
            continue
        terms.append(_canonical_term(cleaned))
    for term in TASK_TERMS:
        if any("\u4e00" <= char <= "\u9fff" for char in term) and term in query:
            terms.append(term)
    return _dedupe(terms)


def _canonical_term(term: str) -> str:
    return (
        term.upper()
        if term.casefold() in {"ai", "cv", "llm", "ml", "nlp", "rag"}
        else term
    )


def _time_term(time_range: object) -> str:
    start = getattr(time_range, "start_year", None)
    end = getattr(time_range, "end_year", None)
    if start is not None and end is not None:
        return f"{start}-{end}"
    if start is not None:
        return f"since {start}"
    if end is not None:
        return f"through {end}"
    return str(getattr(time_range, "label", "temporal") or "temporal")


def _term_jaccard(left: str, right: str) -> float:
    left_terms = _casefold_set(_query_terms(left))
    right_terms = _casefold_set(_query_terms(right))
    union = left_terms | right_terms
    return len(left_terms & right_terms) / len(union) if union else 1.0


def _query_key(query: str) -> str:
    return " ".join(query.casefold().split())


def _casefold_set(values: Iterable[str]) -> set[str]:
    return {value.casefold() for value in values if value.strip()}


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip()
        key = item.casefold()
        if not item or key in seen:
            continue
        result.append(item)
        seen.add(key)
    return result

"""Rule-based query evolution for search recall expansion."""

from __future__ import annotations

import re

from scholar_agent.core.paper_schemas import Paper
from scholar_agent.core.search_schemas import (
    EvolvedSubquery,
    JudgementResult,
    QueryAnalysis,
    QueryEvolutionOptions,
    QueryEvolutionRecord,
    RankedPaper,
    SearchPlan,
    SUPPORTED_SEARCH_SOURCES,
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
    "method",
    "methods",
    "of",
    "on",
    "paper",
    "papers",
    "recent",
    "research",
    "review",
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


class QueryEvolutionAgent:
    """Generate deterministic evolved queries from relevant judged papers."""

    def evolve(
        self,
        query_analysis: QueryAnalysis,
        search_plan: SearchPlan,
        judgements: list[JudgementResult],
        ranked_papers: list[RankedPaper],
        used_queries: set[str],
        options: QueryEvolutionOptions | None = None,
    ) -> QueryEvolutionRecord:
        options = options or QueryEvolutionOptions()
        warnings: list[str] = []
        skipped_reasons: list[str] = []

        if options.max_evolved_queries == 0:
            return QueryEvolutionRecord(
                seed_count=0,
                skipped_reasons=["max_evolved_queries_zero"],
                warnings=["max_evolved_queries_zero"],
            )

        seeds = _select_seed_papers(
            judgements=judgements,
            ranked_papers=ranked_papers,
            max_seed_papers=options.max_seed_papers,
            min_seed_score=options.min_seed_score,
        )
        if not seeds:
            warning = "no_relevant_seed"
            return QueryEvolutionRecord(
                seed_count=0,
                skipped_reasons=[warning],
                warnings=[warning],
            )

        blocked_queries = _used_query_keys(search_plan, used_queries)
        source_hints = _safe_source_hints(search_plan)
        generated: list[EvolvedSubquery] = []
        seen_generated: set[str] = set()

        for candidate in _candidate_queries(query_analysis, seeds):
            normalized = _normalize_query(candidate.query)
            key = _query_key(normalized)
            if not normalized or key in blocked_queries or key in seen_generated:
                continue

            generated.append(
                EvolvedSubquery(
                    query=normalized,
                    source_hints=source_hints,
                    priority=min(len(generated) + 1, 5),
                    purpose=candidate.purpose,
                    seed_paper_titles=candidate.seed_titles,
                    generated_by="rules",
                )
            )
            seen_generated.add(key)
            if len(generated) >= options.max_evolved_queries:
                break

        if not generated:
            warning = "no_new_evolved_query"
            warnings.append(warning)
            skipped_reasons.append(warning)

        return QueryEvolutionRecord(
            seed_count=len(seeds),
            generated_queries=generated,
            skipped_reasons=skipped_reasons,
            warnings=warnings,
        )


def evolve_queries(
    query_analysis: QueryAnalysis,
    search_plan: SearchPlan,
    judgements: list[JudgementResult],
    ranked_papers: list[RankedPaper],
    used_queries: set[str],
    options: QueryEvolutionOptions | None = None,
) -> QueryEvolutionRecord:
    """Generate deterministic evolved queries without LLM or network access."""

    return QueryEvolutionAgent().evolve(
        query_analysis=query_analysis,
        search_plan=search_plan,
        judgements=judgements,
        ranked_papers=ranked_papers,
        used_queries=used_queries,
        options=options,
    )


class _Seed:
    def __init__(self, paper: Paper, score: float, matched_terms: list[str]) -> None:
        self.paper = paper
        self.score = score
        self.matched_terms = matched_terms


class _Candidate:
    def __init__(self, query: str, purpose: str, seed_titles: list[str]) -> None:
        self.query = query
        self.purpose = purpose
        self.seed_titles = seed_titles


def _select_seed_papers(
    *,
    judgements: list[JudgementResult],
    ranked_papers: list[RankedPaper],
    max_seed_papers: int,
    min_seed_score: float,
) -> list[_Seed]:
    if max_seed_papers == 0:
        return []

    judgement_by_key = {_paper_key(item.paper): item for item in judgements}
    seeds: list[_Seed] = []
    seen: set[str] = set()

    for ranked in ranked_papers:
        key = _paper_key(ranked.paper)
        judgement = judgement_by_key.get(key)
        score = judgement.score if judgement is not None else ranked.final_score
        if not _is_seed_category(ranked.category, score, min_seed_score):
            continue
        seeds.append(
            _Seed(
                paper=ranked.paper,
                score=score,
                matched_terms=list(ranked.matched_terms),
            )
        )
        seen.add(key)
        if len(seeds) >= max_seed_papers:
            return seeds

    for judgement in judgements:
        key = _paper_key(judgement.paper)
        if key in seen or not _is_seed_category(
            judgement.category,
            judgement.score,
            min_seed_score,
        ):
            continue
        seeds.append(
            _Seed(
                paper=judgement.paper,
                score=judgement.score,
                matched_terms=list(judgement.matched_terms),
            )
        )
        seen.add(key)
        if len(seeds) >= max_seed_papers:
            break

    return seeds


def _is_seed_category(category: str, score: float, min_seed_score: float) -> bool:
    if category == "highly_relevant":
        return True
    return category == "partially_relevant" and score >= min_seed_score


def _candidate_queries(
    query_analysis: QueryAnalysis,
    seeds: list[_Seed],
) -> list[_Candidate]:
    core_terms = _core_terms(query_analysis, seeds)
    if not core_terms:
        core_terms = _tokenize_text(query_analysis.original_query)
    seed_titles = [seed.paper.title for seed in seeds if seed.paper.title.strip()]
    candidates: list[_Candidate] = []

    base_query = _join_terms(core_terms[:6])
    if base_query:
        candidates.append(
            _Candidate(
                query=_intent_query(query_analysis.intent, base_query),
                purpose=f"query_evolution_{query_analysis.intent}",
                seed_titles=seed_titles[:3],
            )
        )

    method_terms = _dedupe(query_analysis.constraints.methods + _matched_terms(seeds))
    method_query = _join_terms((method_terms + core_terms)[:6])
    if method_query:
        candidates.append(
            _Candidate(
                query=_method_query(query_analysis.intent, method_query),
                purpose="query_evolution_matched_methods",
                seed_titles=seed_titles[:3],
            )
        )

    for seed in seeds:
        title_terms = _tokenize_text(seed.paper.title)
        terms = _dedupe(seed.matched_terms + title_terms + core_terms)
        query = _join_terms(terms[:7])
        if not query:
            continue
        candidates.append(
            _Candidate(
                query=query,
                purpose="query_evolution_from_seed_title",
                seed_titles=[seed.paper.title] if seed.paper.title.strip() else [],
            )
        )

    return candidates


def _core_terms(query_analysis: QueryAnalysis, seeds: list[_Seed]) -> list[str]:
    constraints = query_analysis.constraints
    seed_terms = _matched_terms(seeds)
    terms = (
        constraints.must_include_terms
        + constraints.methods
        + constraints.datasets
        + seed_terms
    )
    return _dedupe(terms)


def _matched_terms(seeds: list[_Seed]) -> list[str]:
    terms: list[str] = []
    for seed in seeds:
        terms.extend(seed.matched_terms)
    return _dedupe(terms)


def _intent_query(intent: str, base_query: str) -> str:
    if intent == "survey":
        return f"{base_query} survey review"
    if intent == "recent_progress":
        return f"{base_query} recent advances"
    if intent == "method_comparison":
        return f"{base_query} comparison benchmark"
    if intent == "benchmark_or_dataset":
        return f"{base_query} dataset benchmark evaluation"
    if intent == "application":
        return f"{base_query} application deployment"
    if intent == "paper_finding":
        return f"{base_query} representative papers"
    return f"{base_query} representative papers"


def _method_query(intent: str, method_query: str) -> str:
    if intent == "benchmark_or_dataset":
        return f"{method_query} benchmark"
    if intent == "method_comparison":
        return f"{method_query} comparison"
    return method_query


def _used_query_keys(search_plan: SearchPlan, used_queries: set[str]) -> set[str]:
    keys = {_query_key(query) for query in used_queries}
    keys.add(_query_key(search_plan.query_analysis.original_query))
    for subquery in search_plan.subqueries:
        keys.add(_query_key(subquery.query))
    return {key for key in keys if key}


def _safe_source_hints(search_plan: SearchPlan) -> list[str]:
    supported = set(SUPPORTED_SEARCH_SOURCES)
    sources = [
        source
        for source in search_plan.selected_sources
        if source in supported
    ]
    return sources or list(SUPPORTED_SEARCH_SOURCES)


def _paper_key(paper: Paper) -> str:
    identifiers = paper.identifiers
    for prefix, value in (
        ("doi", identifiers.doi),
        ("arxiv", identifiers.arxiv_id),
        ("openalex", identifiers.openalex_id),
        ("semantic", identifiers.semantic_scholar_id),
        ("pubmed", identifiers.pubmed_id),
    ):
        if value:
            return f"{prefix}:{value.casefold()}"
    title = _query_key(paper.title)
    return f"title:{title}"


def _tokenize_text(text: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9+.#-]*", text):
        item = token.strip(".,;:()[]{}")
        key = item.casefold()
        if not item or key in STOPWORDS:
            continue
        if len(key) <= 2 and key not in {"ai", "ml", "cv"}:
            continue
        terms.append(_canonical_term(item))
    return _dedupe(terms)


def _canonical_term(term: str) -> str:
    upper_terms = {"ai", "cv", "llm", "ml", "nlp", "rag"}
    clean = term.strip()
    if clean.casefold() in upper_terms:
        return clean.upper()
    return clean


def _join_terms(terms: list[str]) -> str:
    return " ".join(_dedupe(terms))


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query).strip()


def _query_key(query: str) -> str:
    return re.sub(r"\s+", " ", query).strip().casefold()


def _dedupe(values: list[str]) -> list[str]:
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

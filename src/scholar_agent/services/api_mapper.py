"""Map internal SearchService output into the public API result schema."""

from __future__ import annotations

from collections import defaultdict

from scholar_agent.core import api_schemas as api
from scholar_agent.core.paper_schemas import Paper as InternalPaper
from scholar_agent.core.search_schemas import (
    EvidenceItem as InternalEvidenceItem,
    QueryAnalysis as InternalQueryAnalysis,
    RankedPaper as InternalRankedPaper,
    SearchPlan as InternalSearchPlan,
)
from scholar_agent.core.synthesis_schemas import (
    CitationCoverage as InternalCitationCoverage,
    SynthesisEvidenceRow as InternalSynthesisEvidenceRow,
    SynthesisFinding as InternalSynthesisFinding,
    SynthesisOutput as InternalSynthesisOutput,
)
from scholar_agent.services.search_service import SearchServiceOutput


def map_search_service_output_to_api_result(
    run_id: str,
    output: SearchServiceOutput,
    *,
    status: str = "succeeded",
    partial: bool = False,
) -> api.SearchRunResultResponse:
    """Map internal no-LLM SearchService output into the API result contract."""

    highly_relevant: list[api.RankedPaper] = []
    partially_relevant: list[api.RankedPaper] = []
    missing_evidence = _missing_evidence(output)

    for ranked in output.ranked_papers:
        mapped = map_ranked_paper(ranked)
        if ranked.category == "highly_relevant":
            highly_relevant.append(mapped)
        elif ranked.category in {"partially_relevant", "weakly_relevant"}:
            partially_relevant.append(mapped)
        elif ranked.category in {"irrelevant", "insufficient_evidence"}:
            missing_evidence.append(
                f"filtered_paper:{ranked.rank}:{ranked.category}:{ranked.paper.title}"
            )
            missing_evidence.extend(
                f"filtered_paper_warning:{ranked.rank}:{warning}"
                for warning in ranked.warnings
            )

    all_visible = highly_relevant + partially_relevant
    return api.SearchRunResultResponse(
        run_id=run_id,
        status=status,  # type: ignore[arg-type]
        partial=partial,
        query_analysis=map_query_analysis(output.search_plan.query_analysis),
        search_plan=map_search_plan(output.search_plan, output),
        highly_relevant_papers=highly_relevant,
        partially_relevant_papers=partially_relevant,
        method_clusters=_method_clusters(output.search_plan.query_analysis, all_visible),
        timeline=_timeline(all_visible),
        citation_graph=_citation_graph(all_visible, output),
        missing_evidence=_dedupe(missing_evidence),
        synthesis=map_synthesis_output(output.synthesis_output),
        cost_report=_cost_report(output),
    )


def map_paper(paper: InternalPaper) -> api.Paper:
    """Map an internal Paper into an API Paper."""

    return api.Paper(
        title=paper.title,
        authors=list(paper.authors),
        year=paper.year or 0,
        venue=paper.venue,
        abstract=paper.abstract,
        identifiers=api.PaperIdentifiers(
            doi=paper.identifiers.doi,
            arxiv_id=paper.identifiers.arxiv_id,
            semantic_scholar_id=paper.identifiers.semantic_scholar_id,
            openalex_id=paper.identifiers.openalex_id,
            pubmed_id=paper.identifiers.pubmed_id,
        ),
        urls=api.PaperUrls(
            landing_page=paper.urls.landing_page,
            pdf=paper.urls.pdf,
        ),
        sources=list(paper.sources),
    )


def map_ranked_paper(ranked: InternalRankedPaper) -> api.RankedPaper:
    """Map an internal RankedPaper into an API RankedPaper."""

    return api.RankedPaper(
        rank=ranked.rank,
        paper=map_paper(ranked.paper),
        relevance_score=ranked.final_score,
        category=ranked.category,
        matched_constraints=list(ranked.matched_terms),
        ranking_reason=ranked.ranking_reason,
        evidence=[map_evidence_item(item) for item in ranked.evidence],
    )


def map_evidence_item(item: InternalEvidenceItem) -> api.EvidenceItem:
    """Map an internal evidence item into an API evidence item."""

    return api.EvidenceItem(
        source=item.source,
        text=item.text,
        confidence=item.confidence,
    )


def map_synthesis_output(
    synthesis: InternalSynthesisOutput | None,
) -> api.SynthesisOutput | None:
    """Map internal synthesis output into the optional API synthesis object."""

    if synthesis is None:
        return None
    return api.SynthesisOutput(
        answer_summary=synthesis.answer_summary,
        status=synthesis.status,
        key_findings=[
            map_synthesis_finding(finding) for finding in synthesis.key_findings
        ],
        evidence_table=[
            map_synthesis_evidence_row(row) for row in synthesis.evidence_table
        ],
        citation_coverage=map_citation_coverage(synthesis.citation_coverage),
        limitations=list(synthesis.limitations),
        warnings=list(synthesis.warnings),
    )


def map_synthesis_evidence_row(
    row: InternalSynthesisEvidenceRow,
) -> api.SynthesisEvidenceRow:
    """Map an internal synthesis evidence row into the API schema."""

    return api.SynthesisEvidenceRow(
        row_id=row.row_id,
        citation_key=row.citation_key,
        rank=row.rank,
        paper_title=row.paper_title,
        year=row.year,
        venue=row.venue,
        sources=list(row.sources),
        identifiers=api.PaperIdentifiers(
            doi=row.identifiers.doi,
            arxiv_id=row.identifiers.arxiv_id,
            semantic_scholar_id=row.identifiers.semantic_scholar_id,
            openalex_id=row.identifiers.openalex_id,
            pubmed_id=row.identifiers.pubmed_id,
        ),
        category=row.category,
        final_score=row.final_score,
        evidence_source=row.evidence_source,
        evidence_text=row.evidence_text,
        supported_terms=list(row.supported_terms),
        supported_claim=row.supported_claim,
    )


def map_synthesis_finding(
    finding: InternalSynthesisFinding,
) -> api.SynthesisFinding:
    """Map an internal synthesis finding into the API schema."""

    return api.SynthesisFinding(
        text=finding.text,
        citation_keys=list(finding.citation_keys),
        confidence=finding.confidence,
        evidence_row_ids=list(finding.evidence_row_ids),
    )


def map_citation_coverage(
    coverage: InternalCitationCoverage,
) -> api.CitationCoverage:
    """Map internal citation coverage counters into the API schema."""

    return api.CitationCoverage(
        ranked_paper_count=coverage.ranked_paper_count,
        cited_paper_count=coverage.cited_paper_count,
        evidence_row_count=coverage.evidence_row_count,
        cited_evidence_row_count=coverage.cited_evidence_row_count,
        missing_evidence_count=coverage.missing_evidence_count,
        source_error_count=coverage.source_error_count,
        coverage_ratio=coverage.coverage_ratio,
    )


def map_query_analysis(query_analysis: InternalQueryAnalysis) -> api.QueryAnalysis:
    """Map internal query analysis into the API query analysis schema."""

    constraints = query_analysis.constraints
    time_range = constraints.time_range
    constraint_payload = {
        "time_range": (
            {
                "start_year": time_range.start_year,
                "end_year": time_range.end_year,
                "label": time_range.label,
            }
            if time_range is not None
            else None
        ),
        "venues": list(constraints.venues),
        "methods": list(constraints.methods),
        "datasets": list(constraints.datasets),
        "domains": list(constraints.domains),
        "must_have_terms": list(constraints.must_include_terms),
        "excluded_terms": list(constraints.exclude_terms),
        "language": query_analysis.language,
        "needs_expansion": query_analysis.needs_expansion,
    }
    topics = _dedupe(
        constraints.methods
        + constraints.datasets
        + constraints.domains
        + constraints.must_include_terms
    )
    return api.QueryAnalysis(
        intent_type=query_analysis.intent,
        domain=query_analysis.domain,
        research_topics=topics,
        constraints=constraint_payload,
    )


def map_search_plan(
    search_plan: InternalSearchPlan,
    output: SearchServiceOutput | None = None,
) -> api.SearchPlan:
    """Map internal search plan into the API search plan schema."""

    expanded_queries = [subquery.query for subquery in search_plan.subqueries]
    if output is not None:
        for record in output.query_evolution_records:
            expanded_queries.extend(query.query for query in record.generated_queries)
    return api.SearchPlan(
        expanded_queries=_dedupe(expanded_queries),
        source_preferences=list(search_plan.selected_sources),
        max_rounds=_search_rounds(output, search_plan),
    )


def _cost_report(output: SearchServiceOutput) -> api.CostReport:
    search_api_call_count = len(output.source_stats)
    cache_hit_count = sum(1 for stats in output.source_stats if stats.cache_hit)
    return api.CostReport(
        api_call_count=search_api_call_count,
        search_api_call_count=search_api_call_count,
        llm_call_count=output.llm_call_count,
        estimated_input_tokens=0,
        estimated_output_tokens=0,
        estimated_total_tokens=0,
        latency_seconds=output.latency_seconds,
        cache_hit_count=cache_hit_count,
        search_rounds=_search_rounds(output, output.search_plan),
        judged_paper_count=len(output.judgements),
    )


def _search_rounds(
    output: SearchServiceOutput | None,
    search_plan: InternalSearchPlan,
) -> int:
    if output is None:
        return max(1, len(search_plan.subqueries))
    rounds = len(output.retrieval_outputs)
    if output.refchain_output is not None:
        rounds += 1
    return max(1, rounds)


def _method_clusters(
    query_analysis: InternalQueryAnalysis,
    ranked_papers: list[api.RankedPaper],
) -> list[api.MethodCluster]:
    clusters: list[api.MethodCluster] = []
    for method in query_analysis.constraints.methods:
        method_key = method.casefold()
        paper_ranks = [
            paper.rank
            for paper in ranked_papers
            if any(term.casefold() == method_key for term in paper.matched_constraints)
        ]
        if paper_ranks:
            clusters.append(
                api.MethodCluster(
                    name=method,
                    paper_ranks=paper_ranks,
                    summary=f"Papers matched the query method constraint: {method}.",
                )
            )
    return clusters


def _timeline(ranked_papers: list[api.RankedPaper]) -> list[api.TimelineItem]:
    ranks_by_year: dict[int, list[int]] = defaultdict(list)
    for paper in ranked_papers:
        if paper.paper.year > 0:
            ranks_by_year[paper.paper.year].append(paper.rank)
    return [
        api.TimelineItem(
            year=year,
            paper_ranks=sorted(ranks),
            summary=f"{len(ranks)} mapped paper(s) in {year}.",
        )
        for year, ranks in sorted(ranks_by_year.items())
    ]


def _citation_graph(
    ranked_papers: list[api.RankedPaper],
    output: SearchServiceOutput,
) -> api.CitationGraph:
    node_by_id: dict[str, api.CitationGraphNode] = {}

    for ranked in ranked_papers:
        node_id = _paper_node_id(ranked.paper)
        if node_id is None:
            continue
        node_by_id[node_id] = api.CitationGraphNode(
            id=node_id,
            label=ranked.paper.title,
            rank=ranked.rank,
        )

    edges: list[api.CitationGraphEdge] = []
    if output.refchain_output is not None:
        for edge in output.refchain_output.reference_edges:
            if edge.seed_paper_id not in node_by_id:
                node_by_id[edge.seed_paper_id] = api.CitationGraphNode(
                    id=edge.seed_paper_id,
                    label=edge.seed_paper_id,
                )
            if edge.reference_paper_id not in node_by_id:
                node_by_id[edge.reference_paper_id] = api.CitationGraphNode(
                    id=edge.reference_paper_id,
                    label=edge.reference_paper_id,
                )
            edges.append(
                api.CitationGraphEdge(
                    source=edge.seed_paper_id,
                    target=edge.reference_paper_id,
                    relation=edge.relation,
                )
            )

    return api.CitationGraph(
        nodes=list(node_by_id.values()),
        edges=edges,
    )


def _missing_evidence(output: SearchServiceOutput) -> list[str]:
    missing: list[str] = []
    missing.extend(output.warnings)

    for stats in output.source_stats:
        if stats.error_message:
            missing.append(f"source_error:{stats.source}:{stats.error_message}")

    for record in output.query_evolution_records:
        missing.append(
            "query_evolution:"
            f"round={record.round_index}:"
            f"seed_count={record.seed_count}:"
            f"generated_count={len(record.generated_queries)}"
        )
        missing.extend(
            f"query_evolution_warning:{warning}" for warning in record.warnings
        )
        missing.extend(
            f"query_evolution_skipped:{reason}" for reason in record.skipped_reasons
        )

    if output.refchain_output is not None:
        record = output.refchain_output.record
        missing.append(
            "refchain:"
            f"seed_count={len(record.seeds)}:"
            f"returned_reference_count={record.returned_reference_count}"
        )
        missing.extend(f"refchain_warning:{warning}" for warning in record.warnings)
        missing.extend(
            f"refchain_skipped:{reason}" for reason in record.skipped_reasons
        )

    return missing


def _paper_node_id(paper: api.Paper) -> str | None:
    identifiers = paper.identifiers
    if identifiers.openalex_id:
        return f"openalex:{identifiers.openalex_id.casefold()}"
    if identifiers.doi:
        return f"doi:{identifiers.doi.casefold()}"
    if identifiers.arxiv_id:
        return f"arxiv:{identifiers.arxiv_id.casefold()}"
    if identifiers.semantic_scholar_id:
        return f"s2:{identifiers.semantic_scholar_id.casefold()}"
    if identifiers.pubmed_id:
        return f"pubmed:{identifiers.pubmed_id.casefold()}"
    return None


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        deduped.append(item)
        seen.add(item)
    return deduped

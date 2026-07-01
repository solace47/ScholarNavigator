from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scholar_agent.agents.retriever import RetrievalOutput, SourceStats  # noqa: E402
from scholar_agent.agents.synthesis import synthesize_answer  # noqa: E402
from scholar_agent.app.main import app  # noqa: E402
from scholar_agent.core.paper_schemas import (  # noqa: E402
    Paper,
    PaperIdentifiers,
    PaperUrls,
)
from scholar_agent.core.search_schemas import (  # noqa: E402
    EvolvedSubquery,
    EvidenceItem,
    JudgementResult,
    QueryAnalysis,
    QueryConstraint,
    QueryEvolutionRecord,
    RankedPaper,
    RefChainOutput,
    RefChainRecord,
    RefChainSeed,
    ReferenceEdge,
    RerankScoreBreakdown,
    SearchPlan,
    SearchSubquery,
    TimeRange,
)
from scholar_agent.services.api_mapper import (  # noqa: E402
    map_search_service_output_to_api_result,
)
from scholar_agent.services.search_service import SearchServiceOutput  # noqa: E402


client = TestClient(app)


def test_minimal_search_service_output_maps_successfully() -> None:
    output = SearchServiceOutput(
        search_plan=_search_plan("LLM reranking papers"),
        latency_seconds=0.01,
    )

    response = map_search_service_output_to_api_result("run_real_1", output)

    assert response.run_id == "run_real_1"
    assert response.status == "succeeded"
    assert response.partial is False
    assert response.query_analysis.intent_type == "paper_finding"
    assert response.search_plan.expanded_queries == ["LLM reranking papers"]
    assert response.highly_relevant_papers == []
    assert response.partially_relevant_papers == []
    assert response.synthesis is None
    assert response.cost_report.llm_call_count == 0
    assert response.cost_report.cache_hit_count == 0


def test_paper_fields_identifiers_urls_and_sources_are_preserved() -> None:
    paper = _paper(
        "Mapped Paper",
        doi="10.123/mapped",
        arxiv_id="2401.00001",
        openalex_id="W123",
        semantic_scholar_id="S2-123",
        pubmed_id="PMID-123",
        sources=["openalex", "arxiv"],
    )
    output = _output_with_ranked([_ranked(paper, rank=1, category="highly_relevant")])

    response = map_search_service_output_to_api_result("run_real_2", output)
    mapped = response.highly_relevant_papers[0].paper

    assert mapped.title == "Mapped Paper"
    assert mapped.authors == ["Alice", "Bob"]
    assert mapped.year == 2025
    assert mapped.venue == "ACL"
    assert mapped.abstract
    assert mapped.identifiers.doi == "10.123/mapped"
    assert mapped.identifiers.arxiv_id == "2401.00001"
    assert mapped.identifiers.openalex_id == "W123"
    assert mapped.identifiers.semantic_scholar_id == "S2-123"
    assert mapped.identifiers.pubmed_id == "PMID-123"
    assert mapped.urls.landing_page == "https://example.test/mapped-paper"
    assert mapped.urls.pdf == "https://example.test/mapped-paper.pdf"
    assert mapped.sources == ["openalex", "arxiv"]


def test_highly_partial_and_filtered_categories_are_mapped_correctly() -> None:
    ranked = [
        _ranked(_paper("Highly", doi="10.123/high"), rank=1, category="highly_relevant"),
        _ranked(
            _paper("Partial", doi="10.123/partial"),
            rank=2,
            category="partially_relevant",
        ),
        _ranked(_paper("Weak", doi="10.123/weak"), rank=3, category="weakly_relevant"),
        _ranked(
            _paper("Irrelevant", doi="10.123/irrelevant"),
            rank=4,
            category="irrelevant",
        ),
        _ranked(
            _paper("Insufficient", doi="10.123/insufficient"),
            rank=5,
            category="insufficient_evidence",
            warnings=["missing_title"],
        ),
    ]
    output = _output_with_ranked(ranked)

    response = map_search_service_output_to_api_result("run_real_3", output)

    assert [item.paper.title for item in response.highly_relevant_papers] == ["Highly"]
    assert [item.paper.title for item in response.partially_relevant_papers] == [
        "Partial",
        "Weak",
    ]
    assert any("filtered_paper:4:irrelevant:Irrelevant" == item for item in response.missing_evidence)
    assert any(
        "filtered_paper:5:insufficient_evidence:Insufficient" == item
        for item in response.missing_evidence
    )
    assert "filtered_paper_warning:5:missing_title" in response.missing_evidence


def test_warnings_and_source_errors_enter_missing_evidence_and_cost_report() -> None:
    ranked = [_ranked(_paper("Highly", doi="10.123/high"), rank=1)]
    output = _output_with_ranked(
        ranked,
        warnings=["retrieval_warning"],
        source_stats=[
            SourceStats(
                source="openalex",
                returned_count=0,
                latency_seconds=0.2,
                error_message="HTTP 503",
            ),
            SourceStats(
                source="arxiv",
                returned_count=1,
                latency_seconds=0.1,
            ),
        ],
        retrieval_output_count=2,
    )

    response = map_search_service_output_to_api_result("run_real_4", output)

    assert "retrieval_warning" in response.missing_evidence
    assert "source_error:openalex:HTTP 503" in response.missing_evidence
    assert response.cost_report.search_api_call_count == 2
    assert response.cost_report.api_call_count == 2
    assert response.cost_report.search_rounds == 2
    assert response.cost_report.judged_paper_count == 1
    assert response.cost_report.llm_call_count == 0


def test_query_evolution_and_refchain_debug_info_do_not_crash_mapper() -> None:
    seed = _ranked(_paper("Seed", doi="10.123/seed", openalex_id="WSEED"), rank=1)
    reference = _paper("Reference", doi="10.123/ref", openalex_id="WREF")
    edge = ReferenceEdge(
        seed_paper_id="openalex:wseed",
        reference_paper_id="openalex:wref",
        source="openalex",
    )
    output = _output_with_ranked(
        [seed],
        query_evolution_records=[
            QueryEvolutionRecord(
                seed_count=1,
                generated_queries=[
                    EvolvedSubquery(
                        query="LLM reranking recent advances",
                        source_hints=["openalex", "arxiv"],
                        priority=1,
                        purpose="query_evolution_recent_progress",
                    )
                ],
                warnings=["qe_warning"],
            )
        ],
        refchain_output=RefChainOutput(
            references=[reference],
            reference_edges=[edge],
            record=RefChainRecord(
                seeds=[
                    RefChainSeed(
                        paper=seed.paper,
                        rank=1,
                        score=0.9,
                        reason="seed reason",
                    )
                ],
                reference_edges=[edge],
                raw_reference_count=1,
                returned_reference_count=1,
                warnings=["refchain_warning"],
            ),
            warnings=["refchain_warning"],
        ),
    )

    response = map_search_service_output_to_api_result("run_real_5", output)

    assert "LLM reranking recent advances" in response.search_plan.expanded_queries
    assert any(item.startswith("query_evolution:round=1") for item in response.missing_evidence)
    assert "query_evolution_warning:qe_warning" in response.missing_evidence
    assert "refchain:seed_count=1:returned_reference_count=1" in response.missing_evidence
    assert "refchain_warning:refchain_warning" in response.missing_evidence
    assert response.citation_graph.edges[0].source == "openalex:wseed"
    assert response.citation_graph.edges[0].target == "openalex:wref"


def test_synthesis_output_maps_to_api_result_synthesis() -> None:
    ranked = [
        _ranked(_paper("Highly", doi="10.123/high"), rank=1, category="highly_relevant"),
        _ranked(
            _paper("Partial", doi="10.123/partial"),
            rank=2,
            category="partially_relevant",
        ),
    ]
    output = _output_with_ranked(ranked)
    output.synthesis_output = synthesize_answer(output)

    response = map_search_service_output_to_api_result("run_real_synthesis", output)

    assert response.synthesis is not None
    assert response.synthesis.status == "succeeded"
    assert response.synthesis.evidence_table[0].citation_key == "R1"
    assert response.synthesis.evidence_table[0].identifiers.doi == "10.123/high"
    assert response.synthesis.key_findings[0].citation_keys == ["R1"]
    assert response.synthesis.citation_coverage.ranked_paper_count == 2
    assert response.highly_relevant_papers[0].paper.title == "Highly"
    assert response.partially_relevant_papers[0].paper.title == "Partial"


def test_none_synthesis_output_is_valid_api_result() -> None:
    output = _output_with_ranked(
        [_ranked(_paper("Highly", doi="10.123/high"), rank=1)]
    )
    output.synthesis_output = None

    response = map_search_service_output_to_api_result("run_real_no_synthesis", output)

    assert response.synthesis is None
    assert response.highly_relevant_papers
    assert response.partially_relevant_papers == []


def test_existing_mock_search_runs_api_behavior_is_unchanged() -> None:
    create_response = client.post(
        "/api/v1/search/runs",
        json={"query": "请帮我搜索关于 LLM reranking 的代表性论文"},
    )

    assert create_response.status_code == 201
    run_id = create_response.json()["run_id"]
    result_response = client.get(f"/api/v1/search/runs/{run_id}/result")

    assert result_response.status_code == 200
    body = result_response.json()
    assert body["run_id"] == run_id
    assert body["status"] == "succeeded"
    assert body["synthesis"] is None
    assert body["highly_relevant_papers"][0]["paper"]["title"].startswith("SPAR:")
    assert body["cost_report"]["api_call_count"] == 7


def _search_plan(query: str = "LLM reranking retrieval") -> SearchPlan:
    query_analysis = QueryAnalysis(
        original_query=query,
        language="en",
        intent="paper_finding",
        domain="machine_learning",
        constraints=QueryConstraint(
            time_range=TimeRange(start_year=2020, end_year=2026),
            methods=["reranking"],
            datasets=["scientific literature"],
            domains=["machine_learning"],
            must_include_terms=["LLM", "retrieval"],
        ),
    )
    return SearchPlan(
        query_analysis=query_analysis,
        subqueries=[
            SearchSubquery(
                query=query,
                source_hints=["openalex", "arxiv"],
                priority=1,
                purpose="original_query",
            )
        ],
        selected_sources=["openalex", "arxiv"],
        limit_per_source=20,
        top_k=20,
        run_profile="balanced",
    )


def _paper(
    title: str,
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    openalex_id: str | None = None,
    semantic_scholar_id: str | None = None,
    pubmed_id: str | None = None,
    sources: list[str] | None = None,
) -> Paper:
    slug = title.casefold().replace(" ", "-")
    return Paper(
        title=title,
        authors=["Alice", "Bob"],
        year=2025,
        venue="ACL",
        abstract="A mapped paper about LLM reranking and scientific retrieval.",
        identifiers=PaperIdentifiers(
            doi=doi,
            arxiv_id=arxiv_id,
            openalex_id=openalex_id,
            semantic_scholar_id=semantic_scholar_id,
            pubmed_id=pubmed_id,
        ),
        urls=PaperUrls(
            landing_page=f"https://example.test/{slug}",
            pdf=f"https://example.test/{slug}.pdf",
        ),
        sources=sources or ["openalex"],
        citation_count=42,
    )


def _ranked(
    paper: Paper,
    *,
    rank: int,
    category: str = "highly_relevant",
    final_score: float = 0.9,
    warnings: list[str] | None = None,
) -> RankedPaper:
    return RankedPaper(
        rank=rank,
        paper=paper,
        final_score=final_score,
        category=category,
        score_breakdown=RerankScoreBreakdown(
            relevance_score=0.9,
            authority_score=0.5,
            timeliness_score=0.7,
            metadata_score=0.8,
            final_score=final_score,
            relevance_weight=0.65,
            authority_weight=0.1,
            timeliness_weight=0.2,
            metadata_weight=0.05,
        ),
        ranking_reason="metadata-only ranking",
        evidence=[EvidenceItem(source="title", text=paper.title, confidence=0.9)],
        matched_terms=["LLM", "retrieval", "reranking"],
        warnings=warnings or [],
    )


def _output_with_ranked(
    ranked_papers: list[RankedPaper],
    *,
    warnings: list[str] | None = None,
    source_stats: list[SourceStats] | None = None,
    retrieval_output_count: int = 1,
    query_evolution_records: list[QueryEvolutionRecord] | None = None,
    refchain_output: RefChainOutput | None = None,
) -> SearchServiceOutput:
    retrieval_outputs = [
        RetrievalOutput(
            query=f"query {index}",
            requested_sources=["openalex", "arxiv"],
            raw_count=1,
            deduplicated_count=1,
            papers=[ranked_papers[0].paper] if ranked_papers else [],
            source_stats=[],
            warnings=[],
            latency_seconds=0.01,
        )
        for index in range(retrieval_output_count)
    ]
    return SearchServiceOutput(
        search_plan=_search_plan(),
        retrieval_outputs=retrieval_outputs,
        query_evolution_records=query_evolution_records or [],
        refchain_output=refchain_output,
        raw_count=len(ranked_papers),
        deduplicated_count=len(ranked_papers),
        judgements=[
            JudgementResult(
                paper=ranked.paper,
                score=ranked.final_score,
                category=ranked.category,
                reasoning="metadata judgement",
                evidence=ranked.evidence,
                matched_terms=ranked.matched_terms,
            )
            for ranked in ranked_papers
        ],
        ranked_papers=ranked_papers,
        warnings=warnings or [],
        source_stats=source_stats
        or [
            SourceStats(
                source="openalex",
                returned_count=len(ranked_papers),
                latency_seconds=0.01,
            )
        ],
        latency_seconds=0.25,
    )

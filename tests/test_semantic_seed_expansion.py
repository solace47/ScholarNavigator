from __future__ import annotations

from scholar_agent.agents.semantic_seed_expansion import expand_semantic_seeds
from scholar_agent.agents.retriever import RetrievalOutput, SourceStats
from scholar_agent.connectors.schemas import ConnectorSearchResult
from scholar_agent.core.dedup import deduplicate_papers_with_audit
from scholar_agent.core.diagnostics_schemas import ConnectorDiagnostics
from scholar_agent.core.evaluation_schemas import EvalGoldPaper, EvalQuery
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers
from scholar_agent.core.search_schemas import (
    EvidenceItem,
    RankedPaper,
    RerankScoreBreakdown,
)
from scholar_agent.evaluation.snapshots import (
    SnapshotManifest,
    SnapshotRuntime,
    SnapshotStore,
)
from scholar_agent.evaluation.snapshots.store import connector_version, utc_now
from scholar_agent.evaluation.stage_diagnostics import analyze_search_stages
from scholar_agent.services.search_service import SearchService


def _ranked(
    rank: int,
    *,
    semantic_id: str | None,
    doi: str | None = None,
) -> RankedPaper:
    paper = Paper(
        title=f"Seed {rank}",
        authors=["Ada"],
        year=2024,
        identifiers=PaperIdentifiers(
            semantic_scholar_id=semantic_id,
            doi=doi,
        ),
        sources=["semantic_scholar"],
    )
    return RankedPaper(
        rank=rank,
        paper=paper,
        final_score=0.8,
        category="highly_relevant",
        score_breakdown=RerankScoreBreakdown(
            relevance_score=0.8,
            authority_score=0.5,
            timeliness_score=0.5,
            metadata_score=0.5,
            final_score=0.8,
            relevance_weight=0.62,
            authority_weight=0.25,
            timeliness_weight=0.08,
            metadata_weight=0.05,
        ),
        ranking_reason="test",
        evidence=[EvidenceItem(source="title", text=paper.title, confidence=0.8)],
    )


def test_expansion_selects_first_three_unique_exact_s2_ids() -> None:
    captured: list[tuple[list[str], int]] = []
    ranked = [
        _ranked(1, semantic_id=None),
        _ranked(2, semantic_id="S2-A"),
        _ranked(3, semantic_id="s2-a"),
        _ranked(4, semantic_id="S2-B"),
        _ranked(5, semantic_id="S2-C"),
        _ranked(6, semantic_id="S2-D"),
    ]

    output = expand_semantic_seeds(
        ranked,
        lambda seeds, limit: (
            captured.append((list(seeds), limit))
            or ConnectorSearchResult(papers=[Paper(title="Recommendation")])
        ),
    )

    assert captured == [(["S2-A", "S2-B", "S2-C"], 100)]
    assert [seed.rank for seed in output.record.seeds] == [2, 4, 5]
    assert output.record.status == "success"
    assert len(output.recommendations) == 1


def test_expansion_without_s2_seed_does_not_call_source() -> None:
    called = False

    def fetcher(seeds: list[str], limit: int) -> ConnectorSearchResult:
        nonlocal called
        called = True
        return ConnectorSearchResult()

    output = expand_semantic_seeds(
        [_ranked(1, semantic_id=None, doi="10.1/only-doi")],
        fetcher,
    )

    assert called is False
    assert output.recommendations == []
    assert output.record.status == "no_eligible_seed"
    assert output.record.skip_reason == "no_eligible_seed"


def test_expansion_source_failure_preserves_empty_delta() -> None:
    output = expand_semantic_seeds(
        [_ranked(1, semantic_id="S2-A")],
        lambda seeds, limit: ConnectorSearchResult(
            error_message="HTTP 429",
            diagnostics=ConnectorDiagnostics(
                request_count=2,
                retry_count=1,
                error_count=1,
            ),
        ),
    )

    assert output.recommendations == []
    assert output.record.status == "source_failure"
    assert output.record.skip_reason == "source_failure"
    assert output.diagnostics.request_count == 2


def test_recommendation_dedup_keeps_duplicate_merged_and_conflict_separate() -> None:
    initial = Paper(
        title="Initial",
        identifiers=PaperIdentifiers(
            semantic_scholar_id="S2-A",
            doi="10.1/a",
        ),
    )
    duplicate = Paper(
        title="Duplicate",
        identifiers=PaperIdentifiers(semantic_scholar_id="s2-a"),
    )
    conflict = Paper(
        title="Conflict",
        identifiers=PaperIdentifiers(
            semantic_scholar_id="S2-A",
            doi="10.1/conflict",
        ),
    )

    papers, audit = deduplicate_papers_with_audit([initial, duplicate, conflict])

    assert len(papers) == 2
    assert len(audit) == 1
    assert audit[0]["rule"] == "shared_stable_identifier"


def test_recommendation_snapshot_record_and_replay_is_deterministic(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    now = utc_now()
    store.ensure_manifest(
        SnapshotManifest(
            snapshot_name=tmp_path.name,
            dataset="auto_scholar_query",
            split="development",
            offset=0,
            limit=1,
            sources=["semantic_scholar"],
            adapter_policy="adaptive",
            run_profile="balanced",
            budgets={"max_search_rounds": 2},
            llm_enabled=False,
            query_understanding_prompt={"name": "query_understanding"},
            judgement_prompt={"name": "relevance_judgement"},
            connector_versions={
                "semantic_scholar": connector_version("semantic_scholar"),
                "semantic_scholar_recommendations": connector_version(
                    "semantic_scholar_recommendations"
                ),
            },
            code_hash="test",
            dirty_worktree=False,
            created_at=now,
            updated_at=now,
        )
    )
    calls = 0

    def fetcher(seeds: list[str], limit: int) -> ConnectorSearchResult:
        nonlocal calls
        calls += 1
        return ConnectorSearchResult(
            papers=[
                Paper(
                    title="Recorded recommendation",
                    identifiers=PaperIdentifiers(semantic_scholar_id="REC-1"),
                )
            ],
            diagnostics=ConnectorDiagnostics(request_count=1),
        )

    record = SnapshotRuntime(
        store,
        mode="record-missing",
        group_name="semantic_seed_expansion",
    )
    record.begin_case("case-1")
    recorded = record.fetch_recommendations(["Seed-A", "seed-a", "Seed-B"], 100, fetcher)
    record.finish_group(completed=True)

    replay = SnapshotRuntime(
        store,
        mode="replay",
        group_name="semantic_seed_expansion",
    )
    replay.begin_case("case-1")
    replayed = replay.fetch_recommendations(
        ["Seed-A", "Seed-B"],
        100,
        lambda seeds, limit: (_ for _ in ()).throw(AssertionError("HTTP called")),
    )

    assert calls == 1
    assert recorded.snapshot_provenance == "snapshot_record"
    assert replayed.snapshot_provenance == "snapshot_replay"
    assert replayed.snapshot_hit is True
    assert replayed.diagnostics.request_count == 0
    assert replayed.recorded_diagnostics == ConnectorDiagnostics(request_count=1)
    assert replayed.papers == recorded.papers
    entry = store.read_reference(str(recorded.snapshot_key))
    assert entry.source == "semantic_scholar"
    assert entry.seed_identifiers == ["Seed-A", "Seed-B"]
    assert entry.connector_version == "recommendations-v1"


def test_search_service_expands_after_unchanged_initial_ranking() -> None:
    initial = Paper(
        title="Graph retrieval systems",
        authors=["Ada"],
        year=2024,
        abstract="Graph retrieval systems for scientific search.",
        identifiers=PaperIdentifiers(semantic_scholar_id="SEED-1"),
        sources=["semantic_scholar"],
    )
    recommended = Paper(
        title="Related graph retrieval method",
        authors=["Bob"],
        year=2023,
        abstract="A related graph retrieval method.",
        identifiers=PaperIdentifiers(semantic_scholar_id="REC-1"),
        sources=["semantic_scholar"],
    )
    recommendation_calls: list[tuple[list[str], int]] = []

    def retrieve(query: str, *, limit_per_source: int, sources: list[str]):
        return RetrievalOutput(
            query=query,
            requested_sources=sources,
            raw_count=1,
            deduplicated_count=1,
            papers=[initial],
            source_stats=[
                SourceStats(
                    source="semantic_scholar",
                    query=query,
                    returned_count=1,
                    diagnostic_papers=[initial],
                )
            ],
        )

    service = SearchService(
        retriever=lambda query, limit_per_source, sources: retrieve(
            query,
            limit_per_source=limit_per_source,
            sources=sources,
        ),
        recommendation_fetcher=lambda seeds, limit: (
            recommendation_calls.append((list(seeds), limit))
            or ConnectorSearchResult(
                papers=[recommended, initial],
                diagnostics=ConnectorDiagnostics(request_count=1),
            )
        ),
        max_workers=1,
    )

    baseline = service.run_search(
        "graph retrieval systems",
        enable_synthesis=False,
        sources_override=["semantic_scholar"],
        collect_diagnostics=True,
    )
    expanded = service.run_search(
        "graph retrieval systems",
        enable_semantic_seed_expansion=True,
        enable_synthesis=False,
        sources_override=["semantic_scholar"],
        collect_diagnostics=True,
    )

    assert recommendation_calls == [(["SEED-1"], 100)]
    assert baseline.semantic_seed_expansion_output is None
    assert expanded.semantic_seed_expansion_output is not None
    assert expanded.semantic_seed_expansion_output.record.status == "success"
    assert expanded.semantic_seed_expansion_output.record.new_unique_candidate_count == 1
    assert expanded.semantic_seed_expansion_output.record.duplicate_candidate_count == 1
    snapshots = {item.stage: item for item in expanded.stage_snapshots}
    assert [item.title for item in snapshots["initial_reranked"].candidates] == [
        "Graph retrieval systems"
    ]
    assert {
        item.title
        for item in snapshots["post_semantic_seed_expansion_deduplicated"].candidates
    } == {"Graph retrieval systems", "Related graph retrieval method"}
    stage_diagnostics = analyze_search_stages(
        EvalQuery(
            query_id="case-1",
            query="graph retrieval systems",
            gold_papers=[
                EvalGoldPaper(
                    title=recommended.title,
                    semantic_scholar_id="REC-1",
                )
            ],
        ),
        expanded,
        result_policy="highly_and_partial",
    )
    assert stage_diagnostics["stage_metrics"]["candidate_recall"][
        "initial_deduplicated"
    ] == 0.0
    assert stage_diagnostics["stage_metrics"]["candidate_recall"][
        "post_semantic_seed_expansion_deduplicated"
    ] == 1.0
    assert stage_diagnostics["semantic_seed_expansion"][
        "new_unique_reference_gold_count"
    ] == 1
    assert stage_diagnostics["semantic_seed_expansion"][
        "candidate_recall_before"
    ] == 0.0
    assert stage_diagnostics["semantic_seed_expansion"][
        "candidate_recall_after"
    ] == 1.0
    assert stage_diagnostics["semantic_seed_expansion"][
        "initial_gold_lost_after_expansion_count"
    ] == 0


def test_search_service_recommendation_failure_keeps_initial_results() -> None:
    initial = Paper(
        title="Initial stable result",
        authors=["Ada"],
        year=2024,
        identifiers=PaperIdentifiers(semantic_scholar_id="SEED-1"),
        sources=["semantic_scholar"],
    )

    def retriever(query: str, limit_per_source: int, sources: list[str]):
        return RetrievalOutput(
            query=query,
            requested_sources=sources,
            raw_count=1,
            deduplicated_count=1,
            papers=[initial],
            source_stats=[SourceStats(source="semantic_scholar", returned_count=1)],
        )

    service = SearchService(
        retriever=lambda query, limit_per_source, sources: retriever(
            query, limit_per_source, sources
        ),
        recommendation_fetcher=lambda seeds, limit: ConnectorSearchResult(
            error_message="Semantic Scholar recommendations returned 429",
            diagnostics=ConnectorDiagnostics(request_count=2, retry_count=1, error_count=1),
        ),
        max_workers=1,
    )
    baseline = service.run_search(
        "stable result",
        enable_synthesis=False,
        sources_override=["semantic_scholar"],
    )
    expanded = service.run_search(
        "stable result",
        enable_semantic_seed_expansion=True,
        enable_synthesis=False,
        sources_override=["semantic_scholar"],
        collect_diagnostics=True,
    )

    assert expanded.semantic_seed_expansion_output is not None
    assert expanded.semantic_seed_expansion_output.record.status == "source_failure"
    assert [item.paper for item in expanded.all_ranked_papers] == [
        item.paper for item in baseline.all_ranked_papers
    ]
    diagnostics = analyze_search_stages(
        EvalQuery(
            query_id="case-failure",
            query="stable result",
            gold_papers=[
                EvalGoldPaper(
                    title=initial.title,
                    semantic_scholar_id="SEED-1",
                )
            ],
        ),
        expanded,
        result_policy="highly_and_partial",
    )["semantic_seed_expansion"]
    assert diagnostics["candidate_recall_before"] == 1.0
    assert diagnostics["candidate_recall_after"] == 1.0
    assert diagnostics["initial_gold_lost_after_expansion_count"] == 0

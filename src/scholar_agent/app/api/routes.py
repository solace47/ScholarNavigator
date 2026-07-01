"""FastAPI routes for the mock backend API."""

from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...core.api_schemas import (
    CitationGraph,
    CitationGraphEdge,
    CitationGraphNode,
    ConnectorRuntimeConfig,
    CostReport,
    EvidenceItem,
    HealthResponse,
    LLMRuntimeConfig,
    MethodCluster,
    Paper,
    PaperIdentifiers,
    PaperUrls,
    QueryAnalysis,
    RankedPaper,
    RunProgress,
    RuntimeConfigResponse,
    RuntimeFeatures,
    RuntimeLimits,
    SearchPlan,
    SearchRunCreateRequest,
    SearchRunCreateResponse,
    SearchRunResultResponse,
    SearchRunStatusResponse,
    TimelineItem,
)
from ...core.search_schemas import RunProfile
from ...services.api_mapper import map_search_service_output_to_api_result
from ...services.search_service import SearchService


API_VERSION = "0.1.0"
DEFAULT_REAL_PREVIEW_MAX_WORKERS = 2
REAL_PREVIEW_MAX_WORKERS_ENV = "REAL_PREVIEW_MAX_WORKERS"
DEFAULT_REAL_SEARCH_MAX_WORKERS = 2
REAL_SEARCH_MAX_WORKERS_ENV = "REAL_SEARCH_MAX_WORKERS"
DEFAULT_REAL_SEARCH_BACKGROUND_WORKERS = 2
REAL_SEARCH_BACKGROUND_WORKERS_ENV = "REAL_SEARCH_BACKGROUND_WORKERS"

router = APIRouter(prefix="/api/v1", tags=["mock-api"])


@dataclass
class MockRun:
    run_id: str
    request: SearchRunCreateRequest
    created_at: datetime
    updated_at: datetime


@dataclass
class RealRun:
    run_id: str
    request: SearchRunCreateRequest
    status: str
    current_stage: str
    progress: RunProgress
    cost_report: CostReport
    result: SearchRunResultResponse | None
    events: list[tuple[str, dict[str, Any]]]
    error_message: str | None
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime


class InternalSearchPreviewRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=20, ge=1, le=100)
    run_profile: RunProfile = "balanced"
    enable_refchain: bool = False
    enable_query_evolution: bool = False
    current_year: int | None = Field(default=None, ge=1900, le=2200)


class InternalSearchPreviewResponse(BaseModel):
    query_analysis: dict[str, Any]
    search_plan: dict[str, Any]
    query_evolution_records: list[dict[str, Any]]
    refchain_output: dict[str, Any] | None
    synthesis_output: dict[str, Any] | None
    ranked_papers: list[dict[str, Any]]
    raw_count: int
    deduplicated_count: int
    warnings: list[str]
    source_stats: list[dict[str, Any]]
    latency_seconds: float


_RUNS: dict[str, MockRun] = {}
_REAL_RUNS: dict[str, RealRun] = {}
_REAL_RUNS_LOCK = RLock()
_REAL_SEARCH_EXECUTOR: ThreadPoolExecutor | None = None
_REAL_SEARCH_EXECUTOR_LOCK = RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _links(run_id: str) -> dict[str, str]:
    return {
        "self": f"/api/v1/search/runs/{run_id}",
        "events": f"/api/v1/search/runs/{run_id}/events",
        "result": f"/api/v1/search/runs/{run_id}/result",
    }


def _real_links(run_id: str) -> dict[str, str]:
    return {
        "self": f"/api/v1/real/search/runs/{run_id}",
        "events": f"/api/v1/real/search/runs/{run_id}/events",
        "result": f"/api/v1/real/search/runs/{run_id}/result",
    }


def _get_run(run_id: str) -> MockRun:
    try:
        return _RUNS[run_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}") from exc


def _get_real_run(run_id: str) -> RealRun:
    with _REAL_RUNS_LOCK:
        try:
            return _REAL_RUNS[run_id]
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown real run_id: {run_id}",
            ) from exc


def _model_dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _max_workers_from_env(env_name: str, default: int) -> int:
    raw_value = os.getenv(env_name)
    if raw_value is None:
        return default
    try:
        return max(1, int(raw_value))
    except ValueError:
        return default


def _real_preview_max_workers() -> int:
    return _max_workers_from_env(
        REAL_PREVIEW_MAX_WORKERS_ENV,
        DEFAULT_REAL_PREVIEW_MAX_WORKERS,
    )


def _real_search_max_workers() -> int:
    return _max_workers_from_env(
        REAL_SEARCH_MAX_WORKERS_ENV,
        DEFAULT_REAL_SEARCH_MAX_WORKERS,
    )


def _real_search_background_workers() -> int:
    return _max_workers_from_env(
        REAL_SEARCH_BACKGROUND_WORKERS_ENV,
        DEFAULT_REAL_SEARCH_BACKGROUND_WORKERS,
    )


def _preview_search_service() -> SearchService:
    return SearchService(max_workers=_real_preview_max_workers())


def _real_search_service() -> SearchService:
    return SearchService(max_workers=_real_search_max_workers())


def _real_search_executor() -> ThreadPoolExecutor:
    global _REAL_SEARCH_EXECUTOR
    with _REAL_SEARCH_EXECUTOR_LOCK:
        if _REAL_SEARCH_EXECUTOR is None:
            _REAL_SEARCH_EXECUTOR = ThreadPoolExecutor(
                max_workers=_real_search_background_workers(),
                thread_name_prefix="real-search",
            )
        return _REAL_SEARCH_EXECUTOR


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(version=API_VERSION, time=_now())


@router.get("/runtime/config", response_model=RuntimeConfigResponse)
def runtime_config() -> RuntimeConfigResponse:
    return RuntimeConfigResponse(
        llm=LLMRuntimeConfig(
            provider="mock",
            model="mock-no-llm",
            available=False,
        ),
        connectors=[
            ConnectorRuntimeConfig(
                name="mock",
                available=True,
                requires_key=False,
            ),
            ConnectorRuntimeConfig(
                name="openalex",
                available=False,
                requires_key=False,
                reason="mock_api_only",
            ),
            ConnectorRuntimeConfig(
                name="arxiv",
                available=False,
                requires_key=False,
                reason="mock_api_only",
            ),
            ConnectorRuntimeConfig(
                name="semantic_scholar",
                available=False,
                requires_key=True,
                reason="mock_api_only",
            ),
            ConnectorRuntimeConfig(
                name="pubmed",
                available=False,
                requires_key=False,
                reason="mock_api_only",
            ),
        ],
        limits=RuntimeLimits(
            max_top_k=100,
            max_search_rounds=3,
            max_candidate_papers=300,
            max_latency_seconds=120,
        ),
        features=RuntimeFeatures(
            query_evolution=True,
            refchain=True,
            evaluation=False,
            sse=True,
        ),
    )


@router.post("/search/runs", response_model=SearchRunCreateResponse, status_code=201)
def create_search_run(request: SearchRunCreateRequest) -> SearchRunCreateResponse:
    run_id = f"run_{uuid4().hex[:12]}"
    timestamp = _now()
    _RUNS[run_id] = MockRun(
        run_id=run_id,
        request=request,
        created_at=timestamp,
        updated_at=timestamp,
    )
    return SearchRunCreateResponse(
        run_id=run_id,
        status="queued",
        created_at=timestamp,
        links=_links(run_id),
    )


@router.get("/search/runs/{run_id}", response_model=SearchRunStatusResponse)
def get_search_run(run_id: str) -> SearchRunStatusResponse:
    run = _get_run(run_id)
    return SearchRunStatusResponse(
        run_id=run.run_id,
        status="succeeded",
        current_stage="synthesis",
        progress=RunProgress(
            completed_stages=[
                "query_understanding",
                "retrieval",
                "deduplication",
                "judgement",
                "reranking",
                "synthesis",
            ],
            candidate_paper_count=42,
            judged_paper_count=18,
        ),
        cost_report=_mock_cost_report(),
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


@router.get("/search/runs/{run_id}/result", response_model=SearchRunResultResponse)
def get_search_result(run_id: str) -> SearchRunResultResponse:
    run = _get_run(run_id)
    return _mock_result(run)


@router.get("/search/runs/{run_id}/events")
def stream_search_events(run_id: str) -> StreamingResponse:
    run = _get_run(run_id)

    async def event_generator():
        for event_name, payload in _mock_sse_events(run):
            yield f"event: {event_name}\n"
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.01)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/real/search/runs",
    response_model=SearchRunCreateResponse,
    status_code=201,
    tags=["real-search"],
)
def create_real_search_run(request: SearchRunCreateRequest) -> SearchRunCreateResponse:
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")

    run_id = f"run_real_{uuid4().hex[:12]}"
    timestamp = _now()
    with _REAL_RUNS_LOCK:
        _REAL_RUNS[run_id] = RealRun(
            run_id=run_id,
            request=request,
            status="queued",
            current_stage="queued",
            progress=RunProgress(),
            cost_report=CostReport(),
            result=None,
            events=[],
            error_message=None,
            cancel_requested=False,
            created_at=timestamp,
            updated_at=timestamp,
        )
    _append_real_event(
        run_id,
        "run_started",
        {
            "query": request.query,
            "mode": "real_search",
            "status": "queued",
        },
    )
    _real_search_executor().submit(_execute_real_search_run, run_id)
    return SearchRunCreateResponse(
        run_id=run_id,
        status="queued",
        created_at=timestamp,
        links=_real_links(run_id),
    )


@router.get(
    "/real/search/runs/{run_id}",
    response_model=SearchRunStatusResponse,
    tags=["real-search"],
)
def get_real_search_run(run_id: str) -> SearchRunStatusResponse:
    with _REAL_RUNS_LOCK:
        run = _get_real_run(run_id)
        return _real_status_response(run)


@router.get(
    "/real/search/runs/{run_id}/result",
    response_model=SearchRunResultResponse,
    tags=["real-search"],
)
def get_real_search_result(run_id: str) -> SearchRunResultResponse:
    with _REAL_RUNS_LOCK:
        run = _get_real_run(run_id)
        if run.status in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="result not ready")
        if run.status == "cancelled":
            raise HTTPException(status_code=409, detail="run cancelled")
        if run.status == "failed":
            raise HTTPException(
                status_code=500,
                detail=run.error_message or "real search failed",
            )
        if run.result is None:
            raise HTTPException(status_code=409, detail="result not ready")
        return run.result


@router.post(
    "/real/search/runs/{run_id}/cancel",
    response_model=SearchRunStatusResponse,
    tags=["real-search"],
)
def cancel_real_search_run(run_id: str) -> SearchRunStatusResponse:
    with _REAL_RUNS_LOCK:
        run = _get_real_run(run_id)
        if run.status in {"queued", "running"}:
            run.status = "cancelled"
            run.current_stage = "cancelled"
            run.cancel_requested = True
            run.result = None
            run.error_message = "run cancelled"
            run.updated_at = _now()
            should_emit_cancel_events = True
        else:
            should_emit_cancel_events = False

    if should_emit_cancel_events:
        _append_real_event(run_id, "warning", {"message": "run cancelled"})
        _append_real_event(run_id, "run_completed", {"status": "cancelled"})

    with _REAL_RUNS_LOCK:
        return _real_status_response(_get_real_run(run_id))


@router.get("/real/search/runs/{run_id}/events", tags=["real-search"])
def stream_real_search_events(run_id: str) -> StreamingResponse:
    _get_real_run(run_id)

    async def event_generator():
        event_index = 0
        while True:
            with _REAL_RUNS_LOCK:
                run = _REAL_RUNS.get(run_id)
                if run is None:
                    break
                pending_events = run.events[event_index:]
                event_count = len(run.events)
                terminal = run.status in {"succeeded", "failed", "cancelled"}

            for event_name, payload in pending_events:
                yield f"event: {event_name}\n"
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.01)

            event_index += len(pending_events)
            if terminal and event_index >= event_count:
                break
            await asyncio.sleep(0.05)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/internal/search/preview",
    response_model=InternalSearchPreviewResponse,
    tags=["internal-preview"],
)
def internal_search_preview(
    request: InternalSearchPreviewRequest,
) -> InternalSearchPreviewResponse:
    try:
        output = _preview_search_service().run_search(
            request.query,
            top_k=request.top_k,
            run_profile=request.run_profile,
            enable_refchain=request.enable_refchain,
            enable_query_evolution=request.enable_query_evolution,
            current_year=request.current_year,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return InternalSearchPreviewResponse(
        query_analysis=_model_dump(output.search_plan.query_analysis),
        search_plan=_model_dump(output.search_plan),
        query_evolution_records=[
            _model_dump(record) for record in output.query_evolution_records
        ],
        refchain_output=(
            _model_dump(output.refchain_output)
            if output.refchain_output is not None
            else None
        ),
        synthesis_output=(
            _model_dump(output.synthesis_output)
            if output.synthesis_output is not None
            else None
        ),
        ranked_papers=[_model_dump(paper) for paper in output.ranked_papers],
        raw_count=output.raw_count,
        deduplicated_count=output.deduplicated_count,
        warnings=output.warnings,
        source_stats=[_model_dump(stats) for stats in output.source_stats],
        latency_seconds=output.latency_seconds,
    )


@router.post(
    "/internal/search/preview/api-result",
    response_model=SearchRunResultResponse,
    tags=["internal-preview"],
)
def internal_search_preview_api_result(
    request: InternalSearchPreviewRequest,
) -> SearchRunResultResponse:
    try:
        output = _preview_search_service().run_search(
            request.query,
            top_k=request.top_k,
            run_profile=request.run_profile,
            enable_refchain=request.enable_refchain,
            enable_query_evolution=request.enable_query_evolution,
            current_year=request.current_year,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return map_search_service_output_to_api_result(
        run_id=f"run_preview_{uuid4().hex[:12]}",
        output=output,
        status="succeeded",
        partial=False,
    )


def _mock_cost_report() -> CostReport:
    return CostReport(
        api_call_count=7,
        search_api_call_count=4,
        llm_call_count=0,
        estimated_input_tokens=0,
        estimated_output_tokens=0,
        estimated_total_tokens=0,
        latency_seconds=2.4,
        cache_hit_count=3,
        search_rounds=1,
        judged_paper_count=18,
    )


def _mock_result(run: MockRun) -> SearchRunResultResponse:
    request = run.request
    source_preferences = request.source_preferences or [
        "openalex",
        "arxiv",
        "semantic_scholar",
    ]
    query_terms = request.constraints.must_have_terms or ["academic paper search"]
    expanded_queries = [
        request.query,
        f"{request.query} benchmark evaluation",
        f"{' '.join(query_terms)} citation reranking",
    ]

    highly_relevant = [
        RankedPaper(
            rank=1,
            paper=Paper(
                title="SPAR: Scholar Paper Retrieval with LLM-based Agents for Enhanced Academic Search",
                authors=[
                    "Xiaofeng Shi",
                    "Yuduo Li",
                    "Qian Kou",
                    "Longbin Yu",
                    "Jinxin Xie",
                    "Hua Zhou",
                ],
                year=2025,
                venue="arXiv",
                abstract=(
                    "A scholar paper retrieval framework that uses query "
                    "understanding, retrieval agents, judgement, query evolution, "
                    "and reranking to improve complex academic search."
                ),
                identifiers=PaperIdentifiers(
                    arxiv_id="2507.15245",
                    openalex_id="W-MOCK-SPAR-2025",
                ),
                urls=PaperUrls(
                    landing_page="https://arxiv.org/abs/2507.15245",
                    pdf="https://arxiv.org/pdf/2507.15245",
                ),
                sources=["mock", "arxiv", "openalex"],
            ),
            relevance_score=0.94,
            category="highly_relevant",
            matched_constraints=["topic", "agent_pipeline", "reranking"],
            ranking_reason=(
                "This paper matches the planned SPAR-style pipeline and directly "
                "covers query understanding, retrieval, judgement, evolution, and reranking."
            ),
            evidence=[
                EvidenceItem(
                    source="mock_abstract",
                    text="The abstract-level summary describes an agentic retrieval pipeline for complex academic search.",
                    confidence=0.9,
                )
            ],
        ),
        RankedPaper(
            rank=2,
            paper=Paper(
                title="PaSa: An LLM Agent for Comprehensive Academic Paper Search",
                authors=["Yifei He", "Guangyu Huang", "Peng Feng"],
                year=2025,
                venue="ACL",
                abstract=(
                    "An academic paper search agent built around crawler and "
                    "selector roles, paper queues, and iterative search or expand decisions."
                ),
                identifiers=PaperIdentifiers(
                    arxiv_id="2501.10120",
                    semantic_scholar_id="S2-MOCK-PASA-2025",
                ),
                urls=PaperUrls(
                    landing_page="https://arxiv.org/abs/2501.10120",
                    pdf="https://arxiv.org/pdf/2501.10120",
                ),
                sources=["mock", "arxiv", "semantic_scholar"],
            ),
            relevance_score=0.9,
            category="highly_relevant",
            matched_constraints=["agent_search", "query_evolution", "high_recall"],
            ranking_reason=(
                "The crawler and selector design is closely aligned with iterative "
                "academic search and can inform recall-oriented retrieval strategies."
            ),
            evidence=[
                EvidenceItem(
                    source="mock_summary",
                    text="The mock summary links the paper queue and expand actions to high-recall search.",
                    confidence=0.86,
                )
            ],
        ),
        RankedPaper(
            rank=3,
            paper=Paper(
                title="LitSearch: A Retrieval Benchmark for Scientific Literature Search",
                authors=["A. Ajith", "M. Xia", "A. Chevalier"],
                year=2024,
                venue="EMNLP",
                abstract=(
                    "A benchmark for evaluating scientific literature retrieval, "
                    "with query sets and relevance annotations useful for measuring recall and F1."
                ),
                identifiers=PaperIdentifiers(
                    arxiv_id="2407.18940",
                    openalex_id="W-MOCK-LITSEARCH-2024",
                ),
                urls=PaperUrls(
                    landing_page="https://arxiv.org/abs/2407.18940",
                    pdf="https://arxiv.org/pdf/2407.18940",
                ),
                sources=["mock", "arxiv", "openalex"],
            ),
            relevance_score=0.87,
            category="highly_relevant",
            matched_constraints=["benchmark", "evaluation", "scientific_search"],
            ranking_reason=(
                "The benchmark is relevant because it supports repeatable evaluation "
                "of scientific literature search quality."
            ),
            evidence=[
                EvidenceItem(
                    source="mock_metadata",
                    text="The paper is represented as a retrieval benchmark aligned with F1 and Recall@K evaluation.",
                    confidence=0.84,
                )
            ],
        ),
    ]

    partially_relevant = [
        RankedPaper(
            rank=4,
            paper=Paper(
                title="Language Agents for Answering Questions from Scientific Literature",
                authors=["M. Skarlinski", "S. Feldman", "Collaborators"],
                year=2024,
                venue="NeurIPS",
                abstract=(
                    "A scientific literature question-answering system that gathers "
                    "evidence from papers and synthesizes citation-grounded answers."
                ),
                identifiers=PaperIdentifiers(
                    semantic_scholar_id="S2-MOCK-PAPERQA2-2024",
                ),
                urls=PaperUrls(
                    landing_page="https://paperqa.ai/",
                    pdf=None,
                ),
                sources=["mock", "semantic_scholar"],
            ),
            relevance_score=0.72,
            category="partially_relevant",
            matched_constraints=["evidence_synthesis", "citation_grounding"],
            ranking_reason=(
                "The evidence gathering and synthesis workflow is useful, but the "
                "paper is more focused on QA than paper-finding retrieval."
            ),
            evidence=[
                EvidenceItem(
                    source="mock_summary",
                    text="The system contributes evidence synthesis patterns that can support structured result explanations.",
                    confidence=0.74,
                )
            ],
        ),
        RankedPaper(
            rank=5,
            paper=Paper(
                title="Demonstrate-Search-Predict: Composing Retrieval and Language Models for Knowledge-Intensive NLP",
                authors=["Omar Khattab", "Keshav Santhanam", "Xiang Lisa Li"],
                year=2022,
                venue="arXiv",
                abstract=(
                    "A framework for composing retrieval and language model calls "
                    "for knowledge-intensive tasks with explicit search and prediction steps."
                ),
                identifiers=PaperIdentifiers(
                    arxiv_id="2212.14024",
                    openalex_id="W-MOCK-DSP-2022",
                ),
                urls=PaperUrls(
                    landing_page="https://arxiv.org/abs/2212.14024",
                    pdf="https://arxiv.org/pdf/2212.14024",
                ),
                sources=["mock", "arxiv"],
            ),
            relevance_score=0.66,
            category="partially_relevant",
            matched_constraints=["retrieval_composition", "reasoning"],
            ranking_reason=(
                "The retrieval-and-prediction composition is conceptually relevant, "
                "but it is not specific to academic paper recommendation."
            ),
            evidence=[
                EvidenceItem(
                    source="mock_metadata",
                    text="The framework is relevant as a retrieval composition reference rather than a direct scholar search system.",
                    confidence=0.68,
                )
            ],
        ),
    ]

    return SearchRunResultResponse(
        run_id=run.run_id,
        status="succeeded",
        partial=False,
        query_analysis=QueryAnalysis(
            intent_type="paper_finding",
            domain="computer science",
            research_topics=["academic paper search", "LLM agents", "reranking"],
            constraints={
                "time_range": _model_dump(request.constraints.time_range)
                if request.constraints.time_range
                else None,
                "venues": request.constraints.venues,
                "must_have_terms": request.constraints.must_have_terms,
                "paper_types": request.constraints.paper_types,
            },
        ),
        search_plan=SearchPlan(
            expanded_queries=expanded_queries,
            source_preferences=source_preferences,
            max_rounds=request.budgets.max_search_rounds,
        ),
        highly_relevant_papers=highly_relevant,
        partially_relevant_papers=partially_relevant,
        method_clusters=[
            MethodCluster(
                name="Agentic Scholar Retrieval",
                paper_ranks=[1, 2],
                summary=(
                    "Systems that use agent roles, query planning, expansion, and "
                    "iterative candidate filtering for literature search."
                ),
            ),
            MethodCluster(
                name="Evaluation and Evidence Synthesis",
                paper_ranks=[3, 4],
                summary=(
                    "Benchmarks and evidence-gathering systems that support "
                    "repeatable quality measurement and structured explanations."
                ),
            ),
        ],
        timeline=[
            TimelineItem(
                year=2022,
                paper_ranks=[5],
                summary="Retrieval and language model composition becomes a reusable pattern.",
            ),
            TimelineItem(
                year=2024,
                paper_ranks=[3, 4],
                summary="Scientific literature benchmarks and evidence-centric systems mature.",
            ),
            TimelineItem(
                year=2025,
                paper_ranks=[1, 2],
                summary="Agentic academic search systems focus on recall, judgement, and reranking.",
            ),
        ],
        citation_graph=CitationGraph(
            nodes=[
                CitationGraphNode(id="W-MOCK-SPAR-2025", label="SPAR", rank=1),
                CitationGraphNode(id="S2-MOCK-PASA-2025", label="PaSa", rank=2),
                CitationGraphNode(id="W-MOCK-LITSEARCH-2024", label="LitSearch", rank=3),
                CitationGraphNode(id="W-MOCK-DSP-2022", label="DSP", rank=5),
            ],
            edges=[
                CitationGraphEdge(
                    source="W-MOCK-SPAR-2025",
                    target="S2-MOCK-PASA-2025",
                    relation="related_agentic_search",
                ),
                CitationGraphEdge(
                    source="W-MOCK-SPAR-2025",
                    target="W-MOCK-LITSEARCH-2024",
                    relation="evaluated_by_benchmark_family",
                ),
                CitationGraphEdge(
                    source="S2-MOCK-PASA-2025",
                    target="W-MOCK-DSP-2022",
                    relation="shares_retrieval_reasoning_pattern",
                ),
            ],
        ),
        missing_evidence=[
            "This mock response does not call live academic APIs.",
            "Citation counts, venue metadata, and identifier coverage are placeholders.",
            "LLM judgement is disabled in the mock API.",
        ],
        cost_report=_mock_cost_report(),
    )


def _mock_sse_events(run: MockRun) -> list[tuple[str, dict[str, Any]]]:
    timestamp = _now().isoformat()
    run_id = run.run_id
    return [
        (
            "run_started",
            {
                "run_id": run_id,
                "timestamp": timestamp,
                "query": run.request.query,
            },
        ),
        (
            "stage_started",
            {
                "run_id": run_id,
                "stage": "query_understanding",
                "timestamp": timestamp,
            },
        ),
        (
            "stage_completed",
            {
                "run_id": run_id,
                "stage": "query_understanding",
                "expanded_query_count": 3,
                "timestamp": timestamp,
            },
        ),
        (
            "stage_started",
            {
                "run_id": run_id,
                "stage": "retrieval",
                "timestamp": timestamp,
            },
        ),
        (
            "connector_completed",
            {
                "run_id": run_id,
                "stage": "retrieval",
                "connector": "mock",
                "returned_count": 42,
                "latency_ms": 120,
                "cache_hit": False,
                "timestamp": timestamp,
            },
        ),
        (
            "stage_completed",
            {
                "run_id": run_id,
                "stage": "retrieval",
                "candidate_paper_count": 42,
                "timestamp": timestamp,
            },
        ),
        (
            "stage_started",
            {
                "run_id": run_id,
                "stage": "judgement",
                "timestamp": timestamp,
            },
        ),
        (
            "stage_completed",
            {
                "run_id": run_id,
                "stage": "judgement",
                "judged_paper_count": 18,
                "timestamp": timestamp,
            },
        ),
        (
            "stage_started",
            {
                "run_id": run_id,
                "stage": "reranking",
                "timestamp": timestamp,
            },
        ),
        (
            "stage_completed",
            {
                "run_id": run_id,
                "stage": "reranking",
                "top_k": run.request.top_k,
                "timestamp": timestamp,
            },
        ),
        (
            "run_completed",
            {
                "run_id": run_id,
                "status": "succeeded",
                "timestamp": timestamp,
                "cost_report": _model_dump(_mock_cost_report()),
            },
        ),
    ]


def _execute_real_search_run(run_id: str) -> None:
    with _REAL_RUNS_LOCK:
        run = _REAL_RUNS.get(run_id)
        if run is None:
            return
        if run.status == "cancelled" or run.cancel_requested:
            return
        request = run.request

    current_year = (
        request.constraints.time_range.end_year
        if request.constraints.time_range is not None
        else None
    )

    try:
        _start_real_stage(run_id, "query_understanding")
        _complete_real_stage(run_id, "query_understanding")
        _start_real_stage(run_id, "retrieval")
        output = _real_search_service().run_search(
            request.query,
            top_k=request.top_k,
            run_profile=request.run_profile,
            enable_refchain=request.options.enable_refchain,
            enable_query_evolution=request.options.enable_query_evolution,
            enable_synthesis=True,
            current_year=current_year,
        )
        result = map_search_service_output_to_api_result(
            run_id=run_id,
            output=output,
            status="succeeded",
            partial=False,
        )
        with _REAL_RUNS_LOCK:
            run = _REAL_RUNS.get(run_id)
            if run is None:
                return
            if run.status == "cancelled" or run.cancel_requested:
                return

        candidate_count = len(result.highly_relevant_papers) + len(
            result.partially_relevant_papers
        )
        _complete_real_stage(
            run_id,
            "retrieval",
            candidate_paper_count=candidate_count,
            extra_payload={
                "search_api_call_count": result.cost_report.search_api_call_count,
            },
        )
        _start_real_stage(run_id, "judgement")
        _complete_real_stage(
            run_id,
            "judgement",
            judged_paper_count=result.cost_report.judged_paper_count,
        )
        _start_real_stage(run_id, "reranking")
        _complete_real_stage(
            run_id,
            "reranking",
            extra_payload={"top_k": request.top_k},
        )
        _start_real_stage(run_id, "synthesis")
        _complete_real_stage(
            run_id,
            "synthesis",
            extra_payload={
                "synthesis_status": result.synthesis.status
                if result.synthesis is not None
                else None,
            },
        )
        with _REAL_RUNS_LOCK:
            run = _REAL_RUNS.get(run_id)
            if run is None:
                return
            if run.status == "cancelled" or run.cancel_requested:
                return
            run.status = "succeeded"
            run.current_stage = "synthesis"
            run.progress = RunProgress(
                completed_stages=[
                    "query_understanding",
                    "retrieval",
                    "judgement",
                    "reranking",
                    "synthesis",
                ],
                candidate_paper_count=candidate_count,
                judged_paper_count=result.cost_report.judged_paper_count,
            )
            run.cost_report = result.cost_report
            run.result = result
            run.error_message = None
            run.updated_at = _now()
        _append_real_event(
            run_id,
            "run_completed",
            {
                "status": "succeeded",
                "cost_report": _model_dump(result.cost_report),
            },
        )
    except ValueError as exc:
        _fail_real_run(run_id, str(exc))
    except Exception as exc:  # noqa: BLE001 - isolate background failure
        _fail_real_run(run_id, str(exc))


def _append_real_event(
    run_id: str,
    event_name: str,
    payload: dict[str, Any] | None = None,
) -> None:
    event_payload = dict(payload or {})
    event_payload.setdefault("run_id", run_id)
    event_payload.setdefault("timestamp", _now().isoformat())
    with _REAL_RUNS_LOCK:
        run = _REAL_RUNS.get(run_id)
        if run is None:
            return
        run.events.append((event_name, event_payload))
        run.updated_at = _now()


def _real_status_response(run: RealRun) -> SearchRunStatusResponse:
    return SearchRunStatusResponse(
        run_id=run.run_id,
        status=run.status,  # type: ignore[arg-type]
        current_stage=run.current_stage,
        progress=run.progress,
        cost_report=run.cost_report,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _start_real_stage(run_id: str, stage: str) -> None:
    with _REAL_RUNS_LOCK:
        run = _REAL_RUNS.get(run_id)
        if run is None:
            return
        if run.status == "cancelled" or run.cancel_requested:
            return
        run.status = "running"
        run.current_stage = stage
        run.updated_at = _now()
    _append_real_event(run_id, "stage_started", {"stage": stage})


def _complete_real_stage(
    run_id: str,
    stage: str,
    *,
    candidate_paper_count: int | None = None,
    judged_paper_count: int | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    payload = {"stage": stage}
    if candidate_paper_count is not None:
        payload["candidate_paper_count"] = candidate_paper_count
    if judged_paper_count is not None:
        payload["judged_paper_count"] = judged_paper_count
    if extra_payload:
        payload.update(extra_payload)

    with _REAL_RUNS_LOCK:
        run = _REAL_RUNS.get(run_id)
        if run is None:
            return
        if run.status == "cancelled" or run.cancel_requested:
            return
        run.status = "running"
        run.current_stage = stage
        if stage not in run.progress.completed_stages:
            run.progress.completed_stages.append(stage)
        if candidate_paper_count is not None:
            run.progress.candidate_paper_count = candidate_paper_count
        if judged_paper_count is not None:
            run.progress.judged_paper_count = judged_paper_count
        run.updated_at = _now()
    _append_real_event(run_id, "stage_completed", payload)


def _fail_real_run(run_id: str, message: str) -> None:
    with _REAL_RUNS_LOCK:
        run = _REAL_RUNS.get(run_id)
        if run is None:
            return
        if run.status == "cancelled" or run.cancel_requested:
            return
        run.status = "failed"
        run.current_stage = "failed"
        run.error_message = message
        run.cost_report = CostReport()
        run.updated_at = _now()
    _append_real_event(run_id, "error", {"message": message})
    _append_real_event(run_id, "run_completed", {"status": "failed", "error": message})

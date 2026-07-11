"""FastAPI routes for the ScholarNavigator backend API."""

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
    ConnectorRuntimeConfig,
    CostReport,
    HealthResponse,
    LLMRuntimeConfig,
    RunProgress,
    RuntimeConfigResponse,
    RuntimeFeatures,
    RuntimeLimits,
    SearchConstraints,
    SearchPlan,
    SearchRunCreateRequest,
    SearchRunCreateResponse,
    SearchRunResultResponse,
    SearchRunStatusResponse,
)
from ...core.search_schemas import QueryConstraint, RunProfile, SearchBudget, TimeRange
from ...llm.provider import get_llm_runtime_config
from ...services.api_mapper import map_search_service_output_to_api_result
from ...services.search_service import (
    ENABLE_LLM_JUDGEMENT_ENV,
    ENABLE_LLM_QUERY_UNDERSTANDING_ENV,
    SearchService,
    _env_flag,
)


API_VERSION = "0.1.0"
DEFAULT_REAL_PREVIEW_MAX_WORKERS = 2
REAL_PREVIEW_MAX_WORKERS_ENV = "REAL_PREVIEW_MAX_WORKERS"
DEFAULT_REAL_SEARCH_MAX_WORKERS = 2
REAL_SEARCH_MAX_WORKERS_ENV = "REAL_SEARCH_MAX_WORKERS"
DEFAULT_REAL_SEARCH_BACKGROUND_WORKERS = 2
REAL_SEARCH_BACKGROUND_WORKERS_ENV = "REAL_SEARCH_BACKGROUND_WORKERS"
DEFAULT_REAL_SEARCH_RUN_TTL_SECONDS = 3600
REAL_SEARCH_RUN_TTL_SECONDS_ENV = "REAL_SEARCH_RUN_TTL_SECONDS"
DEFAULT_REAL_SEARCH_MAX_STORED_RUNS = 200
REAL_SEARCH_MAX_STORED_RUNS_ENV = "REAL_SEARCH_MAX_STORED_RUNS"
SEMANTIC_SCHOLAR_API_KEY_ENV = "SEMANTIC_SCHOLAR_API_KEY"
NCBI_API_KEY_ENV = "NCBI_API_KEY"
PUBMED_API_KEY_ENV = "PUBMED_API_KEY"
REAL_SEARCH_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}

router = APIRouter(prefix="/api/v1", tags=["api"])


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
    enable_llm_query_understanding: bool | None = None
    enable_llm_judgement: bool | None = None
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


_REAL_RUNS: dict[str, RealRun] = {}
_REAL_RUNS_LOCK = RLock()
_REAL_SEARCH_EXECUTOR: ThreadPoolExecutor | None = None
_REAL_SEARCH_EXECUTOR_LOCK = RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _real_links(run_id: str) -> dict[str, str]:
    return {
        "self": f"/api/v1/real/search/runs/{run_id}",
        "events": f"/api/v1/real/search/runs/{run_id}/events",
        "result": f"/api/v1/real/search/runs/{run_id}/result",
    }


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


def _int_from_env(env_name: str, default: int) -> int:
    raw_value = os.getenv(env_name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def _real_search_run_ttl_seconds() -> int:
    return _int_from_env(
        REAL_SEARCH_RUN_TTL_SECONDS_ENV,
        DEFAULT_REAL_SEARCH_RUN_TTL_SECONDS,
    )


def _real_search_max_stored_runs() -> int:
    return _int_from_env(
        REAL_SEARCH_MAX_STORED_RUNS_ENV,
        DEFAULT_REAL_SEARCH_MAX_STORED_RUNS,
    )


def _cleanup_real_runs(
    *,
    protected_run_id: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Remove old terminal real-search runs from the in-memory store."""

    timestamp = now or _now()
    deleted: list[str] = []
    ttl_seconds = _real_search_run_ttl_seconds()
    max_stored_runs = _real_search_max_stored_runs()

    with _REAL_RUNS_LOCK:
        if ttl_seconds > 0:
            threshold = timestamp.timestamp() - ttl_seconds
            expired_run_ids = [
                run_id
                for run_id, run in _REAL_RUNS.items()
                if run_id != protected_run_id
                and run.status in REAL_SEARCH_TERMINAL_STATUSES
                and run.updated_at.timestamp() < threshold
            ]
            for run_id in expired_run_ids:
                if _REAL_RUNS.pop(run_id, None) is not None:
                    deleted.append(run_id)

        if max_stored_runs > 0 and len(_REAL_RUNS) > max_stored_runs:
            delete_count = len(_REAL_RUNS) - max_stored_runs
            candidates = sorted(
                (
                    run
                    for run_id, run in _REAL_RUNS.items()
                    if run_id != protected_run_id
                    and run.status in REAL_SEARCH_TERMINAL_STATUSES
                ),
                key=lambda run: (run.updated_at, run.created_at, run.run_id),
            )
            for run in candidates[:delete_count]:
                if _REAL_RUNS.pop(run.run_id, None) is not None:
                    deleted.append(run.run_id)

    return deleted


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
    llm_runtime = get_llm_runtime_config()
    llm_feature_enabled = (
        llm_runtime.available
        and _env_flag(ENABLE_LLM_QUERY_UNDERSTANDING_ENV, default=False)
    )
    llm_judgement_enabled = (
        llm_runtime.available
        and _env_flag(ENABLE_LLM_JUDGEMENT_ENV, default=False)
    )
    return RuntimeConfigResponse(
        mode="real_search",
        llm=LLMRuntimeConfig(
            provider=llm_runtime.provider,
            model=llm_runtime.model,
            available=llm_runtime.available,
            base_url_host=llm_runtime.base_url_host,
            reason=llm_runtime.reason,
        ),
        connectors=[
            ConnectorRuntimeConfig(
                name="openalex",
                available=True,
                requires_key=False,
                reason="implemented_for_real_search",
            ),
            ConnectorRuntimeConfig(
                name="arxiv",
                available=True,
                requires_key=False,
                reason="implemented_for_real_search",
            ),
            ConnectorRuntimeConfig(
                name="semantic_scholar",
                available=True,
                requires_key=False,
                reason=_semantic_scholar_runtime_reason(),
            ),
            ConnectorRuntimeConfig(
                name="pubmed",
                available=True,
                requires_key=False,
                reason=_pubmed_runtime_reason(),
            ),
        ],
        limits=RuntimeLimits(
            max_top_k=100,
            max_search_rounds=3,
            max_candidate_papers=300,
            max_latency_seconds=120,
            real_search_max_workers=_real_search_max_workers(),
            real_search_background_workers=_real_search_background_workers(),
            real_search_run_ttl_seconds=_real_search_run_ttl_seconds(),
            real_search_max_stored_runs=_real_search_max_stored_runs(),
        ),
        features=RuntimeFeatures(
            query_evolution=True,
            refchain=True,
            evaluation=False,
            sse=True,
            real_search=True,
            real_search_cancel=True,
            real_search_sse=True,
            retrieval_cache=True,
            batch_cli=True,
            llm_query_understanding=llm_feature_enabled,
            llm_judgement=llm_judgement_enabled,
        ),
    )


def _semantic_scholar_runtime_reason() -> str:
    if os.getenv(SEMANTIC_SCHOLAR_API_KEY_ENV, "").strip():
        return "implemented_for_real_search_with_optional_api_key"
    return "implemented_for_real_search_without_api_key_rate_limited"


def _pubmed_runtime_reason() -> str:
    if os.getenv(NCBI_API_KEY_ENV, "").strip() or os.getenv(PUBMED_API_KEY_ENV, "").strip():
        return "implemented_for_real_search_with_optional_api_key"
    return "implemented_for_real_search_without_api_key_rate_limited"


def _to_internal_constraints(constraints: SearchConstraints) -> QueryConstraint:
    raw_time_range = constraints.time_range
    time_range: TimeRange | None = None
    if raw_time_range is not None and (
        raw_time_range.start_year is not None
        or raw_time_range.end_year is not None
    ):
        time_range = raw_time_range.model_copy(
            update={"label": raw_time_range.label or "explicit"}
        )
    return QueryConstraint(
        time_range=time_range,
        venues=constraints.venues,
        datasets=constraints.datasets,
        must_include_terms=constraints.must_have_terms,
        exclude_terms=constraints.excluded_terms,
        paper_types=constraints.paper_types,
    )


def _to_internal_budget(budgets: BaseModel) -> SearchBudget:
    return SearchBudget.model_validate(budgets.model_dump())


@router.post(
    "/real/search/runs",
    response_model=SearchRunCreateResponse,
    status_code=201,
    tags=["real-search"],
)
def create_real_search_run(request: SearchRunCreateRequest) -> SearchRunCreateResponse:
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")

    _cleanup_real_runs()
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
    _cleanup_real_runs(protected_run_id=run_id)
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
    _cleanup_real_runs(protected_run_id=run_id)
    with _REAL_RUNS_LOCK:
        run = _get_real_run(run_id)
        return _real_status_response(run)


@router.get(
    "/real/search/runs/{run_id}/result",
    response_model=SearchRunResultResponse,
    tags=["real-search"],
)
def get_real_search_result(run_id: str) -> SearchRunResultResponse:
    _cleanup_real_runs(protected_run_id=run_id)
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
    _cleanup_real_runs(protected_run_id=run_id)
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
    _cleanup_real_runs(protected_run_id=run_id)
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
            enable_llm_query_understanding=request.enable_llm_query_understanding,
            enable_llm_judgement=request.enable_llm_judgement,
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
            enable_llm_query_understanding=request.enable_llm_query_understanding,
            enable_llm_judgement=request.enable_llm_judgement,
            current_year=request.current_year,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return map_search_service_output_to_api_result(
        run_id=f"run_internal_{uuid4().hex[:12]}",
        output=output,
        status="succeeded",
        partial=False,
    )

def _execute_real_search_run(run_id: str) -> None:
    with _REAL_RUNS_LOCK:
        run = _REAL_RUNS.get(run_id)
        if run is None:
            return
        if run.status == "cancelled" or run.cancel_requested:
            return
        request = run.request

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
            current_year=None,
            enable_llm_query_understanding=(
                request.options.enable_llm_query_understanding
            ),
            enable_llm_judgement=request.options.enable_llm_judgement,
            sources_override=request.source_preferences,
            explicit_constraints=_to_internal_constraints(request.constraints),
            budget=_to_internal_budget(request.budgets),
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

        _append_real_connector_events(run_id, output)
        _append_real_warning_events(run_id, output.warnings)

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
        if _real_run_is_cancelled(run_id):
            return
        _append_real_event(
            run_id,
            "cost_updated",
            {
                "cost_report": _model_dump(result.cost_report),
            },
        )
        if _real_run_is_cancelled(run_id):
            return
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


def _append_real_connector_events(
    run_id: str,
    output: Any,
) -> None:
    for stats in output.source_stats:
        if _real_run_is_cancelled(run_id):
            return
        _append_real_event(
            run_id,
            "connector_completed",
            {
                "stage": "retrieval",
                "connector": stats.source,
                "source": stats.source,
                "returned_count": stats.returned_count,
                "latency_seconds": stats.latency_seconds,
                "cache_hit": stats.cache_hit,
                "error_message": stats.error_message,
                "diagnostics": _model_dump(stats.diagnostics),
            },
        )


def _append_real_warning_events(run_id: str, warnings: list[str]) -> None:
    for warning in warnings:
        if _real_run_is_cancelled(run_id):
            return
        _append_real_event(run_id, "warning", {"message": warning})


def _real_run_is_cancelled(run_id: str) -> bool:
    with _REAL_RUNS_LOCK:
        run = _REAL_RUNS.get(run_id)
        if run is None:
            return True
        return run.status == "cancelled" or run.cancel_requested


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

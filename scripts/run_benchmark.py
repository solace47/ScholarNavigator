#!/usr/bin/env python3
"""运行已注册公开 Benchmark，并写入可恢复的统一评测产物。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (REPO_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from scripts.evaluate_search_batch import evaluate_batch_results  # noqa: E402
from scholar_agent.core.api_schemas import CostReport  # noqa: E402
from scholar_agent.core.evaluation_schemas import EvalQuery  # noqa: E402
from scholar_agent.core.search_schemas import (  # noqa: E402
    SUPPORTED_SEARCH_SOURCES,
    SearchBudget,
)
from scholar_agent.evaluation.datasets import (  # noqa: E402
    dataset_source_path,
    inspect_dataset,
    load_dataset,
    supported_datasets,
)
from scholar_agent.evaluation.selection import ResultPolicy  # noqa: E402
from scholar_agent.evaluation.stage_diagnostics import (  # noqa: E402
    aggregate_stage_diagnostics,
    analyze_search_stages,
)
from scholar_agent.llm.provider import get_llm_runtime_config  # noqa: E402
from scholar_agent.prompts import load_manifest, load_prompt  # noqa: E402
from scholar_agent.retrieval.query_adapter import QueryAdapterPolicy  # noqa: E402
from scholar_agent.services.api_mapper import (  # noqa: E402
    map_search_service_output_to_api_result,
)
from scholar_agent.services.search_service import SearchService  # noqa: E402


RunProfile = Literal["fast", "balanced", "high_recall", "evaluation"]
_RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
_SENSITIVE_ENV_NAMES = (
    "SCHOLAR_AGENT_LLM_API_KEY",
    "SEMANTIC_SCHOLAR_API_KEY",
    "NCBI_API_KEY",
    "PUBMED_API_KEY",
)


class BenchmarkRunOptions(BaseModel):
    dataset: str
    dataset_path: Path | None = None
    limit: int | None = Field(default=None, ge=1)
    offset: int = Field(default=0, ge=0)
    output_root: Path = Path("outputs/benchmark_runs")
    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    run_profile: RunProfile = "balanced"
    sources: list[str] = Field(
        default_factory=lambda: list(SUPPORTED_SEARCH_SOURCES)
    )
    result_policy: ResultPolicy = "highly_and_partial"
    top_k: int = Field(default=20, ge=1, le=100)
    enable_query_evolution: bool = False
    enable_refchain: bool = False
    enable_llm_query_understanding: bool = False
    enable_llm_judgement: bool = False
    current_year: int | None = Field(default=None, ge=1900, le=2200)
    max_workers: int = Field(default=4, ge=1, le=32)
    budgets: SearchBudget = Field(default_factory=SearchBudget)
    diagnostics: bool = False
    resume: bool = False
    query_adapter_policy: QueryAdapterPolicy = "adaptive"

    @field_validator("sources", mode="before")
    @classmethod
    def validate_sources(cls, value: object) -> list[str]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("sources must be a list")
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_source in value:
            source = str(raw_source).strip().lower()
            if not source or source in seen:
                continue
            if source not in SUPPORTED_SEARCH_SOURCES:
                raise ValueError(f"unsupported source: {source}")
            normalized.append(source)
            seen.add(source)
        if not normalized:
            raise ValueError("sources must not be empty")
        return normalized


class BenchmarkRunResult(BaseModel):
    run_dir: Path
    config: dict[str, Any]
    metrics: dict[str, Any]
    result_rows: list[dict[str, Any]]
    stage_metrics: dict[str, Any] | None = None


def run_benchmark(
    options: BenchmarkRunOptions,
    *,
    service: Any | None = None,
) -> BenchmarkRunResult:
    source_path = dataset_source_path(options.dataset, options.dataset_path)
    all_queries = load_dataset(options.dataset, path=source_path)
    selected = _select_queries(all_queries, options.offset, options.limit)
    dataset_report = inspect_dataset(options.dataset, path=source_path)
    run_dir = options.output_root.expanduser().resolve() / options.run_id
    config = _build_config(options, source_path, selected)

    existing_rows: dict[str, dict[str, Any]] = {}
    if options.resume:
        config, existing_rows = _prepare_resume(run_dir, config, selected)
    else:
        if run_dir.exists():
            raise ValueError(f"run directory already exists; use --resume: {run_dir}")
        run_dir.mkdir(parents=True, exist_ok=False)
        _atomic_write_json(run_dir / "config.json", config)
        _atomic_write_json(
            run_dir / "dataset_report.json",
            dataset_report.model_dump(mode="json"),
        )

    runner = service or SearchService(max_workers=options.max_workers)
    selected_ids = [query.query_id for query in selected]
    for query in selected:
        previous = existing_rows.get(query.query_id)
        if previous is not None and previous.get("status") == "succeeded":
            continue
        existing_rows[query.query_id] = _run_case(runner, query, options)
        _write_result_artifacts(run_dir, selected_ids, existing_rows)

    ordered_rows = [existing_rows[case_id] for case_id in selected_ids]
    metrics = _evaluate_rows(ordered_rows, selected, options.result_policy)
    _atomic_write_json(run_dir / "metrics.json", metrics)
    stage_metrics: dict[str, Any] | None = None
    if options.diagnostics:
        case_diagnostics = [
            row["stage_diagnostics"]
            for row in ordered_rows
            if isinstance(row.get("stage_diagnostics"), dict)
        ]
        stage_metrics, error_analysis, gold_diagnostics = (
            aggregate_stage_diagnostics(case_diagnostics)
        )
        _atomic_write_json(run_dir / "stage_metrics.json", stage_metrics)
        _atomic_write_json(run_dir / "error_analysis.json", error_analysis)
        _atomic_write_jsonl(run_dir / "gold_diagnostics.jsonl", gold_diagnostics)
    _atomic_write_text(
        run_dir / "summary.md",
        _summary_markdown(config, metrics, stage_metrics),
    )
    _write_failures(run_dir / "failures.jsonl", ordered_rows)
    return BenchmarkRunResult(
        run_dir=run_dir,
        config=config,
        metrics=metrics,
        result_rows=ordered_rows,
        stage_metrics=stage_metrics,
    )


def _select_queries(
    queries: list[EvalQuery],
    offset: int,
    limit: int | None,
) -> list[EvalQuery]:
    selected = queries[offset:] if limit is None else queries[offset : offset + limit]
    if not selected:
        raise ValueError("offset/limit selected no benchmark cases")
    return selected


def _build_config(
    options: BenchmarkRunOptions,
    source_path: Path,
    selected: list[EvalQuery],
) -> dict[str, Any]:
    llm_runtime = get_llm_runtime_config()
    requested_llm = (
        options.enable_llm_query_understanding or options.enable_llm_judgement
    )
    semantic_config = {
        "dataset": options.dataset,
        "dataset_source_path": str(source_path),
        "dataset_sha256": _file_sha256(source_path),
        "case_count": len(selected),
        "case_ids": [item.query_id for item in selected],
        "offset": options.offset,
        "limit": options.limit,
        "selection_order": "source_order",
        "result_policy": options.result_policy,
        "sources": list(options.sources),
        "run_profile": options.run_profile,
        "top_k": options.top_k,
        "enable_query_evolution": options.enable_query_evolution,
        "enable_refchain": options.enable_refchain,
        "current_year": options.current_year,
        "max_workers": options.max_workers,
        "budgets": options.budgets.model_dump(mode="json"),
        "diagnostics": options.diagnostics,
        "query_adapter_policy": options.query_adapter_policy,
        "llm": {
            "llm_enabled": bool(requested_llm and llm_runtime.available),
            "requested": requested_llm,
            "query_understanding": options.enable_llm_query_understanding,
            "judgement": options.enable_llm_judgement,
            "provider": llm_runtime.provider,
            "model": llm_runtime.model,
            "runtime_available": llm_runtime.available,
        },
        "prompts": _prompt_metadata(),
        "runtime_code_hash": _runtime_code_hash(),
        "code": _git_metadata(),
    }
    signature_payload = {
        key: value for key, value in semantic_config.items() if key != "code"
    }
    signature = hashlib.sha256(
        json.dumps(
            signature_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        **semantic_config,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "resume_signature": signature,
    }


def _prompt_metadata() -> list[dict[str, Any]]:
    prompts: list[dict[str, Any]] = []
    for name, entry in load_manifest().items():
        if not entry.runtime_enabled:
            continue
        prompt = load_prompt(name)
        prompts.append(
            {
                "name": prompt.name,
                "version": prompt.version,
                "hash": prompt.content_hash,
            }
        )
    return prompts


def _git_metadata() -> dict[str, Any]:
    commit = _git_output(["rev-parse", "HEAD"])
    status = _git_output(["status", "--porcelain", "--untracked-files=no"])
    diff = _git_bytes(["diff", "--binary", "HEAD", "--", "."])
    return {
        "commit": commit or None,
        "dirty": bool(status),
        "working_tree_diff_hash": hashlib.sha256(diff).hexdigest(),
    }


def _runtime_code_hash() -> str:
    paths = sorted((REPO_ROOT / "src" / "scholar_agent").rglob("*.py"))
    paths.extend(
        [
            REPO_ROOT / "scripts" / "evaluate_search_batch.py",
            REPO_ROOT / "scripts" / "run_benchmark.py",
        ]
    )
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(REPO_ROOT).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _git_output(arguments: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def _git_bytes(arguments: list[str]) -> bytes:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return b""
    return result.stdout


def _prepare_resume(
    run_dir: Path,
    current_config: dict[str, Any],
    selected: list[EvalQuery],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    config_path = run_dir / "config.json"
    results_path = run_dir / "results.jsonl"
    if not config_path.is_file() or not results_path.is_file():
        raise ValueError("resume requires existing config.json and results.jsonl")
    stored = _read_json(config_path)
    if stored.get("resume_signature") != current_config.get("resume_signature"):
        raise ValueError("resume config is incompatible with the existing run")

    allowed_ids = {item.query_id for item in selected}
    indexed: dict[str, dict[str, Any]] = {}
    for line_number, row in _read_jsonl(results_path):
        case_id = str(row.get("case_id") or "").strip()
        if not case_id or case_id not in allowed_ids:
            raise ValueError(f"invalid resume results at line {line_number}: case_id")
        if case_id in indexed:
            raise ValueError(
                f"invalid resume results at line {line_number}: duplicate {case_id}"
            )
        indexed[case_id] = row
    return stored, indexed


def _run_case(
    service: Any,
    query: EvalQuery,
    options: BenchmarkRunOptions,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        output = service.run_search(
            query.query,
            top_k=options.top_k,
            run_profile=options.run_profile,
            enable_query_evolution=options.enable_query_evolution,
            enable_refchain=options.enable_refchain,
            enable_synthesis=True,
            current_year=options.current_year,
            enable_llm_query_understanding=options.enable_llm_query_understanding,
            enable_llm_judgement=options.enable_llm_judgement,
            sources_override=list(options.sources),
            budget=options.budgets,
            collect_diagnostics=options.diagnostics,
            query_adapter_policy=options.query_adapter_policy,
        )
        result = map_search_service_output_to_api_result(
            run_id=f"benchmark_{query.query_id}",
            output=output,
            status="succeeded",
            partial=False,
        ).model_dump(mode="json")
        cost_report = dict(result.get("cost_report") or {})
        row = {
            "case_id": query.query_id,
            "query": query.query,
            "status": "succeeded",
            "result": result,
            "error": None,
            "latency_seconds": time.perf_counter() - started,
            "cost_report": cost_report,
        }
        if options.diagnostics:
            row["stage_diagnostics"] = analyze_search_stages(
                query,
                output,
                result_policy=options.result_policy,
            )
        return row
    except Exception as exc:  # noqa: BLE001 - isolate benchmark cases
        return {
            "case_id": query.query_id,
            "query": query.query,
            "status": "failed",
            "result": None,
            "error": _sanitize_message(str(exc)),
            "error_type": type(exc).__name__,
            "latency_seconds": time.perf_counter() - started,
            "cost_report": CostReport().model_dump(mode="json"),
        }


def _evaluate_rows(
    rows: list[dict[str, Any]],
    queries: list[EvalQuery],
    result_policy: ResultPolicy,
) -> dict[str, Any]:
    gold_rows = [
        {
            "case_id": query.query_id,
            "relevant_papers": [
                paper.model_dump(mode="json") for paper in query.gold_papers
            ],
        }
        for query in queries
    ]
    metrics = evaluate_batch_results(
        rows,
        gold_rows,
        k_values=[5, 10, 20],
        result_policy=result_policy,
    )
    statistics = metrics["case_statistics"]
    efficiency = metrics["efficiency"]
    case_count = max(1, int(efficiency.get("case_count") or 0))
    failures = [row for row in rows if row.get("status") != "succeeded"]
    metrics["benchmark_statistics"] = {
        "success_rate": statistics["success_rate"],
        "failed_case_rate": statistics["failed_case_rate"],
        "missing_result_rate": statistics["missing_result_rate"],
        "average_api_calls": efficiency["avg_api_call_count"],
        "average_llm_calls": efficiency["avg_llm_call_count"],
        "average_tokens": efficiency["avg_llm_total_tokens"],
        "average_latency_seconds": efficiency["average_latency_seconds"],
        "average_candidate_count": (
            efficiency["total_deduplicated_count"] / case_count
        ),
        "average_final_result_count": (
            efficiency["total_returned_result_count"] / case_count
        ),
        "failure_reason_distribution": dict(
            sorted(Counter(str(row.get("error_type") or "Unknown") for row in failures).items())
        ),
    }
    return metrics


def _write_result_artifacts(
    run_dir: Path,
    selected_ids: list[str],
    rows_by_id: dict[str, dict[str, Any]],
) -> None:
    ordered = [rows_by_id[item] for item in selected_ids if item in rows_by_id]
    _atomic_write_jsonl(run_dir / "results.jsonl", ordered)
    _write_failures(run_dir / "failures.jsonl", ordered)


def _write_failures(path: Path, rows: list[dict[str, Any]]) -> None:
    failures = [
        {
            "case_id": row["case_id"],
            "query": row["query"],
            "status": row["status"],
            "error_type": row.get("error_type") or "Unknown",
            "error_message": row.get("error") or "",
        }
        for row in rows
        if row.get("status") != "succeeded"
    ]
    _atomic_write_jsonl(path, failures)


def _summary_markdown(
    config: dict[str, Any],
    metrics: dict[str, Any],
    stage_metrics: dict[str, Any] | None = None,
) -> str:
    stats = metrics["case_statistics"]
    efficiency = metrics["benchmark_statistics"]
    lines = [
        "# Benchmark 基线汇总",
        "",
        f"- 数据集：`{config['dataset']}`",
        f"- 案例数：{config['case_count']}",
        f"- 成功率：{stats['success_rate']:.3f}",
        f"- 失败率：{stats['failed_case_rate']:.3f}",
        f"- 平均 API 调用：{efficiency['average_api_calls']:.3f}",
        f"- 平均 LLM 调用：{efficiency['average_llm_calls']:.3f}",
        f"- 平均 Token：{efficiency['average_tokens']:.3f}",
        f"- 平均延迟：{efficiency['average_latency_seconds']:.3f} 秒",
        "",
        "| 口径 | F1@5 | F1@10 | F1@20 | MRR | nDCG@5 | nDCG@10 | nDCG@20 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        _summary_metric_row("仅成功案例", metrics["success_only_metrics"]),
        _summary_metric_row("端到端", metrics["end_to_end_metrics"]),
        "",
    ]
    if stage_metrics is not None:
        judgement = stage_metrics.get("judgement", {})
        reranking = stage_metrics.get("reranking", {})
        lines.extend(
            [
                "## 阶段诊断",
                "",
                "| 初始候选 Recall | 最终返回 Recall@20 | Judgement FN 率 | 平均 gold rank | 瓶颈标签 |",
                "| ---: | ---: | ---: | ---: | --- |",
                (
                    f"| {_format_optional(stage_metrics.get('initial_retrieval_recall'))} "
                    "| "
                    f"{_format_optional((stage_metrics.get('final_returned_recall') or {}).get('20'))} "
                    f"| {float(judgement.get('gold_false_negative_rate') or 0.0):.3f} "
                    f"| {_format_optional(reranking.get('average_gold_rank'))} "
                    f"| {', '.join(stage_metrics.get('bottleneck_labels') or []) or '-'} |"
                ),
                "",
            ]
        )
    lines.extend(
        [
            "> 小规模 smoke 只验证真实 Benchmark 运行链路，不代表最终比赛成绩或完整 Benchmark 性能。",
            "",
        ]
    )
    return "\n".join(lines)


def _format_optional(value: Any) -> str:
    return f"{float(value):.3f}" if value is not None else "-"


def _summary_metric_row(label: str, metrics: dict[str, Any]) -> str:
    return (
        f"| {label} | {_at_k(metrics, 'f1_at_k', 5):.3f} | "
        f"{_at_k(metrics, 'f1_at_k', 10):.3f} | "
        f"{_at_k(metrics, 'f1_at_k', 20):.3f} | "
        f"{float(metrics.get('mrr') or 0.0):.3f} | "
        f"{_at_k(metrics, 'ndcg_at_k', 5):.3f} | "
        f"{_at_k(metrics, 'ndcg_at_k', 10):.3f} | "
        f"{_at_k(metrics, 'ndcg_at_k', 20):.3f} |"
    )


def _at_k(metrics: dict[str, Any], name: str, k: int) -> float:
    values = metrics.get(name) or {}
    return float(values.get(str(k), values.get(k, 0.0)))


def _sanitize_message(message: str) -> str:
    sanitized = message
    for env_name in _SENSITIVE_ENV_NAMES:
        secret = os.getenv(env_name)
        if secret:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    sanitized = re.sub(
        r"(?i)(authorization|api[_-]?key|token)(\s*[:=]\s*)[^\s&,;]+",
        r"\1\2[REDACTED]",
        sanitized,
    )
    return sanitized[:1000]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON file: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid JSON object: {path}")
    return payload


def _read_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_number}: {path}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"invalid JSONL object at line {line_number}: {path}")
        rows.append((line_number, payload))
    return rows


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    _atomic_write_text(path, text)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _parse_sources(value: str) -> list[str]:
    sources: list[str] = []
    seen: set[str] = set()
    supported = set(SUPPORTED_SEARCH_SOURCES)
    for raw in value.split(","):
        source = raw.strip().lower()
        if not source or source in seen:
            continue
        if source not in supported:
            raise ValueError(f"unsupported source: {source}")
        seen.add(source)
        sources.append(source)
    if not sources:
        raise ValueError("--sources must contain at least one supported source")
    return sources


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行公开学术检索 Benchmark。")
    parser.add_argument("--dataset", required=True, choices=supported_datasets())
    parser.add_argument("--dataset-path", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--output-root", default="outputs/benchmark_runs")
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--run-profile",
        choices=["fast", "balanced", "high_recall", "evaluation"],
        default="balanced",
    )
    parser.add_argument(
        "--sources",
        default=",".join(SUPPORTED_SEARCH_SOURCES),
    )
    parser.add_argument(
        "--result-policy",
        choices=["highly_only", "highly_and_partial"],
        default="highly_and_partial",
    )
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--enable-query-evolution", action="store_true")
    parser.add_argument("--enable-refchain", action="store_true")
    parser.add_argument("--enable-llm-query-understanding", action="store_true")
    parser.add_argument("--enable-llm-judgement", action="store_true")
    parser.add_argument("--current-year", type=int, default=None)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--max-search-rounds", type=int, default=None)
    parser.add_argument("--max-candidate-papers", type=int, default=None)
    parser.add_argument("--max-llm-calls", type=int, default=None)
    parser.add_argument("--max-total-tokens", type=int, default=None)
    parser.add_argument("--max-latency-seconds", type=float, default=None)
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument(
        "--query-adapter-policy",
        choices=["safe_original", "hybrid", "adaptive"],
        default="adaptive",
    )
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        sources = _parse_sources(args.sources)
        default_budget = SearchBudget()
        budgets = SearchBudget(
            max_search_rounds=(
                default_budget.max_search_rounds
                if args.max_search_rounds is None
                else args.max_search_rounds
            ),
            max_candidate_papers=(
                default_budget.max_candidate_papers
                if args.max_candidate_papers is None
                else args.max_candidate_papers
            ),
            max_llm_calls=(
                default_budget.max_llm_calls
                if args.max_llm_calls is None
                else args.max_llm_calls
            ),
            max_total_tokens=(
                default_budget.max_total_tokens
                if args.max_total_tokens is None
                else args.max_total_tokens
            ),
            max_latency_seconds=(
                default_budget.max_latency_seconds
                if args.max_latency_seconds is None
                else args.max_latency_seconds
            ),
        )
        options = BenchmarkRunOptions(
            dataset=args.dataset,
            dataset_path=args.dataset_path,
            limit=args.limit,
            offset=args.offset,
            output_root=args.output_root,
            run_id=args.run_id,
            run_profile=args.run_profile,
            sources=sources,
            result_policy=args.result_policy,
            top_k=args.top_k,
            enable_query_evolution=args.enable_query_evolution,
            enable_refchain=args.enable_refchain,
            enable_llm_query_understanding=args.enable_llm_query_understanding,
            enable_llm_judgement=args.enable_llm_judgement,
            current_year=args.current_year,
            max_workers=args.max_workers,
            budgets=budgets,
            diagnostics=args.diagnostics,
            query_adapter_policy=args.query_adapter_policy,
            resume=args.resume,
        )
        result = run_benchmark(options)
    except (ValueError, OSError) as exc:
        print(_sanitize_message(str(exc)), file=sys.stderr)
        return 1
    print(result.run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

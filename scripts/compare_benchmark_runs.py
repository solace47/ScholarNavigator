#!/usr/bin/env python3
"""比较使用同一固定子集和预算的多个 Benchmark 运行。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def compare_runs(run_dirs: list[Path]) -> str:
    if len(run_dirs) < 2:
        raise ValueError("at least two --run directories are required")
    runs = [_load_run(path) for path in run_dirs]
    _validate_comparable(runs)
    baseline = runs[0]
    lines = [
        "# Benchmark 配置对比",
        "",
        "> 开发诊断样本，仅 10 条，不代表最终比赛成绩；不进行显著性声明。",
        "",
        (
            "| 配置 | 成功率 | F1@5 | F1@10 | F1@20 | P@20 | R@20 | "
            "初始候选 Recall | 最终返回 Recall@20 | Judgement FN 率 | "
            "平均 gold rank | 平均 API | 平均延迟（秒） | 来源错误率 | 瓶颈标签 |"
        ),
        (
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
            "---: | ---: | ---: | ---: | ---: | ---: | --- |"
        ),
    ]
    for run in runs:
        lines.append(_run_row(run))
    lines.extend(
        [
            "",
            "## 相对首个配置",
            "",
            "| 配置 | ΔF1@20 | ΔRecall@20 | Δ平均 API | Δ平均延迟（秒） |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for run in runs:
        lines.append(_delta_row(run, baseline))
    lines.append("")
    return "\n".join(lines)


def _load_run(path: Path) -> dict[str, Any]:
    run_dir = path.expanduser().resolve()
    config = _read_json(run_dir / "config.json")
    metrics = _read_json(run_dir / "metrics.json")
    stage_metrics = _read_json(run_dir / "stage_metrics.json")
    return {
        "run_dir": str(run_dir),
        "name": run_dir.name,
        "config": config,
        "metrics": metrics,
        "stage_metrics": stage_metrics,
    }


def _validate_comparable(runs: list[dict[str, Any]]) -> None:
    baseline = runs[0]["config"]
    fields = (
        "dataset",
        "dataset_sha256",
        "case_ids",
        "offset",
        "limit",
        "result_policy",
        "top_k",
        "budgets",
        "llm",
    )
    for run in runs[1:]:
        mismatched = [
            field
            for field in fields
            if run["config"].get(field) != baseline.get(field)
        ]
        if mismatched:
            raise ValueError(
                f"incompatible benchmark runs: {run['name']}: "
                + ",".join(mismatched)
            )


def _run_row(run: dict[str, Any]) -> str:
    metrics = run["metrics"]
    stage = run["stage_metrics"]
    end_to_end = metrics["end_to_end_metrics"]
    stats = metrics["case_statistics"]
    efficiency = metrics["benchmark_statistics"]
    judgement = stage.get("judgement", {})
    reranking = stage.get("reranking", {})
    sources = stage.get("source_contribution", {})
    labels = ", ".join(stage.get("bottleneck_labels") or []) or "-"
    return (
        f"| {run['name']} | {float(stats['success_rate']):.3f} | "
        f"{_at_k(end_to_end, 'f1_at_k', 5):.3f} | "
        f"{_at_k(end_to_end, 'f1_at_k', 10):.3f} | "
        f"{_at_k(end_to_end, 'f1_at_k', 20):.3f} | "
        f"{_at_k(end_to_end, 'precision_at_k', 20):.3f} | "
        f"{_at_k(end_to_end, 'recall_at_k', 20):.3f} | "
        f"{_optional(stage.get('initial_retrieval_recall'))} | "
        f"{_optional((stage.get('final_returned_recall') or {}).get('20'))} | "
        f"{float(judgement.get('gold_false_negative_rate') or 0.0):.3f} | "
        f"{_optional(reranking.get('average_gold_rank'))} | "
        f"{float(efficiency.get('average_api_calls') or 0.0):.3f} | "
        f"{float(efficiency.get('average_latency_seconds') or 0.0):.3f} | "
        f"{float(sources.get('source_error_rate') or 0.0):.3f} | {labels} |"
    )


def _delta_row(run: dict[str, Any], baseline: dict[str, Any]) -> str:
    return (
        f"| {run['name']} | {_delta(run, baseline, 'f1'):.3f} | "
        f"{_delta(run, baseline, 'recall'):.3f} | "
        f"{_delta(run, baseline, 'api'):.3f} | "
        f"{_delta(run, baseline, 'latency'):.3f} |"
    )


def _delta(run: dict[str, Any], baseline: dict[str, Any], field: str) -> float:
    def value(item: dict[str, Any]) -> float:
        metrics = item["metrics"]
        if field == "f1":
            return _at_k(metrics["end_to_end_metrics"], "f1_at_k", 20)
        if field == "recall":
            return _at_k(metrics["end_to_end_metrics"], "recall_at_k", 20)
        if field == "api":
            return float(
                metrics["benchmark_statistics"].get("average_api_calls") or 0.0
            )
        return float(
            metrics["benchmark_statistics"].get("average_latency_seconds") or 0.0
        )

    return value(run) - value(baseline)


def _at_k(metrics: dict[str, Any], name: str, k: int) -> float:
    values = metrics.get(name) or {}
    return float(values.get(str(k), values.get(k, 0.0)))


def _optional(value: Any) -> str:
    return f"{float(value):.3f}" if value is not None else "-"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"missing benchmark output: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid benchmark JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid benchmark JSON object: {path}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="比较多个 Benchmark 运行。")
    parser.add_argument("--run", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        report = compare_runs([Path(item) for item in args.run])
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

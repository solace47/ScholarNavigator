#!/usr/bin/env python3
"""Generate a Markdown summary for offline evaluation results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scholar_agent.core.evaluation_schemas import EvalSuiteResult  # noqa: E402


DEFAULT_K_VALUES = (5, 10, 20)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize an offline evaluation result.json file."
    )
    parser.add_argument("result_json", help="Path to result.json.")
    parser.add_argument(
        "--output",
        default=None,
        help="Markdown output path. Defaults to summary.md next to result.json.",
    )
    args = parser.parse_args()

    result_path = Path(args.result_json)
    with result_path.open("r", encoding="utf-8") as handle:
        result = EvalSuiteResult.model_validate(json.load(handle))

    summary = build_markdown_summary(result)
    output_path = Path(args.output) if args.output else result_path.with_name("summary.md")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(summary, encoding="utf-8")
    print(output_path)
    return 0


def build_markdown_summary(result: EvalSuiteResult) -> str:
    lines = [
        "# Offline Evaluation Summary",
        "",
        f"Query count: {len(result.query_results)}",
        "",
        "| Group | R@5 | R@10 | R@20 | P@5 | P@10 | P@20 | MRR | nDCG@5 | nDCG@10 | nDCG@20 | Raw | Dedup | Warnings | Source Error Rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for group, metrics in result.aggregate_metrics.items():
        lines.append(
            "| {group} | {r5:.3f} | {r10:.3f} | {r20:.3f} | "
            "{p5:.3f} | {p10:.3f} | {p20:.3f} | {mrr:.3f} | "
            "{n5:.3f} | {n10:.3f} | {n20:.3f} | {raw} | {dedup} | "
            "{warnings} | {error_rate:.3f} |".format(
                group=group,
                r5=_metric_at(metrics.recall_at_k, 5),
                r10=_metric_at(metrics.recall_at_k, 10),
                r20=_metric_at(metrics.recall_at_k, 20),
                p5=_metric_at(metrics.precision_at_k, 5),
                p10=_metric_at(metrics.precision_at_k, 10),
                p20=_metric_at(metrics.precision_at_k, 20),
                mrr=metrics.mrr,
                n5=_metric_at(metrics.ndcg_at_k, 5),
                n10=_metric_at(metrics.ndcg_at_k, 10),
                n20=_metric_at(metrics.ndcg_at_k, 20),
                raw=metrics.raw_count,
                dedup=metrics.deduplicated_count,
                warnings=metrics.warning_count,
                error_rate=metrics.source_error_rate,
            )
        )
    lines.append("")
    lines.append("## Per Query")
    for query_result in result.query_results:
        lines.append("")
        lines.append(f"### {query_result.query_id}")
        lines.append("")
        lines.append(query_result.query)
        lines.append("")
        lines.append("| Group | Ranked IDs | Warnings |")
        lines.append("| --- | --- | --- |")
        for group, group_result in query_result.group_results.items():
            ranked_ids = ", ".join(group_result.ranked_paper_ids[:5]) or "-"
            warnings = ", ".join(group_result.warnings) or "-"
            lines.append(f"| {group} | {ranked_ids} | {warnings} |")
    lines.append("")
    return "\n".join(lines)


def _metric_at(values: dict[int, float], k: int) -> float:
    return float(values.get(k, values.get(str(k), 0.0)))  # type: ignore[arg-type]


if __name__ == "__main__":
    raise SystemExit(main())

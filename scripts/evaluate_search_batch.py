#!/usr/bin/env python3
"""Evaluate run_search_batch.py JSONL output against gold/qrels JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scholar_agent.core.evaluation_schemas import EvalGoldPaper  # noqa: E402
from scholar_agent.evaluation.metrics import (  # noqa: E402
    canonical_paper_id,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


DEFAULT_K_VALUES = [5, 10]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate SearchService batch JSONL results against gold qrels."
    )
    parser.add_argument(
        "--batch-results",
        required=True,
        help="JSONL output produced by scripts/run_search_batch.py.",
    )
    parser.add_argument("--gold", required=True, help="Gold/qrels JSONL file.")
    parser.add_argument(
        "--output",
        default=None,
        help="JSON output path. Defaults to stdout.",
    )
    parser.add_argument(
        "--k",
        action="append",
        type=int,
        default=None,
        help="K value to evaluate. Repeatable. Defaults to 5 and 10.",
    )
    parser.add_argument(
        "--include-partial",
        action="store_true",
        help="Include partially_relevant_papers after highly_relevant_papers.",
    )
    args = parser.parse_args(argv)

    batch_path = Path(args.batch_results)
    gold_path = Path(args.gold)
    for label, path in (("batch results", batch_path), ("gold", gold_path)):
        if not path.exists():
            print(f"{label} file not found: {path}", file=sys.stderr)
            return 1
        if not path.is_file():
            print(f"{label} path is not a file: {path}", file=sys.stderr)
            return 1

    k_values = _normalize_k_values(args.k or DEFAULT_K_VALUES)
    try:
        batch_rows = load_jsonl_objects(batch_path, label="batch results")
        gold_rows = load_gold_rows(gold_path)
        result = evaluate_batch_results(
            batch_rows,
            gold_rows,
            k_values=k_values,
            include_partial=args.include_partial,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


def load_jsonl_objects(path: Path, *, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid {label} JSONL at line {line_number}: {exc.msg}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(
                f"invalid {label} JSONL at line {line_number}: expected object"
            )
        rows.append(payload)
    return rows


def load_gold_rows(path: Path) -> list[dict[str, Any]]:
    rows = load_jsonl_objects(path, label="gold")
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        case_id = str(row.get("case_id") or "").strip()
        if not case_id:
            raise ValueError(f"invalid gold JSONL at line {index}: missing case_id")
        raw_papers = row.get("relevant_papers", [])
        if raw_papers is None:
            raw_papers = []
        if not isinstance(raw_papers, list):
            raise ValueError(
                f"invalid gold JSONL at line {index}: relevant_papers must be a list"
            )
        try:
            gold_papers = [
                EvalGoldPaper.model_validate(paper).model_dump(mode="json")
                for paper in raw_papers
            ]
        except Exception as exc:  # noqa: BLE001 - surface malformed gold row
            raise ValueError(f"invalid gold JSONL at line {index}: {exc}") from exc
        normalized.append({"case_id": case_id, "relevant_papers": gold_papers})
    return normalized


def evaluate_batch_results(
    batch_rows: list[dict[str, Any]],
    gold_rows: list[dict[str, Any]],
    *,
    k_values: list[int] | None = None,
    include_partial: bool = False,
) -> dict[str, Any]:
    k_values = _normalize_k_values(k_values or DEFAULT_K_VALUES)
    batch_by_case = {
        str(row.get("case_id") or "").strip(): row
        for row in batch_rows
        if str(row.get("case_id") or "").strip()
    }
    gold_by_case = {
        str(row.get("case_id") or "").strip(): list(row.get("relevant_papers") or [])
        for row in gold_rows
        if str(row.get("case_id") or "").strip()
    }

    failed_cases: list[dict[str, Any]] = []
    missing_gold_cases: list[str] = []
    missing_result_cases: list[str] = []
    per_case: list[dict[str, Any]] = []
    evaluated_metrics: list[dict[str, Any]] = []

    for row in batch_rows:
        case_id = str(row.get("case_id") or "").strip()
        if not case_id:
            continue
        status = str(row.get("status") or "")
        if status == "failed":
            failed_cases.append(
                {
                    "case_id": case_id,
                    "query": str(row.get("query") or ""),
                    "error": str(row.get("error") or ""),
                }
            )
        if case_id not in gold_by_case:
            missing_gold_cases.append(case_id)
            continue
        result = row.get("result")
        if status != "succeeded" or not isinstance(result, dict):
            continue

        ranked = extract_ranked_papers(result, include_partial=include_partial)
        gold = gold_by_case[case_id]
        metrics = _case_metrics(ranked, gold, k_values)
        case_payload = {
            "case_id": case_id,
            "query": str(row.get("query") or ""),
            "ranked_count": len(ranked),
            "gold_count": _positive_gold_count(gold),
            "matched_ids": _matched_ids(ranked, gold),
            "metrics": metrics,
        }
        per_case.append(case_payload)
        evaluated_metrics.append(metrics)

    for case_id in gold_by_case:
        if case_id not in batch_by_case:
            missing_result_cases.append(case_id)

    aggregate = _aggregate_metrics(evaluated_metrics, k_values)
    return {
        "config": {
            "k_values": k_values,
            "include_partial": include_partial,
            "failed_cases_policy": "excluded_from_metric_averages",
        },
        "aggregate": aggregate,
        "per_case": per_case,
        "failed_cases": failed_cases,
        "missing_gold_cases": missing_gold_cases,
        "missing_result_cases": missing_result_cases,
        "case_count": len(batch_rows),
        "evaluated_case_count": len(per_case),
    }


def extract_ranked_papers(
    result: dict[str, Any],
    *,
    include_partial: bool = False,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    high = result.get("highly_relevant_papers")
    if isinstance(high, list):
        ranked.extend(item for item in high if isinstance(item, dict))
    if include_partial:
        partial = result.get("partially_relevant_papers")
        if isinstance(partial, list):
            ranked.extend(item for item in partial if isinstance(item, dict))
    return ranked


def _case_metrics(
    ranked: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    k_values: list[int],
) -> dict[str, Any]:
    return {
        "recall_at_k": {
            str(k): recall_at_k(ranked, gold, k) for k in k_values
        },
        "precision_at_k": {
            str(k): precision_at_k(ranked, gold, k) for k in k_values
        },
        "mrr": mrr(ranked, gold),
        "ndcg_at_k": {str(k): ndcg_at_k(ranked, gold, k) for k in k_values},
    }


def _aggregate_metrics(
    case_metrics: list[dict[str, Any]],
    k_values: list[int],
) -> dict[str, Any]:
    if not case_metrics:
        return {
            "recall_at_k": {str(k): 0.0 for k in k_values},
            "precision_at_k": {str(k): 0.0 for k in k_values},
            "mrr": 0.0,
            "ndcg_at_k": {str(k): 0.0 for k in k_values},
        }

    count = len(case_metrics)
    return {
        "recall_at_k": {
            str(k): sum(item["recall_at_k"][str(k)] for item in case_metrics) / count
            for k in k_values
        },
        "precision_at_k": {
            str(k): sum(item["precision_at_k"][str(k)] for item in case_metrics)
            / count
            for k in k_values
        },
        "mrr": sum(item["mrr"] for item in case_metrics) / count,
        "ndcg_at_k": {
            str(k): sum(item["ndcg_at_k"][str(k)] for item in case_metrics) / count
            for k in k_values
        },
    }


def _matched_ids(
    ranked: list[dict[str, Any]],
    gold: list[dict[str, Any]],
) -> list[str]:
    gold_ids = {canonical_paper_id(paper) for paper in gold}
    gold_ids.discard(None)
    matched: list[str] = []
    seen: set[str] = set()
    for paper in ranked:
        paper_id = canonical_paper_id(paper)
        if paper_id is None or paper_id in seen or paper_id not in gold_ids:
            continue
        matched.append(paper_id)
        seen.add(paper_id)
    return matched


def _positive_gold_count(gold: list[dict[str, Any]]) -> int:
    return sum(1 for paper in gold if canonical_paper_id(paper) is not None)


def _normalize_k_values(values: list[int]) -> list[int]:
    normalized = sorted({int(value) for value in values if int(value) > 0})
    if not normalized:
        raise ValueError("at least one positive --k value is required")
    return normalized


if __name__ == "__main__":
    raise SystemExit(main())

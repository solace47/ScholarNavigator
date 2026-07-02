#!/usr/bin/env python3
"""Evaluate run_search_batch.py JSONL output against gold/qrels JSONL."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scholar_agent.core.evaluation_schemas import EvalGoldPaper  # noqa: E402

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
    gold_records = _gold_match_records(gold)
    return {
        "recall_at_k": {
            str(k): _recall_at_k(ranked, gold_records, k) for k in k_values
        },
        "precision_at_k": {
            str(k): _precision_at_k(ranked, gold_records, k) for k in k_values
        },
        "mrr": _mrr(ranked, gold_records),
        "ndcg_at_k": {str(k): _ndcg_at_k(ranked, gold_records, k) for k in k_values},
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
    gold_records = _gold_match_records(gold)
    matched: list[str] = []
    seen_gold_indexes: set[int] = set()
    for paper in ranked:
        match = _first_matching_gold(paper, gold_records, seen_gold_indexes)
        if match is None:
            continue
        gold_index, match_key = match
        matched.append(match_key)
        seen_gold_indexes.add(gold_index)
    return matched


def _positive_gold_count(gold: list[dict[str, Any]]) -> int:
    return sum(1 for record in _gold_match_records(gold) if record["grade"] > 0)


def _recall_at_k(
    ranked: list[dict[str, Any]],
    gold_records: list[dict[str, Any]],
    k: int,
) -> float:
    if k <= 0:
        return 0.0
    relevant_count = sum(1 for record in gold_records if record["grade"] > 0)
    if relevant_count == 0:
        return 0.0
    matched_indexes = _matched_gold_indexes(ranked[:k], gold_records)
    return len(matched_indexes) / relevant_count


def _precision_at_k(
    ranked: list[dict[str, Any]],
    gold_records: list[dict[str, Any]],
    k: int,
) -> float:
    if k <= 0 or not ranked:
        return 0.0
    if not any(record["grade"] > 0 for record in gold_records):
        return 0.0
    matched_indexes = _matched_gold_indexes(ranked[:k], gold_records)
    return len(matched_indexes) / k


def _mrr(ranked: list[dict[str, Any]], gold_records: list[dict[str, Any]]) -> float:
    if not any(record["grade"] > 0 for record in gold_records):
        return 0.0
    seen_gold_indexes: set[int] = set()
    for rank, paper in enumerate(ranked, start=1):
        match = _first_matching_gold(paper, gold_records, seen_gold_indexes)
        if match is not None:
            return 1.0 / rank
    return 0.0


def _ndcg_at_k(
    ranked: list[dict[str, Any]],
    gold_records: list[dict[str, Any]],
    k: int,
) -> float:
    if k <= 0:
        return 0.0
    positive_grades = [record["grade"] for record in gold_records if record["grade"] > 0]
    if not positive_grades:
        return 0.0

    seen_gold_indexes: set[int] = set()
    gains: list[float] = []
    for paper in ranked[:k]:
        match = _first_matching_gold(paper, gold_records, seen_gold_indexes)
        if match is None:
            gains.append(0.0)
            continue
        gold_index, _ = match
        gains.append(float(gold_records[gold_index]["grade"]))
        seen_gold_indexes.add(gold_index)

    dcg = _dcg(gains)
    idcg = _dcg(sorted(positive_grades, reverse=True)[:k])
    return dcg / idcg if idcg > 0 else 0.0


def _matched_gold_indexes(
    ranked: list[dict[str, Any]],
    gold_records: list[dict[str, Any]],
) -> set[int]:
    matched: set[int] = set()
    for paper in ranked:
        match = _first_matching_gold(paper, gold_records, matched)
        if match is None:
            continue
        gold_index, _ = match
        matched.add(gold_index)
    return matched


def _first_matching_gold(
    predicted_paper: dict[str, Any],
    gold_records: list[dict[str, Any]],
    seen_gold_indexes: set[int],
) -> tuple[int, str] | None:
    predicted_ids = _paper_identifier_set(predicted_paper)
    predicted_title_key = _title_year_key(predicted_paper)
    for index, record in enumerate(gold_records):
        if index in seen_gold_indexes or record["grade"] <= 0:
            continue
        match_key = _match_key(
            predicted_ids,
            predicted_title_key,
            record["identifiers"],
            record["title_key"],
        )
        if match_key is not None:
            return index, match_key
    return None


def _match_key(
    predicted_ids: set[str],
    predicted_title_key: str | None,
    gold_ids: set[str],
    gold_title_key: str | None,
) -> str | None:
    if predicted_ids or gold_ids:
        shared = predicted_ids.intersection(gold_ids)
        return _preferred_identifier(shared) if shared else None
    if predicted_title_key and predicted_title_key == gold_title_key:
        return predicted_title_key
    return None


def _gold_match_records(gold: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for paper in gold:
        identifiers = _paper_identifier_set(paper)
        title_key = None if identifiers else _title_year_key(paper)
        if not identifiers and title_key is None:
            continue
        records.append(
            {
                "identifiers": identifiers,
                "title_key": title_key,
                "grade": _relevance_grade(paper),
            }
        )
    return records


def _paper_identifier_set(paper: Any) -> set[str]:
    identifiers: set[str] = set()

    doi_value = _extract_identifier(paper, "doi")
    if doi_value:
        normalized_doi = _normalize_doi(doi_value)
        if normalized_doi:
            identifiers.add(f"doi:{normalized_doi}")
            arxiv_id = _arxiv_id_from_doi(normalized_doi)
            if arxiv_id:
                identifiers.add(f"arxiv:{arxiv_id}")

    arxiv_value = _extract_identifier(paper, "arxiv_id")
    if arxiv_value:
        identifiers.add(f"arxiv:{_normalize_arxiv_id(arxiv_value)}")

    semantic_scholar_value = (
        _extract_identifier(paper, "semantic_scholar_id")
        or _extract_identifier(paper, "s2_id")
        or _extract_identifier(paper, "corpus_id")
        or _extract_identifier(paper, "paper_id")
    )
    if semantic_scholar_value:
        identifiers.add(f"s2:{_normalize_semantic_scholar_id(semantic_scholar_value)}")

    return {identifier for identifier in identifiers if identifier.split(":", 1)[1]}


def _extract_identifier(paper: Any, name: str) -> str | None:
    unwrapped = _unwrap_ranked_paper(paper)
    value = _get_value(unwrapped, name)
    if value:
        return str(value)
    nested_identifiers = _get_value(unwrapped, "identifiers")
    value = _get_value(nested_identifiers, name)
    if value:
        return str(value)
    return None


def _title_year_key(paper: Any) -> str | None:
    unwrapped = _unwrap_ranked_paper(paper)
    title = _normalize_title(str(_get_value(unwrapped, "title") or ""))
    year = _get_value(unwrapped, "year")
    if not title or year is None:
        return None
    return f"title_year:{title}:{year}"


def _preferred_identifier(identifiers: set[str]) -> str | None:
    if not identifiers:
        return None
    priority = ("arxiv:", "doi:", "s2:")
    for prefix in priority:
        values = sorted(identifier for identifier in identifiers if identifier.startswith(prefix))
        if values:
            return values[0]
    return sorted(identifiers)[0]


def _relevance_grade(paper: Any) -> float:
    grade = _get_value(_unwrap_ranked_paper(paper), "relevance_grade")
    if grade is None:
        return 1.0
    try:
        return max(0.0, float(grade))
    except (TypeError, ValueError):
        return 0.0


def _dcg(gains: list[float]) -> float:
    score = 0.0
    for index, gain in enumerate(gains, start=1):
        if gain <= 0:
            continue
        score += (math.pow(2.0, gain) - 1.0) / math.log2(index + 1)
    return score


def _unwrap_ranked_paper(paper: Any) -> Any:
    if isinstance(paper, Mapping) and "paper" in paper:
        return paper["paper"]
    if hasattr(paper, "paper"):
        return getattr(paper, "paper")
    return paper


def _get_value(item: Any, key: str) -> Any:
    if item is None:
        return None
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def _normalize_doi(value: str) -> str:
    normalized = value.strip().casefold()
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized.strip()


def _arxiv_id_from_doi(normalized_doi: str) -> str | None:
    match = re.fullmatch(r"10\.48550/arxiv\.(.+)", normalized_doi.strip())
    if not match:
        return None
    arxiv_id = _normalize_arxiv_id(match.group(1))
    return arxiv_id or None


def _normalize_arxiv_id(value: str) -> str:
    normalized = value.strip().casefold()
    normalized = normalized.split("?", 1)[0].rstrip("/")
    if "arxiv.org/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    for prefix in ("arxiv:", "abs/", "pdf/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    if normalized.endswith(".pdf"):
        normalized = normalized[:-4]
    return re.sub(r"v\d+$", "", normalized).strip()


def _normalize_semantic_scholar_id(value: str) -> str:
    normalized = value.strip().casefold()
    for prefix in ("semantic_scholar:", "semantic-scholar:", "corpusid:", "s2:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized.strip()


def _normalize_title(value: str) -> str:
    normalized = value.casefold()
    normalized = re.sub(r"\\[a-zA-Z]+\*?", " ", normalized)
    normalized = re.sub(r"[{}$^_~]", " ", normalized)
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _normalize_k_values(values: list[int]) -> list[int]:
    normalized = sorted({int(value) for value in values if int(value) > 0})
    if not normalized:
        raise ValueError("at least one positive --k value is required")
    return normalized


if __name__ == "__main__":
    raise SystemExit(main())

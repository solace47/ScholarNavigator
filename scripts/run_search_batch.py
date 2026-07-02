#!/usr/bin/env python3
"""Run SearchService for a batch of JSONL queries."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scholar_agent.services.api_mapper import (  # noqa: E402
    map_search_service_output_to_api_result,
)
from scholar_agent.services.search_service import SearchService  # noqa: E402


SUPPORTED_SOURCES = {"openalex", "arxiv", "semantic_scholar"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run SearchService over a JSONL query file and write JSONL results."
    )
    parser.add_argument("--input", required=True, help="Input JSONL query file.")
    parser.add_argument("--output", required=True, help="Output JSONL result file.")
    parser.add_argument("--top-k", type=int, default=20, help="Default top_k.")
    parser.add_argument(
        "--run-profile",
        default="balanced",
        choices=["fast", "balanced", "high_recall", "evaluation"],
        help="Default run profile.",
    )
    parser.add_argument(
        "--current-year",
        type=int,
        default=None,
        help="Default current year for reproducible time parsing.",
    )
    parser.add_argument(
        "--enable-query-evolution",
        action="store_true",
        help="Enable Query Evolution by default for rows that do not override it.",
    )
    parser.add_argument(
        "--enable-refchain",
        action="store_true",
        help="Enable RefChain by default for rows that do not override it.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="SearchService(max_workers=...).",
    )
    parser.add_argument(
        "--sources",
        default=None,
        help=(
            "Comma-separated default retrieval sources, for example "
            "arxiv,semantic_scholar. JSONL source_preferences overrides this."
        ),
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop and return non-zero after the first per-row failure.",
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        print(f"input file not found: {input_path}", file=sys.stderr)
        return 1
    if not input_path.is_file():
        print(f"input path is not a file: {input_path}", file=sys.stderr)
        return 1

    try:
        cases = _load_cases(input_path)
        default_sources = _parse_sources(args.sources, field_name="--sources")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    service = SearchService(max_workers=args.max_workers)
    had_failure = False

    with output_path.open("w", encoding="utf-8") as handle:
        for case in cases:
            result = _run_case(
                case,
                service=service,
                default_top_k=args.top_k,
                default_run_profile=args.run_profile,
                default_current_year=args.current_year,
                default_enable_query_evolution=args.enable_query_evolution,
                default_enable_refchain=args.enable_refchain,
                default_sources=default_sources,
            )
            if result["status"] == "failed":
                had_failure = True
            handle.write(json.dumps(result, ensure_ascii=False))
            handle.write("\n")
            handle.flush()
            if had_failure and args.fail_fast:
                return 1

    return 0


def _load_cases(input_path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        input_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_number}: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"invalid JSONL at line {line_number}: expected object")
        payload = dict(payload)
        if not str(payload.get("case_id") or "").strip():
            payload["case_id"] = f"row_{len(cases) + 1}"
        cases.append(payload)
    return cases


def _run_case(
    case: dict[str, Any],
    *,
    service: SearchService,
    default_top_k: int,
    default_run_profile: str,
    default_current_year: int | None,
    default_enable_query_evolution: bool,
    default_enable_refchain: bool,
    default_sources: list[str] | None,
) -> dict[str, Any]:
    start = time.perf_counter()
    case_id = str(case["case_id"])
    query = str(case.get("query") or "")
    try:
        if not query.strip():
            raise ValueError("query must not be empty")
        top_k = int(case.get("top_k", default_top_k))
        run_profile = str(case.get("run_profile", default_run_profile))
        current_year = case.get("current_year", default_current_year)
        if current_year is not None:
            current_year = int(current_year)
        enable_query_evolution = bool(
            case.get("enable_query_evolution", default_enable_query_evolution)
        )
        enable_refchain = bool(case.get("enable_refchain", default_enable_refchain))
        sources_override = _case_sources(case, default_sources)

        output = service.run_search(
            query,
            top_k=top_k,
            run_profile=run_profile,  # type: ignore[arg-type]
            enable_query_evolution=enable_query_evolution,
            enable_refchain=enable_refchain,
            enable_synthesis=True,
            current_year=current_year,
            sources_override=sources_override,
        )
        api_result = map_search_service_output_to_api_result(
            run_id=f"batch_{case_id}",
            output=output,
            status="succeeded",
            partial=False,
        )
        return {
            "case_id": case_id,
            "query": query,
            "status": "succeeded",
            "result": api_result.model_dump(mode="json"),
            "error": None,
            "latency_seconds": time.perf_counter() - start,
        }
    except Exception as exc:  # noqa: BLE001 - isolate per-row batch failure
        return {
            "case_id": case_id,
            "query": query,
            "status": "failed",
            "result": None,
            "error": str(exc),
            "latency_seconds": time.perf_counter() - start,
        }


def _case_sources(
    case: dict[str, Any],
    default_sources: list[str] | None,
) -> list[str] | None:
    if "source_preferences" not in case:
        return default_sources
    return _parse_sources(case.get("source_preferences"), field_name="source_preferences")


def _parse_sources(value: Any, *, field_name: str) -> list[str] | None:
    if value is None:
        return None

    if isinstance(value, str):
        raw_sources = value.split(",")
    elif isinstance(value, list):
        raw_sources = value
    else:
        raise ValueError(f"{field_name} must be a comma-separated string or list")

    normalized: list[str] = []
    seen: set[str] = set()
    invalid: list[str] = []
    for item in raw_sources:
        source = str(item).strip().lower()
        if not source:
            continue
        if source not in SUPPORTED_SOURCES:
            invalid.append(source)
            continue
        if source not in seen:
            normalized.append(source)
            seen.add(source)

    if invalid:
        allowed = ", ".join(sorted(SUPPORTED_SOURCES))
        raise ValueError(
            f"{field_name} contains unsupported source(s): {', '.join(invalid)}; "
            f"allowed sources: {allowed}"
        )
    return normalized or None


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scholar_agent.agents.synthesis import synthesize_answer  # noqa: E402
from scholar_agent.core.search_schemas import (  # noqa: E402
    QueryAnalysis,
    QueryConstraint,
    SearchPlan,
    SearchSubquery,
)
from scholar_agent.services.search_service import SearchServiceOutput  # noqa: E402
from scripts import run_search_batch  # noqa: E402


def test_batch_runs_two_queries_and_writes_succeeded_jsonl(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_path = _write_jsonl(
        tmp_path / "queries.jsonl",
        [
            {"case_id": "case_001", "query": "LLM reranking"},
            {"case_id": "case_002", "query": "scientific retrieval"},
        ],
    )
    output_path = tmp_path / "out" / "results.jsonl"

    monkeypatch.setattr(run_search_batch, "SearchService", _fake_service_class())

    code = run_search_batch.main(
        ["--input", str(input_path), "--output", str(output_path)]
    )

    rows = _read_jsonl(output_path)
    assert code == 0
    assert [row["status"] for row in rows] == ["succeeded", "succeeded"]
    assert [row["case_id"] for row in rows] == ["case_001", "case_002"]
    assert rows[0]["result"]["run_id"] == "batch_case_001"
    assert rows[0]["result"]["synthesis"] is not None


def test_row_parameters_override_cli_defaults(tmp_path: Path, monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    input_path = _write_jsonl(
        tmp_path / "queries.jsonl",
        [
            {
                "case_id": "override",
                "query": "latest LLM reranking",
                "top_k": 7,
                "run_profile": "high_recall",
                "current_year": 2026,
                "enable_query_evolution": False,
                "enable_refchain": True,
                "source_preferences": ["semantic_scholar"],
            }
        ],
    )
    output_path = tmp_path / "results.jsonl"

    monkeypatch.setattr(
        run_search_batch,
        "SearchService",
        _fake_service_class(captured=captured),
    )

    code = run_search_batch.main(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--top-k",
            "3",
            "--run-profile",
            "fast",
            "--current-year",
            "2024",
            "--enable-query-evolution",
            "--sources",
            "arxiv,openalex",
            "--max-workers",
            "2",
        ]
    )

    assert code == 0
    assert captured[0]["max_workers"] == 2
    assert captured[0]["top_k"] == 7
    assert captured[0]["run_profile"] == "high_recall"
    assert captured[0]["current_year"] == 2026
    assert captured[0]["enable_query_evolution"] is False
    assert captured[0]["enable_refchain"] is True
    assert captured[0]["sources_override"] == ["semantic_scholar"]


def test_cli_sources_are_used_as_default(tmp_path: Path, monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    input_path = _write_jsonl(
        tmp_path / "queries.jsonl",
        [{"case_id": "default_sources", "query": "LLM retrieval"}],
    )
    output_path = tmp_path / "results.jsonl"

    monkeypatch.setattr(
        run_search_batch,
        "SearchService",
        _fake_service_class(captured=captured),
    )

    code = run_search_batch.main(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--sources",
            "arxiv,semantic_scholar",
        ]
    )

    rows = _read_jsonl(output_path)
    assert code == 0
    assert rows[0]["status"] == "succeeded"
    assert captured[0]["sources_override"] == ["arxiv", "semantic_scholar"]


def test_sleep_between_cases_sleeps_only_between_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sleep_calls: list[float] = []
    input_path = _write_jsonl(
        tmp_path / "queries.jsonl",
        [
            {"case_id": "case_001", "query": "LLM reranking"},
            {"case_id": "case_002", "query": "scientific retrieval"},
            {"case_id": "case_003", "query": "academic search"},
        ],
    )
    output_path = tmp_path / "results.jsonl"

    monkeypatch.setattr(run_search_batch, "SearchService", _fake_service_class())
    monkeypatch.setattr(run_search_batch.time, "sleep", sleep_calls.append)

    code = run_search_batch.main(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--sleep-between-cases-seconds",
            "1.5",
        ]
    )

    rows = _read_jsonl(output_path)
    assert code == 0
    assert [row["status"] for row in rows] == ["succeeded", "succeeded", "succeeded"]
    assert sleep_calls == [1.5, 1.5]


def test_negative_sleep_between_cases_returns_nonzero(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_path = _write_jsonl(
        tmp_path / "queries.jsonl",
        [{"case_id": "case_001", "query": "LLM reranking"}],
    )
    output_path = tmp_path / "results.jsonl"

    monkeypatch.setattr(run_search_batch, "SearchService", _fake_service_class())

    code = run_search_batch.main(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--sleep-between-cases-seconds",
            "-1",
        ]
    )

    assert code == 1
    assert not output_path.exists()


def test_invalid_row_source_outputs_failed_row_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: list[dict[str, Any]] = []
    input_path = _write_jsonl(
        tmp_path / "queries.jsonl",
        [
            {
                "case_id": "bad_source",
                "query": "LLM retrieval",
                "source_preferences": ["arxiv", "pubmed"],
            },
            {
                "case_id": "good",
                "query": "scientific search",
                "source_preferences": ["arxiv"],
            },
        ],
    )
    output_path = tmp_path / "results.jsonl"

    monkeypatch.setattr(
        run_search_batch,
        "SearchService",
        _fake_service_class(captured=captured),
    )

    code = run_search_batch.main(
        ["--input", str(input_path), "--output", str(output_path)]
    )

    rows = _read_jsonl(output_path)
    assert code == 0
    assert rows[0]["status"] == "failed"
    assert rows[0]["result"] is None
    assert "unsupported source(s): pubmed" in rows[0]["error"]
    assert rows[1]["status"] == "succeeded"
    assert captured == [
        {
            "max_workers": 4,
            "query": "scientific search",
            "top_k": 20,
            "run_profile": "balanced",
            "enable_refchain": False,
            "enable_query_evolution": False,
            "enable_synthesis": True,
            "current_year": None,
            "sources_override": ["arxiv"],
        }
    ]


def test_invalid_cli_sources_returns_nonzero_before_writing_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_path = _write_jsonl(
        tmp_path / "queries.jsonl",
        [{"case_id": "case", "query": "LLM retrieval"}],
    )
    output_path = tmp_path / "results.jsonl"

    monkeypatch.setattr(run_search_batch, "SearchService", _fake_service_class())

    code = run_search_batch.main(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--sources",
            "arxiv,pubmed",
        ]
    )

    assert code == 1
    assert not output_path.exists()


def test_invalid_row_source_with_fail_fast_returns_nonzero(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_path = _write_jsonl(
        tmp_path / "queries.jsonl",
        [
            {
                "case_id": "bad_source",
                "query": "LLM retrieval",
                "source_preferences": ["unknown"],
            },
            {"case_id": "skipped", "query": "scientific search"},
        ],
    )
    output_path = tmp_path / "results.jsonl"

    monkeypatch.setattr(run_search_batch, "SearchService", _fake_service_class())

    code = run_search_batch.main(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--fail-fast",
        ]
    )

    rows = _read_jsonl(output_path)
    assert code == 1
    assert len(rows) == 1
    assert rows[0]["case_id"] == "bad_source"
    assert rows[0]["status"] == "failed"


def test_missing_case_id_generates_row_ids(tmp_path: Path, monkeypatch) -> None:
    input_path = _write_jsonl(
        tmp_path / "queries.jsonl",
        [
            {"query": "first query"},
            {"case_id": "", "query": "second query"},
        ],
    )
    output_path = tmp_path / "results.jsonl"

    monkeypatch.setattr(run_search_batch, "SearchService", _fake_service_class())

    code = run_search_batch.main(
        ["--input", str(input_path), "--output", str(output_path)]
    )

    rows = _read_jsonl(output_path)
    assert code == 0
    assert [row["case_id"] for row in rows] == ["row_1", "row_2"]
    assert [row["result"]["run_id"] for row in rows] == ["batch_row_1", "batch_row_2"]


def test_single_failure_continues_by_default(tmp_path: Path, monkeypatch) -> None:
    input_path = _write_jsonl(
        tmp_path / "queries.jsonl",
        [
            {"case_id": "bad", "query": "explode"},
            {"case_id": "good", "query": "LLM retrieval"},
        ],
    )
    output_path = tmp_path / "results.jsonl"

    monkeypatch.setattr(
        run_search_batch,
        "SearchService",
        _fake_service_class(fail_queries={"explode"}),
    )

    code = run_search_batch.main(
        ["--input", str(input_path), "--output", str(output_path)]
    )

    rows = _read_jsonl(output_path)
    assert code == 0
    assert [row["status"] for row in rows] == ["failed", "succeeded"]
    assert rows[0]["result"] is None
    assert rows[0]["error"] == "forced failure"
    assert rows[1]["result"]["synthesis"] is not None


def test_fail_fast_returns_nonzero_after_first_failed_row(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_path = _write_jsonl(
        tmp_path / "queries.jsonl",
        [
            {"case_id": "bad", "query": "explode"},
            {"case_id": "skipped", "query": "LLM retrieval"},
        ],
    )
    output_path = tmp_path / "results.jsonl"

    monkeypatch.setattr(
        run_search_batch,
        "SearchService",
        _fake_service_class(fail_queries={"explode"}),
    )

    code = run_search_batch.main(
        ["--input", str(input_path), "--output", str(output_path), "--fail-fast"]
    )

    rows = _read_jsonl(output_path)
    assert code == 1
    assert len(rows) == 1
    assert rows[0]["case_id"] == "bad"
    assert rows[0]["status"] == "failed"


def test_empty_query_outputs_failed_row_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_path = _write_jsonl(
        tmp_path / "queries.jsonl",
        [{"case_id": "empty", "query": "  "}],
    )
    output_path = tmp_path / "results.jsonl"

    monkeypatch.setattr(run_search_batch, "SearchService", _fake_service_class())

    code = run_search_batch.main(
        ["--input", str(input_path), "--output", str(output_path)]
    )

    rows = _read_jsonl(output_path)
    assert code == 0
    assert rows[0]["status"] == "failed"
    assert rows[0]["error"] == "query must not be empty"
    assert rows[0]["result"] is None


def test_invalid_jsonl_returns_nonzero(tmp_path: Path, monkeypatch) -> None:
    input_path = tmp_path / "bad.jsonl"
    input_path.write_text('{"query": "ok"}\n{not-json}\n', encoding="utf-8")
    output_path = tmp_path / "results.jsonl"

    monkeypatch.setattr(run_search_batch, "SearchService", _fake_service_class())

    code = run_search_batch.main(
        ["--input", str(input_path), "--output", str(output_path)]
    )

    assert code == 1
    assert not output_path.exists()


def test_missing_input_file_returns_nonzero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(run_search_batch, "SearchService", _fake_service_class())

    code = run_search_batch.main(
        [
            "--input",
            str(tmp_path / "missing.jsonl"),
            "--output",
            str(tmp_path / "results.jsonl"),
        ]
    )

    assert code == 1


def _fake_service_class(
    *,
    captured: list[dict[str, Any]] | None = None,
    fail_queries: set[str] | None = None,
):
    captured = captured if captured is not None else []
    fail_queries = fail_queries if fail_queries is not None else set()

    class FakeSearchService:
        def __init__(self, *args, **kwargs) -> None:
            self._max_workers = kwargs.get("max_workers")

        def run_search(
            self,
            query: str,
            top_k: int = 20,
            run_profile: str = "balanced",
            enable_refchain: bool = False,
            enable_query_evolution: bool = False,
            enable_synthesis: bool = True,
            current_year: int | None = None,
            sources_override: list[str] | None = None,
        ) -> SearchServiceOutput:
            captured.append(
                {
                    "max_workers": self._max_workers,
                    "query": query,
                    "top_k": top_k,
                    "run_profile": run_profile,
                    "enable_refchain": enable_refchain,
                    "enable_query_evolution": enable_query_evolution,
                    "enable_synthesis": enable_synthesis,
                    "current_year": current_year,
                    "sources_override": sources_override,
                }
            )
            if query in fail_queries:
                raise RuntimeError("forced failure")
            output = SearchServiceOutput(
                search_plan=_search_plan(query, top_k=top_k),
                raw_count=0,
                deduplicated_count=0,
                latency_seconds=0.01,
            )
            output.synthesis_output = synthesize_answer(output)
            return output

    return FakeSearchService


def _search_plan(query: str, top_k: int = 20) -> SearchPlan:
    return SearchPlan(
        query_analysis=QueryAnalysis(
            original_query=query,
            language="en",
            intent="paper_finding",
            domain="machine_learning",
            constraints=QueryConstraint(must_include_terms=["LLM"]),
        ),
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
        top_k=top_k,
        run_profile="balanced",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

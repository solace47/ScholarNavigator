from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_benchmark
from scholar_agent.agents.retriever import RetrievalOutput, SourceStats
from scholar_agent.core.diagnostics_schemas import ConnectorDiagnostics
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers
from scholar_agent.core.search_schemas import (
    EvidenceItem,
    JudgementResult,
    QueryAnalysis,
    QueryConstraint,
    RankedPaper,
    RerankScoreBreakdown,
    SearchPlan,
    SearchSubquery,
)
from scholar_agent.services.search_service import SearchServiceOutput


def test_limit_and_offset_follow_source_order(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path, count=4)
    service = FakeService()
    result = run_benchmark.run_benchmark(
        _options(tmp_path, dataset, run_id="subset", offset=1, limit=2),
        service=service,
    )

    assert [row["case_id"] for row in result.result_rows] == ["case-1", "case-2"]
    assert result.config["selection_order"] == "source_order"
    assert result.config["case_ids"] == ["case-1", "case-2"]


def test_runner_writes_required_outputs_and_uses_shared_metrics(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path, count=2)
    result = run_benchmark.run_benchmark(
        _options(tmp_path, dataset, run_id="outputs"),
        service=FakeService(),
    )
    run_dir = result.run_dir

    assert {path.name for path in run_dir.iterdir()} == {
        "config.json",
        "dataset_report.json",
        "results.jsonl",
        "metrics.json",
        "failures.jsonl",
        "summary.md",
    }
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["success_only_metrics"]["f1_at_k"]["5"] == pytest.approx(1 / 3)
    assert metrics["end_to_end_metrics"]["recall_at_k"]["20"] == 1.0
    assert metrics["success_only_metrics"]["mrr"] == 1.0
    assert metrics["benchmark_statistics"]["average_api_calls"] == 1.0
    assert metrics["benchmark_statistics"]["average_candidate_count"] == 1.0
    assert metrics["benchmark_statistics"]["average_final_result_count"] == 1.0
    assert "小规模 smoke" in (run_dir / "summary.md").read_text(encoding="utf-8")


def test_runner_uses_shared_result_selection_policy(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path, count=1)
    highly_only = run_benchmark.run_benchmark(
        _options(
            tmp_path,
            dataset,
            run_id="high-only",
            result_policy="highly_only",
        ),
        service=FakeService(category="partially_relevant"),
    )
    with_partial = run_benchmark.run_benchmark(
        _options(
            tmp_path,
            dataset,
            run_id="with-partial",
            result_policy="highly_and_partial",
        ),
        service=FakeService(category="partially_relevant"),
    )

    assert highly_only.metrics["end_to_end_metrics"]["recall_at_k"]["5"] == 0.0
    assert with_partial.metrics["end_to_end_metrics"]["recall_at_k"]["5"] == 1.0


def test_failed_case_is_zero_in_end_to_end_and_written_to_failures(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path, count=2)
    result = run_benchmark.run_benchmark(
        _options(tmp_path, dataset, run_id="failure"),
        service=FakeService(fail_queries={"query-1"}),
    )

    assert result.metrics["success_only_metrics"]["recall_at_k"]["5"] == 1.0
    assert result.metrics["end_to_end_metrics"]["recall_at_k"]["5"] == 0.5
    assert result.metrics["case_statistics"]["failed_case_rate"] == 0.5
    failures = _read_jsonl(result.run_dir / "failures.jsonl")
    assert failures[0]["case_id"] == "case-1"
    assert failures[0]["error_type"] == "RuntimeError"


def test_resume_skips_success_and_retries_failed_without_duplicates(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path, count=2)
    first_service = FakeService(fail_queries={"query-1"})
    options = _options(tmp_path, dataset, run_id="resume")
    run_benchmark.run_benchmark(options, service=first_service)

    retry_service = FakeService()
    resumed = run_benchmark.run_benchmark(
        options.model_copy(update={"resume": True}),
        service=retry_service,
    )

    assert retry_service.calls == ["query-1"]
    assert [row["case_id"] for row in resumed.result_rows] == ["case-0", "case-1"]
    assert all(row["status"] == "succeeded" for row in resumed.result_rows)
    assert len(_read_jsonl(resumed.run_dir / "results.jsonl")) == 2
    assert _read_jsonl(resumed.run_dir / "failures.jsonl") == []


def test_resume_rejects_incompatible_config(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path, count=1)
    options = _options(tmp_path, dataset, run_id="incompatible")
    run_benchmark.run_benchmark(options, service=FakeService())

    with pytest.raises(ValueError, match="resume config is incompatible"):
        run_benchmark.run_benchmark(
            options.model_copy(update={"resume": True, "top_k": 10}),
            service=FakeService(),
        )


def test_config_records_llm_prompt_budget_and_code_metadata(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path, count=1)
    result = run_benchmark.run_benchmark(
        _options(tmp_path, dataset, run_id="metadata"),
        service=FakeService(),
    )
    config = result.config

    assert config["llm"]["llm_enabled"] is False
    assert config["llm"]["requested"] is False
    assert {item["name"] for item in config["prompts"]} == {
        "query_understanding",
        "relevance_judgement",
    }
    assert all(len(item["hash"]) == 64 for item in config["prompts"])
    assert config["budgets"]["max_search_rounds"] == 2
    assert len(config["dataset_sha256"]) == 64
    assert len(config["runtime_code_hash"]) == 64
    assert "commit" in config["code"]
    assert "dirty" in config["code"]


def test_outputs_redact_api_keys(tmp_path: Path, monkeypatch) -> None:
    secret = "do-not-write-this-secret"
    monkeypatch.setenv("SCHOLAR_AGENT_LLM_API_KEY", secret)
    dataset = _dataset(tmp_path, count=1)
    result = run_benchmark.run_benchmark(
        _options(tmp_path, dataset, run_id="redacted"),
        service=FakeService(error_message=f"api_key={secret}"),
    )

    combined = "".join(
        path.read_text(encoding="utf-8")
        for path in result.run_dir.iterdir()
        if path.is_file()
    )
    assert secret not in combined
    assert "[REDACTED]" in combined


def test_search_service_receives_query_and_runtime_options_but_not_gold(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path, count=1)
    service = FakeService()
    run_benchmark.run_benchmark(
        _options(tmp_path, dataset, run_id="no-gold"),
        service=service,
    )

    assert service.calls == ["query-0"]
    assert service.kwargs[0]["sources_override"] == ["openalex"]
    assert "gold" not in service.kwargs[0]
    assert "gold_papers" not in service.kwargs[0]


def test_benchmark_gold_does_not_appear_in_production_search_strategy() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "scholar_agent"
    strategy_text = "\n".join(
        path.read_text(encoding="utf-8")
        for directory in ("agents", "services", "connectors")
        for path in (root / directory).rglob("*.py")
    )

    assert "Gold Fixture Paper 0" not in strategy_text
    assert "2401.00000" not in strategy_text
    assert "AutoScholarQuery" not in strategy_text


class FakeService:
    def __init__(
        self,
        *,
        category: str = "highly_relevant",
        fail_queries: set[str] | None = None,
        error_message: str | None = None,
    ) -> None:
        self.category = category
        self.fail_queries = fail_queries or set()
        self.error_message = error_message
        self.calls: list[str] = []
        self.kwargs: list[dict[str, Any]] = []

    def run_search(self, query: str, **kwargs: Any) -> SearchServiceOutput:
        self.calls.append(query)
        self.kwargs.append(kwargs)
        if query in self.fail_queries or self.error_message:
            raise RuntimeError(self.error_message or "offline fixture failure")
        index = int(query.rsplit("-", 1)[-1])
        return _output(query, f"2401.{index:05d}", category=self.category)


def _output(
    query: str,
    arxiv_id: str,
    *,
    category: str,
) -> SearchServiceOutput:
    paper = Paper(
        title=f"Gold Fixture Paper {int(arxiv_id[-1])}",
        authors=["Fixture"],
        year=2024,
        abstract="offline benchmark fixture",
        identifiers=PaperIdentifiers(arxiv_id=arxiv_id),
        sources=["openalex"],
    )
    analysis = QueryAnalysis(
        original_query=query,
        language="en",
        intent="survey",
        domain="machine_learning",
        constraints=QueryConstraint(),
    )
    plan = SearchPlan(
        query_analysis=analysis,
        subqueries=[
            SearchSubquery(
                query=query,
                source_hints=["openalex"],
                purpose="original_query",
            )
        ],
        selected_sources=["openalex"],
        top_k=20,
    )
    evidence = [EvidenceItem(source="title", text=paper.title, confidence=1.0)]
    judgement = JudgementResult(
        paper=paper,
        score=0.9,
        category=category,
        reasoning="offline fixture",
        evidence=evidence,
    )
    ranked = RankedPaper(
        rank=1,
        paper=paper,
        final_score=0.9,
        category=category,
        score_breakdown=RerankScoreBreakdown(
            relevance_score=0.9,
            authority_score=0.5,
            timeliness_score=0.5,
            metadata_score=1.0,
            final_score=0.9,
            relevance_weight=0.65,
            authority_weight=0.1,
            timeliness_weight=0.15,
            metadata_weight=0.1,
        ),
        ranking_reason="offline fixture",
        evidence=evidence,
    )
    stats = SourceStats(
        source="openalex",
        returned_count=1,
        diagnostics=ConnectorDiagnostics(request_count=1),
    )
    retrieval = RetrievalOutput(
        query=query,
        requested_sources=["openalex"],
        raw_count=1,
        deduplicated_count=1,
        papers=[paper],
        source_stats=[stats],
    )
    return SearchServiceOutput(
        search_plan=plan,
        retrieval_outputs=[retrieval],
        raw_count=1,
        deduplicated_count=1,
        judgements=[judgement],
        ranked_papers=[ranked],
        all_ranked_papers=[ranked],
        source_stats=[stats],
        search_diagnostics=stats.diagnostics,
    )


def _dataset(tmp_path: Path, *, count: int) -> Path:
    rows = [
        {
            "qid": f"case-{index}",
            "question": f"query-{index}",
            "answer": [f"Gold Fixture Paper {index}"],
            "answer_arxiv_id": [f"2401.{index:05d}"],
            "source_meta": {"published_time": "20240101"},
        }
        for index in range(count)
    ]
    path = tmp_path / "benchmark.jsonl"
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _options(
    tmp_path: Path,
    dataset: Path,
    *,
    run_id: str,
    offset: int = 0,
    limit: int | None = None,
    result_policy: str = "highly_and_partial",
) -> run_benchmark.BenchmarkRunOptions:
    return run_benchmark.BenchmarkRunOptions(
        dataset="auto_scholar_query",
        dataset_path=dataset,
        offset=offset,
        limit=limit,
        output_root=tmp_path / "runs",
        run_id=run_id,
        sources=["openalex"],
        result_policy=result_policy,
        max_workers=1,
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]

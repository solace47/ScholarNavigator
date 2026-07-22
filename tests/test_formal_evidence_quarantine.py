from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

import scripts.check_formal_evidence_quarantine as cli
from scholar_agent.evaluation.formal_evidence_quarantine import (
    EXIT_BLOCKED,
    EXIT_READY,
    EXIT_VIOLATION,
    QuarantineError,
    audit_contamination,
    canonical_json,
    consume_for_evaluation,
    current_readiness,
    load_protocol,
    quarantine_io_guard,
    stable_hash,
    synthetic_manifest,
    verify_boundaries,
    verify_intake_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "benchmark/formal_evidence_quarantine_v1_protocol.json"


@pytest.fixture(scope="module")
def protocol() -> dict[str, object]:
    return load_protocol(PROTOCOL_PATH)


def _fixture_protocol(protocol: dict[str, object], roots: list[str]) -> dict[str, object]:
    value = copy.deepcopy(protocol)
    value["forbidden_consumer_roots"] = roots
    return value


def test_current_boundaries_and_readiness_are_closed(protocol: dict[str, object]) -> None:
    first = verify_boundaries(ROOT, protocol)
    second = verify_boundaries(ROOT, protocol)
    assert first["status"] == "passed"
    assert first["violation_count"] == 0
    assert canonical_json(first) == canonical_json(second)
    readiness = current_readiness(ROOT, protocol)
    assert readiness["exit_code"] == EXIT_BLOCKED
    assert readiness["controls_ready"] is True
    assert readiness["formal_validation_complete"] is False


def test_direct_and_indirect_production_imports_are_rejected(
    tmp_path: Path, protocol: dict[str, object]
) -> None:
    retrieval = tmp_path / "src/scholar_agent/retrieval"
    core = tmp_path / "src/scholar_agent/core"
    retrieval.mkdir(parents=True)
    core.mkdir(parents=True)
    (retrieval / "direct.py").write_text(
        "from scholar_agent.evaluation.formal_evidence_quarantine import load_intake_manifest\n",
        encoding="utf-8",
    )
    (retrieval / "indirect.py").write_text(
        "from scholar_agent.core.formal_bridge import read\n", encoding="utf-8"
    )
    (core / "formal_bridge.py").write_text(
        "import scholar_agent.evaluation.formal_evidence_quarantine\n", encoding="utf-8"
    )
    report = verify_boundaries(
        tmp_path,
        _fixture_protocol(
            protocol, ["src/scholar_agent/retrieval", "src/scholar_agent/core"]
        ),
    )
    assert report["exit_code"] == EXIT_VIOLATION
    assert report["violation_count"] == 3
    assert any(row["path"].endswith("retrieval/indirect.py") for row in report["violations"])


def test_relative_import_and_frontend_asset_leakage_are_rejected(
    tmp_path: Path, protocol: dict[str, object]
) -> None:
    retrieval = tmp_path / "src/scholar_agent/retrieval"
    frontend = tmp_path / "frontend/src"
    retrieval.mkdir(parents=True)
    frontend.mkdir(parents=True)
    (retrieval / "relative.py").write_text(
        "from ..evaluation.formal_evidence_quarantine import consume_for_evaluation\n",
        encoding="utf-8",
    )
    (frontend / "search.ts").write_text(
        "export const forbidden = 'official_scorer_output';\n", encoding="utf-8"
    )
    report = verify_boundaries(
        tmp_path,
        _fixture_protocol(
            protocol, ["src/scholar_agent/retrieval", "frontend/src"]
        ),
    )
    assert report["exit_code"] == EXIT_VIOLATION
    assert {row["invariant"] for row in report["violations"]} == {
        "production_imports_formal_evidence",
        "formal_evidence_token_in_runtime_asset",
    }


@pytest.mark.parametrize(
    "source",
    [
        "open('formal_evidence/labels.json').read()\n",
        "PROMPT_CONFIG['qrels'] = 'inject'\n",
        "CACHE['official_scorer_output'] = 'reuse'\n",
        "EVENT = {'gold': 'leak'}\n",
    ],
)
def test_file_prompt_and_configuration_leakage_are_rejected(
    tmp_path: Path, protocol: dict[str, object], source: str
) -> None:
    target = tmp_path / "src/scholar_agent/prompts"
    target.mkdir(parents=True)
    (target / "unsafe.py").write_text(source, encoding="utf-8")
    report = verify_boundaries(
        tmp_path, _fixture_protocol(protocol, ["src/scholar_agent/prompts"])
    )
    assert report["exit_code"] == EXIT_VIOLATION
    assert report["violation_count"] >= 1


@pytest.mark.parametrize("evidence_type", ["human_annotation_labels", "official_scorer_output"])
def test_legal_evaluation_only_consumption_and_runtime_copy_denial(
    tmp_path: Path, protocol: dict[str, object], evidence_type: str
) -> None:
    manifest = synthetic_manifest(tmp_path, protocol, evidence_type=evidence_type)
    expected = (tmp_path / manifest.artifact.path).read_bytes()
    actual = consume_for_evaluation(
        manifest,
        evidence_root=tmp_path,
        consumer="scholar_agent.evaluation.offline_report",
        purpose="reporting",
        protocol=protocol,
    )
    assert actual == expected
    with pytest.raises(QuarantineError, match="consumer_not_allowed"):
        consume_for_evaluation(
            manifest,
            evidence_root=tmp_path,
            consumer="scholar_agent.services.search_service",
            purpose="evaluation",
            protocol=protocol,
        )
    artifact = tmp_path / manifest.artifact.path
    with quarantine_io_guard(artifact=artifact):
        with pytest.raises(QuarantineError, match="copy_denied"):
            shutil.copy2(artifact, tmp_path / "copied.json")


def test_artifact_tamper_and_cross_protocol_mix_are_rejected(
    tmp_path: Path, protocol: dict[str, object]
) -> None:
    manifest = synthetic_manifest(tmp_path, protocol)
    (tmp_path / manifest.artifact.path).write_text("tampered", encoding="utf-8")
    with pytest.raises(QuarantineError, match="hash_mismatch"):
        verify_intake_manifest(manifest, evidence_root=tmp_path, protocol=protocol)

    clean_root = tmp_path / "clean"
    manifest = synthetic_manifest(clean_root, protocol)
    changed = copy.deepcopy(protocol)
    changed["allowed_consumer_prefixes"] = ["scholar_agent.evaluation.other."]
    with pytest.raises(QuarantineError, match="consumer_drift"):
        verify_intake_manifest(manifest, evidence_root=clean_root, protocol=changed)


def test_unlocked_lifecycle_cannot_be_consumed(
    tmp_path: Path, protocol: dict[str, object]
) -> None:
    manifest = synthetic_manifest(tmp_path, protocol)
    payload = manifest.model_dump(mode="json")
    payload["lifecycle_state"] = "received"
    payload.pop("manifest_sha256")
    payload["manifest_sha256"] = stable_hash(payload)
    path = tmp_path / "received.json"
    path.write_bytes(canonical_json(payload))
    from scholar_agent.evaluation.formal_evidence_quarantine import load_intake_manifest

    received = load_intake_manifest(path)
    with pytest.raises(QuarantineError, match="lifecycle_not_consumable"):
        consume_for_evaluation(
            received,
            evidence_root=tmp_path,
            consumer="scholar_agent.evaluation.offline_report",
            purpose="reporting",
            protocol=protocol,
        )


@pytest.mark.parametrize(
    ("path", "component"),
    [
        ("src/scholar_agent/agents/reranker.py", "ranking"),
        ("src/scholar_agent/prompts/manifest.json", "prompt"),
        ("src/scholar_agent/core/config.py", "default_policy"),
        ("src/scholar_agent/retrieval/query_adapter.py", "query_planning"),
    ],
)
def test_posthoc_changes_make_formal_claim_stale(
    tmp_path: Path, protocol: dict[str, object], path: str, component: str
) -> None:
    manifest = synthetic_manifest(tmp_path / component, protocol)
    report = audit_contamination(manifest, [path], protocol)
    assert report["status"] == "stale_for_claim"
    assert report["exit_code"] == EXIT_VIOLATION
    assert component in report["affected_components"]


def test_reporting_only_change_does_not_reselect_strategy(
    tmp_path: Path, protocol: dict[str, object]
) -> None:
    manifest = synthetic_manifest(tmp_path, protocol)
    report = audit_contamination(manifest, ["docs/formal-evaluation-report.md"], protocol)
    assert report["status"] == "clean"
    assert report["minimum_rerun_components"] == []


def test_synthetic_chronology_cannot_claim_real_evidence(
    tmp_path: Path, protocol: dict[str, object]
) -> None:
    manifest = synthetic_manifest(tmp_path, protocol)
    payload = manifest.model_dump(mode="json")
    payload["synthetic_only"] = False
    payload.pop("manifest_sha256")
    payload["manifest_sha256"] = stable_hash(payload)
    path = tmp_path / "spoofed.json"
    path.write_bytes(canonical_json(payload))
    from scholar_agent.evaluation.formal_evidence_quarantine import load_intake_manifest

    spoofed = load_intake_manifest(path)
    with pytest.raises(QuarantineError, match="synthetic_chronology"):
        verify_intake_manifest(spoofed, evidence_root=tmp_path, protocol=protocol)


def test_cross_commit_chronology_is_rejected(
    tmp_path: Path, protocol: dict[str, object]
) -> None:
    manifest = synthetic_manifest(tmp_path, protocol)
    payload = manifest.model_dump(mode="json")
    payload["synthetic_only"] = False
    payload["chronology"] = {
        "preregistration_commit": "f" * 40,
        "execution_commit": "e1e2545cab9d6f2ecf0e95be157d4c1e71376ec8",
        "intake_commit": protocol["source_commit"],
        "report_code_commit": "6e3b5518b6b58c8384072a4e5a99ac1b6e649712",
        "proof": "git_ancestry",
    }
    payload.pop("manifest_sha256")
    payload["manifest_sha256"] = stable_hash(payload)
    path = tmp_path / "cross-commit.json"
    path.write_bytes(canonical_json(payload))
    from scholar_agent.evaluation.formal_evidence_quarantine import load_intake_manifest

    mixed = load_intake_manifest(path)
    with pytest.raises(QuarantineError, match="chronology_invalid"):
        verify_intake_manifest(
            mixed, evidence_root=tmp_path, protocol=protocol, repository_root=ROOT
        )


def test_cli_readiness_and_determinism(
    protocol: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["audit-readiness"]) == EXIT_BLOCKED
    first = capsys.readouterr().out
    assert cli.main(["audit-readiness"]) == EXIT_BLOCKED
    second = capsys.readouterr().out
    assert first == second
    assert '"formal_validation_complete": false' in first


def test_cli_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 4
    assert '"status": "usage_error"' in capsys.readouterr().out

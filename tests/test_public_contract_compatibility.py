from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scholar_agent.evaluation.public_contract_compatibility import (
    ContractError,
    build_snapshot,
    canonical_json,
    compare_snapshots,
    load_protocol,
    stable_hash,
    validate_snapshot,
    verify_current,
)


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "benchmark/public_contract_compatibility_v1_protocol.json"
BASELINE_PATH = ROOT / "benchmark/public_contract_compatibility_v1_baseline.json"
CLI = ROOT / "scripts/check_public_contract_compatibility.py"


def _rehash(value: dict[str, object]) -> dict[str, object]:
    value["content_sha256"] = stable_hash({key: child for key, child in value.items() if key != "content_sha256"})
    return value


def _snapshot() -> dict[str, object]:
    return build_snapshot(load_protocol(PROTOCOL_PATH), repository_root=ROOT)


def test_current_snapshot_is_deterministic_and_matches_baseline() -> None:
    first = _snapshot()
    second = _snapshot()
    assert canonical_json(first) == canonical_json(second)
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert verify_current(load_protocol(PROTOCOL_PATH), baseline, repository_root=ROOT)["status"] == "contracts_compatible"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda value: value["openapi"]["paths"].pop(next(iter(value["openapi"]["paths"]))), "field_removed"),
        (lambda value: value["openapi"].__setitem__("paths", []), "type_changed"),
        (lambda value: value["cli"]["run_provenance"].__setitem__("exit_codes", [0, 2, 3]), "ordered_or_membership_changed"),
        (lambda value: value["frontend"]["declarations"].pop("HealthResponse"), "field_removed"),
    ],
)
def test_breaking_contract_changes_are_rejected(mutate, reason: str) -> None:
    baseline = _snapshot()
    changed = copy.deepcopy(baseline)
    mutate(changed)
    _rehash(changed)
    report = compare_snapshots(baseline, changed)
    assert report["classification"] == "breaking"
    assert reason in {row["reason"] for row in report["changes"]}


def test_enum_narrowing_is_breaking() -> None:
    baseline = _snapshot()
    baseline["openapi"]["synthetic_enum_contract"] = {"enum": ["alpha", "beta"]}
    _rehash(baseline)
    changed = copy.deepcopy(baseline)
    schema = changed["openapi"]["synthetic_enum_contract"]
    schema["enum"] = schema["enum"][:-1]
    _rehash(changed)
    report = compare_snapshots(baseline, changed)
    assert report["classification"] == "breaking"
    assert any(row["reason"] == "enum_narrowed" for row in report["changes"])


def test_optional_addition_requires_explicit_review_policy() -> None:
    baseline = _snapshot()
    changed = copy.deepcopy(baseline)
    changed["openapi"]["paths"]["/synthetic-optional"] = {"get": {}}
    _rehash(changed)
    assert compare_snapshots(baseline, changed)["classification"] == "breaking"
    assert compare_snapshots(baseline, changed, extension_policy="allow_optional")["classification"] == "additive_review_required"


def test_tampered_baseline_and_same_version_drift_fail_closed() -> None:
    baseline = _snapshot()
    baseline["cli"]["run_provenance"]["commands"].append("silent-drift")
    with pytest.raises(ContractError, match="contract_snapshot_hash_mismatch"):
        validate_snapshot(baseline)


def test_cli_reports_stable_exit_codes_without_traceback(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{}", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(CLI), "verify-current", "--baseline", str(broken)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert result.stderr == ""
    assert json.loads(result.stdout)["status"] == "breaking_or_versioning_violation"


def test_snapshot_round_trip_and_baseline_bytes_are_canonical(tmp_path: Path) -> None:
    output = tmp_path / "snapshot.json"
    first = subprocess.run(
        [sys.executable, str(CLI), "snapshot", "--output", str(output)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert first.returncode == 0
    assert first.stdout == output.read_bytes()
    validate_snapshot(json.loads(first.stdout))
    second = subprocess.run(
        [sys.executable, str(CLI), "snapshot"], cwd=ROOT, capture_output=True, check=False
    )
    assert second.returncode == 0
    assert first.stdout == second.stdout


def test_snapshot_cannot_silently_replace_existing_baseline(tmp_path: Path) -> None:
    output = tmp_path / "existing.json"
    output.write_text("{}\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(CLI), "snapshot", "--output", str(output)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert result.stderr == ""
    assert output.read_text(encoding="utf-8") == "{}\n"

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_standalone_auditor_bundle.py"
SPEC = importlib.util.spec_from_file_location("standalone_bundle_cli", SCRIPT)
assert SPEC and SPEC.loader
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


def _build(tmp_path: Path, name: str = "bundle.zip") -> Path:
    target = tmp_path / name
    report = bundle.build_archive(
        ROOT / "benchmark" / "standalone_auditor_bundle_v1_contract.json",
        target,
        ROOT,
    )
    assert report["status"] == "verified_with_declared_blockers"
    return target


def _rewrite(source: Path, target: Path, transform) -> None:
    with zipfile.ZipFile(source) as archive:
        rows = [(info, archive.read(info)) for info in archive.infolist()]
    rows = transform(rows)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for old, content in rows:
            info = zipfile.ZipInfo(old.filename, old.date_time)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = old.external_attr
            info.create_system = old.create_system
            archive.writestr(info, content)


def test_two_builds_and_isolated_standard_library_verify_are_byte_stable(tmp_path: Path) -> None:
    first = _build(tmp_path, "one.zip")
    second = _build(tmp_path, "two.zip")
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        verifier = tmp_path / "foreign" / "verify.py"
        verifier.parent.mkdir()
        verifier.write_bytes(archive.read("verify.py"))
    reports = []
    for suffix in ("a", "b"):
        cwd = tmp_path / suffix / "cwd"
        home = tmp_path / suffix / "home"
        scratch = tmp_path / suffix / "tmp"
        cwd.mkdir(parents=True); home.mkdir(); scratch.mkdir()
        completed = subprocess.run(
            [sys.executable, "-I", "-S", str(verifier), "verify", str(first)],
            cwd=cwd,
            env={"HOME": str(home), "TMPDIR": str(scratch), "PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": "random" if suffix == "a" else "7"},
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0
        assert completed.stderr == b""
        reports.append(completed.stdout)
    assert reports[0] == reports[1]
    assert json.loads(reports[0])["formal_validation_complete"] is False


@pytest.mark.parametrize("kind", ["tamper", "missing", "extra"])
def test_inventory_tamper_missing_and_extra_are_rejected(tmp_path: Path, kind: str) -> None:
    source = _build(tmp_path)
    target = tmp_path / f"{kind}.zip"
    def change(rows):
        if kind == "tamper":
            return [(info, content + b" ") if info.filename == "claims.json" else (info, content) for info, content in rows]
        if kind == "missing":
            return [(info, content) for info, content in rows if info.filename != "claims.json"]
        info = zipfile.ZipInfo("extra.json", (1980, 1, 1, 0, 0, 0)); info.external_attr = 0o100644 << 16; info.create_system = 3
        return [*rows, (info, b"{}\n")]
    _rewrite(source, target, change)
    with pytest.raises(bundle.AuditError):
        bundle.verify_archive(target)


def _mutate_json(source: Path, target: Path, member: str, mutate) -> None:
    def change(rows):
        result = []
        for info, content in rows:
            if info.filename == member:
                value = json.loads(content)
                mutate(value)
                content = bundle.canonical_bytes(value)
            result.append((info, content))
        return result
    _rewrite(source, target, change)


@pytest.mark.parametrize(
    ("member", "mutation"),
    [
        ("blockers.json", lambda value: value["blockers"].pop()),
        ("readiness.json", lambda value: value.__setitem__("formal_validation_complete", True)),
        ("freshness.json", lambda value: value.__setitem__("stale_count", 1)),
        ("policy.json", lambda value: value.__setitem__("deterministic_tiebreak_v2_default_enabled", True)),
        ("evidence_index.json", lambda value: value["evidence"][0].__setitem__("verification_scope", "externally_verified")),
        ("protocol_dependencies.json", lambda value: value["implementation_commit_ancestry"].__setitem__("head_commit", "0" * 40)),
    ],
)
def test_claim_blocker_freshness_policy_and_commit_attacks_fail(
    tmp_path: Path, member: str, mutation
) -> None:
    source = _build(tmp_path)
    target = tmp_path / "attack.zip"
    _mutate_json(source, target, member, mutation)
    # Even if an attacker changes data, the unchanged manifest catches it first.
    with pytest.raises(bundle.AuditError):
        bundle.verify_archive(target)


def test_duplicate_key_path_traversal_and_link_are_rejected(tmp_path: Path) -> None:
    source = _build(tmp_path)
    duplicate = tmp_path / "duplicate-key.zip"
    def duplicate_json(rows):
        return [(info, b'{"schema_version":"1","schema_version":"1"}\n') if info.filename == "claims.json" else (info, content) for info, content in rows]
    _rewrite(source, duplicate, duplicate_json)
    with pytest.raises(bundle.AuditError): bundle.verify_archive(duplicate)

    for name, mode in (("../escape", 0o100644), ("C:/absolute", 0o100644), ("link", 0o120777)):
        target = tmp_path / ("path.zip" if ".." in name else "link.zip")
        def add(rows, name=name, mode=mode):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)); info.external_attr = mode << 16; info.create_system = 3
            return [*rows, (info, b"x")]
        _rewrite(source, target, add)
        with pytest.raises(bundle.AuditError): bundle.verify_archive(target)


def test_malformed_utf8_nonfinite_and_resource_limits_are_rejected(tmp_path: Path) -> None:
    source = _build(tmp_path)
    for suffix, content in (("utf8", b"\xff"), ("nan", b'{"value":NaN}\n')):
        target = tmp_path / f"{suffix}.zip"
        def change(rows, content=content):
            return [(info, content) if info.filename == "claims.json" else (info, value) for info, value in rows]
        _rewrite(source, target, change)
        with pytest.raises(bundle.AuditError): bundle.verify_archive(target)

    target = tmp_path / "oversized.zip"
    def oversized(rows):
        info = zipfile.ZipInfo("large.bin", (1980, 1, 1, 0, 0, 0)); info.external_attr = 0o100644 << 16; info.create_system = 3
        return [*rows, (info, b"x" * (1024 * 1024 + 1))]
    _rewrite(source, target, oversized)
    with pytest.raises(bundle.AuditError, match="resource_limit"):
        bundle.verify_archive(target)


def test_claim_reference_and_overclaim_are_rejected_after_resealing(tmp_path: Path) -> None:
    source = _build(tmp_path)
    with zipfile.ZipFile(source) as archive:
        rows = {info.filename: archive.read(info) for info in archive.infolist()}
    claims = json.loads(rows["claims.json"]); claims["claims"][0]["evidence_ids"] = ["unknown"]
    rows["claims.json"] = bundle.canonical_bytes(claims)
    manifest = json.loads(rows["manifest.json"])
    for item in manifest["files"]:
        if item["path"] == "claims.json": item["size"] = len(rows["claims.json"]); item["sha256"] = bundle.digest(rows["claims.json"])
    manifest["manifest_self_sha256"] = bundle._self_hash(manifest)
    rows["manifest.json"] = bundle.canonical_bytes(manifest)
    target = tmp_path / "resealed.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(rows.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)); info.external_attr = 0o100644 << 16; info.create_system = 3
            archive.writestr(info, content)
    with pytest.raises(bundle.AuditError, match="claim_evidence_reference_missing"):
        bundle.verify_archive(target)

#!/usr/bin/env python3
"""Build and independently verify standalone_auditor_bundle_v1.

The ``verify`` and ``compare`` paths use only the Python standard library and
never import project modules, execute archive members, use the network, launch
subprocesses, or read files other than the explicitly supplied archives.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import stat
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath


PROTOCOL = "standalone_auditor_bundle_v1"
SCHEMA = "1"
EXIT_PASSED = 0
EXIT_VIOLATION = 2
EXIT_NOT_READY = 3
EXIT_USAGE = 4
MANIFEST = "manifest.json"
VERIFIER = "verify.py"
REQUIRED_BLOCKERS = {
    "full1000_incomplete",
    "human_precision_missing",
    "official_scorer_schema_missing",
}
CANONICAL_MEMBERS = {
    "blockers.json",
    "claims.json",
    "evidence_index.json",
    "freshness.json",
    "policy.json",
    "protocol_dependencies.json",
    "readiness.json",
    VERIFIER,
}


class AuditError(RuntimeError):
    pass


class NotReady(AuditError):
    pass


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def strict_json(value: bytes) -> object:
    try:
        text = value.decode("utf-8")
        def pairs(rows: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, child in rows:
                if key in result:
                    raise AuditError("duplicate_json_key")
                result[key] = child
            return result
        result = json.loads(
            text,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(AuditError("nonfinite_json_number")),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError("invalid_utf8_or_json") from exc
    if canonical_bytes(result) != value:
        raise AuditError("noncanonical_json")
    return result


def _safe_name(name: str) -> str:
    if (
        not name
        or "\\" in name
        or name.startswith("/")
        or posixpath.isabs(name)
        or ":" in PurePosixPath(name).parts[0]
    ):
        raise AuditError("unsafe_archive_path")
    normalized = unicodedata.normalize("NFC", name)
    parts = PurePosixPath(normalized).parts
    if normalized != name or not parts or any(part in {"", ".", ".."} for part in parts):
        raise AuditError("unsafe_or_normalization_conflict_path")
    return normalized


def _read_archive(path: Path, *, max_files: int = 32, max_file: int = 1048576, max_total: int = 5242880) -> dict[str, bytes]:
    try:
        if not path.is_file():
            raise AuditError("archive_missing")
        rows: dict[str, bytes] = {}
        normalized: set[str] = set()
        total = 0
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > max_files:
                raise AuditError("archive_file_limit_exceeded")
            for info in infos:
                name = _safe_name(info.filename)
                if name in rows or name.casefold() in normalized:
                    raise AuditError("duplicate_or_normalization_conflict_member")
                normalized.add(name.casefold())
                mode = info.external_attr >> 16
                if info.is_dir() or stat.S_ISLNK(mode) or (mode and not stat.S_ISREG(mode)):
                    raise AuditError("links_or_nonfiles_forbidden")
                if info.file_size > max_file or info.compress_size == 0 < info.file_size:
                    raise AuditError("archive_resource_limit_exceeded")
                if info.compress_size and info.file_size / info.compress_size > 20:
                    raise AuditError("compression_ratio_exceeded")
                total += info.file_size
                if total > max_total:
                    raise AuditError("archive_total_limit_exceeded")
                rows[name] = archive.read(info)
        return rows
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, AuditError):
            raise
        raise AuditError("archive_unreadable") from exc


def _self_hash(manifest: dict[str, object]) -> str:
    copy = dict(manifest)
    copy["manifest_self_sha256"] = "0" * 64
    return digest(canonical_bytes(copy))


def verify_archive(path: Path) -> dict[str, object]:
    rows = _read_archive(path)
    if MANIFEST not in rows:
        raise AuditError("manifest_missing")
    manifest = strict_json(rows[MANIFEST])
    if not isinstance(manifest, dict):
        raise AuditError("manifest_not_object")
    if manifest.get("protocol") != PROTOCOL or manifest.get("schema_version") != SCHEMA:
        raise AuditError("protocol_or_schema_mismatch")
    inventory = manifest.get("files")
    if not isinstance(inventory, list):
        raise AuditError("manifest_inventory_invalid")
    expected = {MANIFEST}
    for item in inventory:
        if not isinstance(item, dict) or set(item) != {"path", "role", "sha256", "size"}:
            raise AuditError("manifest_file_entry_invalid")
        name = _safe_name(str(item["path"])); expected.add(name)
        if name not in rows or len(rows[name]) != item["size"] or digest(rows[name]) != item["sha256"]:
            raise AuditError("member_hash_or_size_mismatch")
    if set(rows) != expected or expected - {MANIFEST} != CANONICAL_MEMBERS:
        raise AuditError("archive_inventory_not_closed")
    if manifest.get("manifest_self_sha256") != _self_hash(manifest):
        raise AuditError("manifest_self_hash_mismatch")
    values: dict[str, object] = {}
    for name in sorted(CANONICAL_MEMBERS - {VERIFIER}):
        values[name] = strict_json(rows[name])
    claims = values["claims.json"]; evidence = values["evidence_index.json"]
    blockers = values["blockers.json"]; readiness = values["readiness.json"]
    freshness = values["freshness.json"]; policy = values["policy.json"]
    dependencies = values["protocol_dependencies.json"]
    if not all(isinstance(value, dict) for value in (claims, evidence, blockers, readiness, freshness, policy, dependencies)):
        raise AuditError("bundle_document_not_object")
    evidence_ids = {row.get("evidence_id") for row in evidence.get("evidence", []) if isinstance(row, dict)}
    if any(ref not in evidence_ids for row in claims.get("claims", []) for ref in row.get("evidence_ids", [])):
        raise AuditError("claim_evidence_reference_missing")
    if any(row.get("verification_scope") != "externally_unverifiable_reference" for row in evidence.get("evidence", [])):
        raise AuditError("internal_evidence_overclaimed")
    blocker_ids = {row.get("blocker_id") for row in blockers.get("blockers", []) if isinstance(row, dict)}
    if blocker_ids != REQUIRED_BLOCKERS or blockers.get("blocker_count") != 3:
        raise AuditError("formal_blocker_set_drift")
    if readiness.get("formal_validation_complete") is not False or readiness.get("status") != "verified_with_declared_blockers":
        raise AuditError("formal_status_overclaimed")
    if freshness.get("stale_count") != 0 or freshness.get("status") != "fresh_with_declared_blockers":
        raise AuditError("stale_evidence_present")
    if policy != {"default_strategy": "current_rules", "deterministic_tiebreak_v2_default_enabled": False}:
        raise AuditError("default_policy_drift")
    ancestry = dependencies.get("implementation_commit_ancestry")
    if not isinstance(ancestry, dict) or ancestry.get("source_is_ancestor") is not True:
        raise AuditError("implementation_commit_ancestry_invalid")
    if ancestry.get("source_commit") != manifest.get("source_commit") or ancestry.get("head_commit") != manifest.get("generated_from_commit"):
        raise AuditError("implementation_commit_identity_mismatch")
    if digest(rows[VERIFIER]) != manifest.get("verifier_sha256"):
        raise AuditError("verifier_hash_mismatch")
    return {
        "archive_sha256": digest(path.read_bytes()),
        "blocker_count": 3,
        "claim_count": len(claims.get("claims", [])),
        "evidence_reference_count": len(evidence.get("evidence", [])),
        "execution": {"archive_code_execution_count": 0, "external_file_read_count": 0, "network_request_count": 0, "subprocess_count": 0},
        "exit_code": EXIT_PASSED,
        "formal_validation_complete": False,
        "protocol": PROTOCOL,
        "schema_version": SCHEMA,
        "status": "verified_with_declared_blockers",
    }


def _load(path: Path) -> dict[str, object]:
    value = strict_json(path.read_bytes())
    if not isinstance(value, dict): raise AuditError("source_not_object")
    return value


def _repo_path(root: Path, value: str) -> Path:
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", "..", ".env", "third_party"} for part in pure.parts):
        raise AuditError("unsafe_source_path")
    path = (root / Path(*pure.parts)).resolve()
    try: path.relative_to(root.resolve())
    except ValueError as exc: raise AuditError("source_path_escape") from exc
    return path


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False, env={"PATH": os.environ.get("PATH", ""), "LANG": "C", "LC_ALL": "C"})
    if completed.returncode: raise AuditError("git_identity_unavailable")
    return completed.stdout.strip()


def build_archive(contract_path: Path, output: Path, root: Path) -> dict[str, object]:
    contract = _load(contract_path)
    if contract.get("protocol") != PROTOCOL or contract.get("schema_version") != SCHEMA:
        raise AuditError("contract_version_invalid")
    source_commit = str(contract.get("source_commit")); head = _git(root, "rev-parse", "HEAD")
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", source_commit, head], cwd=root, check=False).returncode == 0
    if not ancestor: raise AuditError("source_commit_not_ancestor")
    sources = {key: _load(_repo_path(root, value)) for key, value in contract["sources"].items()}
    source_hashes = {key: digest(_repo_path(root, value).read_bytes()) for key, value in contract["sources"].items()}
    missing = sources["missing_inputs"]
    if {row.get("blocker_id") for row in missing.get("blockers", [])} != REQUIRED_BLOCKERS:
        raise AuditError("required_blocker_set_drift")
    clearance = sources["clearance"]
    if set(clearance.get("global_prerequisites", {}).get("passed", [])) < {"current_rules_default", "default_tiebreak_unchanged", "fresh"}:
        raise AuditError("policy_prerequisite_missing")
    claims_source = sources["claims"]
    evidence_source = sources["evidence_index"]
    payloads: dict[str, bytes] = {}
    payloads["claims.json"] = canonical_bytes({"claims": [{key: row[key] for key in ("boundary", "claim_id", "evidence_ids", "scope", "status")} for row in claims_source["claims"]], "protocol": PROTOCOL, "schema_version": SCHEMA})
    payloads["evidence_index.json"] = canonical_bytes({"evidence": [{"evidence_id": row["evidence_id"], "protocol": row["protocol"], "role": row["role"], "sha256": row["sha256"], "size": row["size"], "verification_scope": "externally_unverifiable_reference"} for row in evidence_source["evidence"]], "protocol": PROTOCOL, "schema_version": SCHEMA})
    payloads["blockers.json"] = canonical_bytes({"blocker_count": 3, "blockers": missing["blockers"], "protocol": PROTOCOL, "schema_version": SCHEMA})
    freshness = sources["freshness"]
    payloads["freshness.json"] = canonical_bytes({"baseline_head": freshness["baseline_head"], "protocol": "validation_evidence_freshness_v1", "stale_count": freshness["state_counts"].get("stale", 0), "status": freshness["status"]})
    payloads["policy.json"] = canonical_bytes(contract["policy"])
    payloads["readiness.json"] = canonical_bytes({"formal_validation_complete": False, "published_readiness_status": sources["readiness"]["status"], "status": "verified_with_declared_blockers"})
    payloads["protocol_dependencies.json"] = canonical_bytes({"implementation_commit_ancestry": {"head_commit": head, "source_commit": source_commit, "source_is_ancestor": ancestor}, "source_files": [{"role": key, "sha256": source_hashes[key]} for key in sorted(source_hashes)]})
    verifier_bytes = Path(__file__).read_bytes()
    payloads[VERIFIER] = verifier_bytes
    inventory = [{"path": name, "role": "standalone_verifier" if name == VERIFIER else "auditable_data", "sha256": digest(content), "size": len(content)} for name, content in sorted(payloads.items())]
    manifest: dict[str, object] = {"files": inventory, "formal_validation_complete": False, "generated_from_commit": head, "manifest_self_sha256": "0" * 64, "protocol": PROTOCOL, "schema_version": SCHEMA, "source_commit": source_commit, "status": "verified_with_declared_blockers", "verifier_sha256": digest(verifier_bytes)}
    manifest["manifest_self_sha256"] = _self_hash(manifest)
    payloads[MANIFEST] = canonical_bytes(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name("." + output.name + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(payloads.items()):
            info = zipfile.ZipInfo(name, tuple(contract["archive"]["fixed_timestamp"])); info.compress_type = zipfile.ZIP_STORED; info.external_attr = 0o100644 << 16; info.create_system = 3
            archive.writestr(info, content)
    os.replace(temporary, output)
    return verify_archive(output)


def _emit(value: dict[str, object]) -> None:
    sys.stdout.buffer.write(canonical_bytes(value))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build"); build.add_argument("--contract", default="benchmark/standalone_auditor_bundle_v1_contract.json"); build.add_argument("--output", required=True); build.add_argument("--repository-root", default=".")
    verify = commands.add_parser("verify"); verify.add_argument("archive")
    compare = commands.add_parser("compare"); compare.add_argument("first"); compare.add_argument("second")
    audit = commands.add_parser("audit-readiness"); audit.add_argument("--contract", default="benchmark/standalone_auditor_bundle_v1_contract.json"); audit.add_argument("--repository-root", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "build": report = build_archive(Path(args.contract), Path(args.output), Path(args.repository_root).resolve())
        elif args.command == "verify": report = verify_archive(Path(args.archive))
        elif args.command == "compare":
            first, second = Path(args.first), Path(args.second); one, two = verify_archive(first), verify_archive(second)
            if first.read_bytes() != second.read_bytes() or canonical_bytes(one) != canonical_bytes(two): raise AuditError("archive_or_report_byte_mismatch")
            report = {**one, "comparison": "byte_identical"}
        else:
            contract = _load(Path(args.repository_root).resolve() / args.contract)
            if contract.get("status") != "verified_with_declared_blockers": raise NotReady("shareable_evidence_not_ready")
            report = {"blocker_count": 3, "exit_code": 0, "formal_validation_complete": False, "protocol": PROTOCOL, "schema_version": SCHEMA, "status": "verified_with_declared_blockers"}
        _emit(report); return EXIT_PASSED
    except NotReady as exc:
        _emit({"error_code": str(exc), "exit_code": EXIT_NOT_READY, "protocol": PROTOCOL, "schema_version": SCHEMA, "status": "not_ready_missing_shareable_evidence"}); return EXIT_NOT_READY
    except (AuditError, OSError, KeyError, TypeError, ValueError) as exc:
        _emit({"error_code": str(exc) if isinstance(exc, AuditError) else "controlled_input_failure", "exit_code": EXIT_VIOLATION, "protocol": PROTOCOL, "schema_version": SCHEMA, "status": "integrity_or_claim_violation"}); return EXIT_VIOLATION
    except SystemExit as exc:
        if exc.code == 0: raise
        _emit({"exit_code": EXIT_USAGE, "protocol": PROTOCOL, "schema_version": SCHEMA, "status": "usage_error"}); return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())

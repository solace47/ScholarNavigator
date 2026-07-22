"""Deterministic public API/CLI/artifact compatibility governance."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import FastAPI

from scholar_agent.app.api.routes import router
from scholar_agent.evaluation.run_provenance import RunManifestV1


PROTOCOL = "public_contract_compatibility_v1"
SCHEMA_VERSION = "1"
EXIT_COMPATIBLE = 0
EXIT_BREAKING = 2
EXIT_NOT_READY = 3
EXIT_USAGE = 4
ROOT = Path(__file__).resolve().parents[3]
_DROP_KEYS = {"description", "examples", "example", "externalDocs"}
_DECLARATION = re.compile(r"export\s+(?:interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)")


class ContractError(RuntimeError):
    pass


class ContractNotReady(ContractError):
    pass


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe(root: Path, value: str) -> Path:
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", "..", ".env", "third_party"} for part in pure.parts):
        raise ContractError("unsafe_contract_path")
    path = (root / Path(*pure.parts)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ContractError("contract_path_escape") from exc
    return path


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractNotReady("contract_baseline_missing") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("contract_json_invalid") from exc
    if not isinstance(value, dict):
        raise ContractError("contract_root_not_object")
    return value


def load_protocol(path: Path) -> dict[str, Any]:
    value = load_json(path)
    required = {"artifact_contracts", "cli_contracts", "documentation_files", "execution", "formal_validation_complete", "frontend_types", "protocol", "schema_version", "source_commit"}
    if set(value) != required or value.get("protocol") != PROTOCOL or value.get("schema_version") != SCHEMA_VERSION or value.get("formal_validation_complete") is not False:
        raise ContractError("protocol_schema_invalid")
    if value.get("execution") != {"gold_or_qrels_loaded": False, "llm_request_count": 0, "network_request_count": 0, "quality_metric_count": 0, "snapshot_write_count": 0}:
        raise ContractError("offline_boundary_drift")
    return value


def _normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _normalize(child) for key, child in sorted(value.items()) if key not in _DROP_KEYS}
    if isinstance(value, list):
        normalized = [_normalize(child) for child in value]
        if all(isinstance(child, str) for child in normalized):
            return sorted(set(normalized))
        return normalized
    if isinstance(value, tuple):
        return _normalize(list(value))
    return value


def _openapi_contract() -> dict[str, Any]:
    app = FastAPI(title="contract-snapshot", version="0")
    app.include_router(router)
    schema = _normalize(app.openapi())
    return {"components": schema.get("components", {}), "paths": schema.get("paths", {})}


def _extract_ts_declarations(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    declarations: dict[str, str] = {}
    for match in _DECLARATION.finditer(text):
        name = match.group(1)
        start = match.start()
        brace = text.find("{", match.end())
        equals = text.find("=", match.end())
        if brace != -1 and (equals == -1 or brace < equals):
            depth = 0
            end = None
            for index in range(brace, len(text)):
                if text[index] == "{": depth += 1
                elif text[index] == "}":
                    depth -= 1
                    if depth == 0:
                        end = index + 1
                        break
            if end is None: raise ContractError("frontend_declaration_unclosed")
        else:
            end = text.find(";", match.end())
            if end == -1: raise ContractError("frontend_declaration_unclosed")
            end += 1
        declaration = re.sub(r"\s+", " ", text[start:end]).strip()
        if name in declarations: raise ContractError("duplicate_frontend_type")
        declarations[name] = declaration
    if not declarations: raise ContractError("frontend_types_missing")
    return dict(sorted(declarations.items()))


def _extract_ts_interface_fields(path: Path) -> dict[str, list[str]]:
    """Extract only top-level interface keys; nested object keys stay opaque."""
    text = path.read_text(encoding="utf-8")
    fields: dict[str, list[str]] = {}
    for match in re.finditer(r"export\s+interface\s+([A-Za-z_][A-Za-z0-9_]*)\s*{", text):
        name = match.group(1)
        depth = 1
        buffer = ""
        names: list[str] = []
        for character in text[match.end() :]:
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    break
            if depth == 1:
                buffer += character
                if character == ";":
                    field_match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\??\s*:", buffer)
                    if field_match:
                        names.append(field_match.group(1))
                    buffer = ""
        if depth != 0:
            raise ContractError("frontend_declaration_unclosed")
        fields[name] = sorted(names)
    return dict(sorted(fields.items()))


def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return "<dynamic>"


def _cli_ast(path: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    commands: set[str] = set()
    arguments: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute): continue
        if node.func.attr == "add_parser" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            commands.add(node.args[0].value)
        if node.func.attr == "add_argument":
            names = [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
            if names:
                keywords = {item.arg: _normalize(_literal(item.value)) for item in node.keywords if item.arg in {"action", "choices", "default", "dest", "required"}}
                arguments.append({"names": sorted(names), "options": keywords})
    arguments.sort(key=lambda row: canonical_json(row))
    return {"arguments": arguments, "commands": sorted(commands)}


def _artifact_contract(path: Path) -> dict[str, Any]:
    value = load_json(path)
    versions = {key: value[key] for key in ("schema_version", "protocol", "contract", "analysis") if key in value and isinstance(value[key], str)}
    return {"top_level_fields": {key: type(child).__name__ for key, child in sorted(value.items())}, "versions": versions}


def _frontend_openapi_consistency(openapi: Mapping[str, Any], frontend_fields: Mapping[str, list[str]]) -> dict[str, Any]:
    schemas = openapi.get("components", {}).get("schemas", {})
    if not isinstance(schemas, Mapping):
        raise ContractError("openapi_components_invalid")
    checked: dict[str, Any] = {}
    for name in sorted(set(frontend_fields) & set(schemas)):
        schema = schemas[name]
        if not isinstance(schema, Mapping) or not isinstance(schema.get("properties", {}), Mapping):
            continue
        backend = sorted(schema.get("properties", {}))
        frontend = frontend_fields[name]
        if backend != frontend:
            raise ContractError(f"frontend_openapi_field_mismatch:{name}")
        checked[name] = {"fields": backend, "nullable_or_optional_governed_by_schema": True}
    if not checked:
        raise ContractError("frontend_openapi_models_missing")
    return checked


def build_snapshot(protocol: Mapping[str, Any], *, repository_root: Path = ROOT) -> dict[str, Any]:
    root = repository_root.resolve()
    cli = {}
    for name, spec in sorted(protocol["cli_contracts"].items()):
        row = _cli_ast(_safe(root, spec["path"]))
        row["exit_codes"] = spec["exit_codes"]
        cli[name] = row
    artifacts = {name: _artifact_contract(_safe(root, path)) for name, path in sorted(protocol["artifact_contracts"].items())}
    artifacts["run_manifest_v1"] = {"json_schema": _normalize(RunManifestV1.model_json_schema()), "versions": {"schema_version": "1"}}
    documents = {}
    for relative in protocol["documentation_files"]:
        path = _safe(root, relative)
        relevant = sorted(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if "scripts/check_" in line or "_v1" in line)
        documents[relative] = stable_hash(relevant)
    frontend_path = _safe(root, protocol["frontend_types"])
    openapi = _openapi_contract()
    frontend_fields = _extract_ts_interface_fields(frontend_path)
    snapshot = {
        "artifacts": artifacts,
        "cli": cli,
        "documentation": documents,
        "execution": dict(protocol["execution"]),
        "formal_validation_complete": False,
        "frontend": {"declarations": _extract_ts_declarations(frontend_path)},
        "frontend_openapi_consistency": _frontend_openapi_consistency(openapi, frontend_fields),
        "openapi": openapi,
        "protocol": PROTOCOL,
        "schema_version": SCHEMA_VERSION,
        "source_commit": protocol["source_commit"],
    }
    snapshot["content_sha256"] = stable_hash(snapshot)
    return snapshot


def _diff(base: Any, current: Any, path: str, changes: list[dict[str, str]], extension_policy: str) -> None:
    if type(base) is not type(current):
        changes.append({"classification": "breaking", "path": path, "reason": "type_changed"}); return
    if isinstance(base, dict):
        for key in sorted(set(base) - set(current)):
            changes.append({"classification": "breaking", "path": f"{path}/{key}", "reason": "field_removed"})
        for key in sorted(set(current) - set(base)):
            classification = "additive_review_required" if extension_policy == "allow_optional" else "breaking"
            changes.append({"classification": classification, "path": f"{path}/{key}", "reason": "field_added"})
        for key in sorted(set(base) & set(current)):
            _diff(base[key], current[key], f"{path}/{key}", changes, extension_policy)
    elif isinstance(base, list):
        if path.endswith("/enum") and not set(base).issubset(set(current)):
            changes.append({"classification": "breaking", "path": path, "reason": "enum_narrowed"})
        elif base != current:
            changes.append({"classification": "breaking", "path": path, "reason": "ordered_or_membership_changed"})
    elif base != current:
        changes.append({"classification": "breaking", "path": path, "reason": "value_changed"})


def compare_snapshots(base: Mapping[str, Any], current: Mapping[str, Any], *, extension_policy: str = "forbid") -> dict[str, Any]:
    left = {key: value for key, value in base.items() if key != "content_sha256"}
    right = {key: value for key, value in current.items() if key != "content_sha256"}
    changes: list[dict[str, str]] = []
    _diff(left, right, "$", changes, extension_policy)
    classes = {row["classification"] for row in changes}
    classification = "breaking" if "breaking" in classes else "additive_review_required" if changes else "compatible"
    return {"change_count": len(changes), "changes": changes, "classification": classification, "protocol": PROTOCOL, "schema_version": SCHEMA_VERSION}


def validate_snapshot(value: Mapping[str, Any]) -> None:
    required = {"artifacts", "cli", "content_sha256", "documentation", "execution", "formal_validation_complete", "frontend", "frontend_openapi_consistency", "openapi", "protocol", "schema_version", "source_commit"}
    if set(value) != required or value.get("protocol") != PROTOCOL or value.get("schema_version") != SCHEMA_VERSION:
        raise ContractError("contract_snapshot_schema_invalid")
    expected = stable_hash({key: child for key, child in value.items() if key != "content_sha256"})
    if value.get("content_sha256") != expected:
        raise ContractError("contract_snapshot_hash_mismatch")
    if value.get("formal_validation_complete") is not False:
        raise ContractError("formal_validation_boundary_drift")


def verify_current(protocol: Mapping[str, Any], baseline: Mapping[str, Any], *, repository_root: Path = ROOT) -> dict[str, Any]:
    validate_snapshot(baseline)
    current = build_snapshot(protocol, repository_root=repository_root)
    comparison = compare_snapshots(baseline, current)
    if comparison["classification"] != "compatible":
        raise ContractError("public_contract_drift")
    return {"baseline_sha256": baseline.get("content_sha256"), "contract_count": len(current["artifacts"]) + len(current["cli"]) + 2, "execution": protocol["execution"], "exit_code": 0, "formal_validation_complete": False, "protocol": PROTOCOL, "schema_version": SCHEMA_VERSION, "snapshot_sha256": current["content_sha256"], "status": "contracts_compatible"}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_bytes(canonical_json(value))
    temporary.replace(path)

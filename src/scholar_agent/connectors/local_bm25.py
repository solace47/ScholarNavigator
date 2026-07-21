"""Deterministic BM25 connector for explicitly configured local JSONL corpora."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from rank_bm25 import BM25Okapi

from scholar_agent.connectors.schemas import ConnectorSearchResult
from scholar_agent.core.diagnostics_schemas import ConnectorDiagnostics
from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers


LOCAL_BM25_CONNECTOR_VERSION = "local-bm25-v1"
LOCAL_BM25_CACHE_SCHEMA_VERSION = "1"
LOCAL_BM25_TOKENIZER_VERSION = "unicode-word-casefold-v1"
TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
DEFAULT_K1 = 1.5
DEFAULT_B = 0.75
DEFAULT_EPSILON = 0.25
IdentityField = Literal[
    "doi",
    "arxiv_id",
    "semantic_scholar_id",
    "s2orc_corpus_id",
    "openalex_id",
    "pubmed_id",
]


@dataclass(frozen=True)
class LocalBM25FieldConfig:
    """JSONL field paths and the stable identity carried by the document ID."""

    document_id: str = "_id"
    title: str = "title"
    abstract: str = "abstract"
    document_id_identity: IdentityField = "s2orc_corpus_id"
    doi: str | None = None
    arxiv_id: str | None = None
    semantic_scholar_id: str | None = None
    s2orc_corpus_id: str | None = None
    openalex_id: str | None = None
    pubmed_id: str | None = None


@dataclass(frozen=True)
class LocalBM25Config:
    """Explicit local corpus configuration; no dataset or evaluator inputs exist."""

    corpus_path: Path
    cache_dir: Path
    fields: LocalBM25FieldConfig = LocalBM25FieldConfig()
    k1: float = DEFAULT_K1
    b: float = DEFAULT_B
    epsilon: float = DEFAULT_EPSILON


@dataclass(frozen=True)
class LocalBM25IndexMetadata:
    corpus_sha256: str
    corpus_size_bytes: int
    document_count: int
    fingerprint: str
    cache_path: str
    cache_hit: bool
    index_load_seconds: float


@dataclass
class _LocalBM25Index:
    fingerprint: str
    papers: list[Paper]
    document_ids: list[str]
    tokenized_documents: list[list[str]]
    engine: BM25Okapi


_CONFIG_LOCK = RLock()
_ACTIVE_CONFIG: LocalBM25Config | None = None
_ACTIVE_METADATA: LocalBM25IndexMetadata | None = None
_ACTIVE_INDEX: _LocalBM25Index | None = None


def tokenize_local_bm25(value: str | None) -> list[str]:
    """Match the frozen SciFact offline audit tokenizer exactly."""

    return TOKEN_PATTERN.findall(str(value or "").casefold())


def configure_local_bm25(
    config: LocalBM25Config | None,
    *,
    build_index: bool = True,
) -> LocalBM25IndexMetadata | None:
    """Set or clear the process-local connector configuration.

    Replay can set ``build_index=False`` because only the deterministic
    fingerprint is needed to validate Snapshot keys.
    """

    global _ACTIVE_CONFIG, _ACTIVE_INDEX, _ACTIVE_METADATA
    with _CONFIG_LOCK:
        _ACTIVE_CONFIG = None
        _ACTIVE_INDEX = None
        _ACTIVE_METADATA = None
        if config is None:
            return None
        normalized = _normalize_config(config)
        fingerprint, corpus_sha, corpus_size = _fingerprint(normalized)
        cache_path = _cache_path(normalized, fingerprint)
        _ACTIVE_CONFIG = normalized
        _ACTIVE_METADATA = LocalBM25IndexMetadata(
            corpus_sha256=corpus_sha,
            corpus_size_bytes=corpus_size,
            document_count=_nonempty_line_count(normalized.corpus_path),
            fingerprint=fingerprint,
            cache_path=str(cache_path),
            cache_hit=False,
            index_load_seconds=0.0,
        )
        if build_index:
            _ACTIVE_INDEX, _ACTIVE_METADATA = _load_or_build_index(
                normalized,
                fingerprint=fingerprint,
                corpus_sha=corpus_sha,
                corpus_size=corpus_size,
            )
        return _ACTIVE_METADATA


def local_bm25_metadata() -> LocalBM25IndexMetadata:
    with _CONFIG_LOCK:
        if _ACTIVE_METADATA is None:
            raise ValueError("local_bm25_not_configured")
        return _ACTIVE_METADATA


def local_bm25_connector_version() -> str:
    metadata = local_bm25_metadata()
    return f"{LOCAL_BM25_CONNECTOR_VERSION}:{metadata.fingerprint}"


def search_local_bm25(query: str, limit: int = 20) -> list[Paper]:
    return search_local_bm25_detailed(query, limit).papers


def search_local_bm25_detailed(
    query: str,
    limit: int = 20,
) -> ConnectorSearchResult:
    """Search the configured local index without evaluator-side information."""

    started = time.perf_counter()
    normalized_query = str(query).strip()
    if not normalized_query or limit <= 0:
        latency = time.perf_counter() - started
        return ConnectorSearchResult(
            warnings=["local_bm25_empty_query"] if not normalized_query else [],
            latency_seconds=latency,
            diagnostics=ConnectorDiagnostics(latency_seconds=latency),
        )
    try:
        index, metadata = _active_index()
    except (OSError, ValueError) as exc:
        latency = time.perf_counter() - started
        return ConnectorSearchResult(
            error_message=f"local_bm25_failed:{type(exc).__name__}",
            warnings=[f"local_bm25_failed:{type(exc).__name__}"],
            latency_seconds=latency,
            diagnostics=ConnectorDiagnostics(
                error_count=1,
                latency_seconds=latency,
            ),
        )

    tokens = tokenize_local_bm25(normalized_query)
    scores = index.engine.get_scores(tokens)
    ranked = sorted(
        range(len(index.papers)),
        key=lambda offset: (-float(scores[offset]), index.document_ids[offset]),
    )[: min(int(limit), len(index.papers))]
    papers = [index.papers[offset].model_copy(deep=True) for offset in ranked]
    latency = time.perf_counter() - started
    return ConnectorSearchResult(
        papers=papers,
        warnings=[
            "local_bm25_index_cache_hit"
            if metadata.cache_hit
            else "local_bm25_index_built"
        ],
        latency_seconds=latency,
        diagnostics=ConnectorDiagnostics(
            cache_hit_count=int(metadata.cache_hit),
            latency_seconds=latency,
        ),
    )


def _active_index() -> tuple[_LocalBM25Index, LocalBM25IndexMetadata]:
    global _ACTIVE_INDEX, _ACTIVE_METADATA
    with _CONFIG_LOCK:
        if _ACTIVE_CONFIG is None or _ACTIVE_METADATA is None:
            raise ValueError("local_bm25_not_configured")
        if _ACTIVE_INDEX is None:
            _ACTIVE_INDEX, _ACTIVE_METADATA = _load_or_build_index(
                _ACTIVE_CONFIG,
                fingerprint=_ACTIVE_METADATA.fingerprint,
                corpus_sha=_ACTIVE_METADATA.corpus_sha256,
                corpus_size=_ACTIVE_METADATA.corpus_size_bytes,
            )
        return _ACTIVE_INDEX, _ACTIVE_METADATA


def _normalize_config(config: LocalBM25Config) -> LocalBM25Config:
    corpus_path = Path(config.corpus_path).expanduser().resolve()
    cache_dir = Path(config.cache_dir).expanduser().resolve()
    if not corpus_path.is_file():
        raise ValueError("local_bm25_corpus_not_found")
    if corpus_path.suffix.casefold() not in {".jsonl", ".json"}:
        raise ValueError("local_bm25_corpus_must_be_jsonl")
    for name, value in asdict(config.fields).items():
        if value is not None and not str(value).strip():
            raise ValueError(f"local_bm25_empty_field:{name}")
    if (config.k1, config.b, config.epsilon) != (
        DEFAULT_K1,
        DEFAULT_B,
        DEFAULT_EPSILON,
    ):
        raise ValueError("local_bm25_parameters_are_frozen")
    return LocalBM25Config(
        corpus_path=corpus_path,
        cache_dir=cache_dir,
        fields=config.fields,
        k1=config.k1,
        b=config.b,
        epsilon=config.epsilon,
    )


def _fingerprint(config: LocalBM25Config) -> tuple[str, str, int]:
    payload = config.corpus_path.read_bytes()
    corpus_sha = hashlib.sha256(payload).hexdigest()
    descriptor = {
        "cache_schema_version": LOCAL_BM25_CACHE_SCHEMA_VERSION,
        "connector_version": LOCAL_BM25_CONNECTOR_VERSION,
        "corpus_sha256": corpus_sha,
        "fields": asdict(config.fields),
        "parameters": {
            "b": config.b,
            "epsilon": config.epsilon,
            "k1": config.k1,
        },
        "rank_bm25_version": version("rank_bm25"),
        "tokenizer": LOCAL_BM25_TOKENIZER_VERSION,
    }
    encoded = json.dumps(
        descriptor,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), corpus_sha, len(payload)


def _cache_path(config: LocalBM25Config, fingerprint: str) -> Path:
    return config.cache_dir / f"{fingerprint}.json.gz"


def _load_or_build_index(
    config: LocalBM25Config,
    *,
    fingerprint: str,
    corpus_sha: str,
    corpus_size: int,
) -> tuple[_LocalBM25Index, LocalBM25IndexMetadata]:
    started = time.perf_counter()
    cache_path = _cache_path(config, fingerprint)
    cache_hit = False
    index: _LocalBM25Index | None = None
    if cache_path.is_file():
        try:
            with gzip.open(cache_path, mode="rt", encoding="utf-8") as handle:
                candidate = json.load(handle)
            index = _deserialize_index(candidate, config, fingerprint)
            if index is not None:
                cache_hit = True
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            index = None
    if index is None:
        index = _build_index(config, fingerprint)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = tempfile.NamedTemporaryFile(
            mode="w",
            prefix=f".{fingerprint}.",
            suffix=".tmp",
            dir=cache_path.parent,
            delete=False,
        )
        temporary = Path(descriptor.name)
        descriptor.close()
        try:
            with gzip.open(
                temporary,
                mode="wt",
                encoding="utf-8",
                compresslevel=1,
            ) as handle:
                json.dump(
                    _serialize_index(index),
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, cache_path)
        finally:
            temporary.unlink(missing_ok=True)
    metadata = LocalBM25IndexMetadata(
        corpus_sha256=corpus_sha,
        corpus_size_bytes=corpus_size,
        document_count=len(index.papers),
        fingerprint=fingerprint,
        cache_path=str(cache_path),
        cache_hit=cache_hit,
        index_load_seconds=time.perf_counter() - started,
    )
    return index, metadata


def _build_index(config: LocalBM25Config, fingerprint: str) -> _LocalBM25Index:
    papers: list[Paper] = []
    document_ids: list[str] = []
    tokenized: list[list[str]] = []
    seen: dict[str, tuple[str, str, PaperIdentifiers]] = {}
    with config.corpus_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"local_bm25_invalid_jsonl:{line_number}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"local_bm25_invalid_row:{line_number}")
            document_id = _required_value(
                payload,
                config.fields.document_id,
                line_number,
            )
            title = _string_value(payload, config.fields.title)
            abstract = _string_value(payload, config.fields.abstract) or ""
            if not title:
                raise ValueError(f"local_bm25_missing_title:{line_number}")
            identifiers = _identifiers(payload, document_id, config.fields)
            signature = (title, abstract, identifiers)
            prior = seen.get(document_id)
            if prior is not None:
                if prior != signature:
                    raise ValueError(f"local_bm25_conflicting_document:{document_id}")
                continue
            seen[document_id] = signature
            paper = Paper(
                title=title,
                abstract=abstract,
                identifiers=identifiers,
                sources=["local_bm25"],
            )
            papers.append(paper)
            document_ids.append(document_id)
            tokenized.append(tokenize_local_bm25(f"{title} {abstract}"))
    if not papers:
        raise ValueError("local_bm25_empty_corpus")
    order = sorted(range(len(papers)), key=lambda offset: document_ids[offset])
    sorted_papers = [papers[offset] for offset in order]
    sorted_ids = [document_ids[offset] for offset in order]
    sorted_tokens = [tokenized[offset] for offset in order]
    engine = BM25Okapi(
        sorted_tokens,
        k1=config.k1,
        b=config.b,
        epsilon=config.epsilon,
    )
    return _LocalBM25Index(
        fingerprint=fingerprint,
        papers=sorted_papers,
        document_ids=sorted_ids,
        tokenized_documents=sorted_tokens,
        engine=engine,
    )


def _serialize_index(index: _LocalBM25Index) -> dict[str, Any]:
    payload = {
        "cache_schema_version": LOCAL_BM25_CACHE_SCHEMA_VERSION,
        "fingerprint": index.fingerprint,
        "document_ids": index.document_ids,
        "papers": [paper.model_dump(mode="json") for paper in index.papers],
        "tokenized_documents": index.tokenized_documents,
    }
    return {**payload, "content_hash": _stable_json_hash(payload)}


def _deserialize_index(
    payload: Any,
    config: LocalBM25Config,
    fingerprint: str,
) -> _LocalBM25Index | None:
    if not isinstance(payload, dict):
        return None
    content = {key: value for key, value in payload.items() if key != "content_hash"}
    if (
        payload.get("cache_schema_version") != LOCAL_BM25_CACHE_SCHEMA_VERSION
        or payload.get("fingerprint") != fingerprint
        or payload.get("content_hash") != _stable_json_hash(content)
    ):
        return None
    raw_ids = payload.get("document_ids")
    raw_papers = payload.get("papers")
    raw_tokens = payload.get("tokenized_documents")
    if not all(isinstance(value, list) for value in (raw_ids, raw_papers, raw_tokens)):
        return None
    document_ids = [str(value) for value in raw_ids]
    papers = [Paper.model_validate(value) for value in raw_papers]
    tokenized = [
        [str(token) for token in tokens]
        for tokens in raw_tokens
        if isinstance(tokens, list)
    ]
    if not document_ids or not (
        len(document_ids) == len(papers) == len(tokenized)
    ):
        return None
    if document_ids != sorted(set(document_ids)):
        return None
    for document_id, paper in zip(document_ids, papers, strict=True):
        if (
            getattr(paper.identifiers, config.fields.document_id_identity)
            != document_id
        ):
            return None
    return _LocalBM25Index(
        fingerprint=fingerprint,
        papers=papers,
        document_ids=document_ids,
        tokenized_documents=tokenized,
        engine=BM25Okapi(
            tokenized,
            k1=config.k1,
            b=config.b,
            epsilon=config.epsilon,
        ),
    )


def _identifiers(
    payload: dict[str, Any],
    document_id: str,
    fields: LocalBM25FieldConfig,
) -> PaperIdentifiers:
    values: dict[str, str | None] = {}
    for name in PaperIdentifiers.model_fields:
        field_path = getattr(fields, name)
        values[name] = _string_value(payload, field_path) if field_path else None
    existing = values[fields.document_id_identity]
    if existing is not None and existing != document_id:
        raise ValueError(
            f"local_bm25_document_identity_conflict:{fields.document_id_identity}"
        )
    values[fields.document_id_identity] = document_id
    return PaperIdentifiers(**values)


def _required_value(
    payload: dict[str, Any],
    field_path: str,
    line_number: int,
) -> str:
    value = _string_value(payload, field_path)
    if value is None:
        raise ValueError(f"local_bm25_missing_document_id:{line_number}")
    return value


def _string_value(payload: dict[str, Any], field_path: str | None) -> str | None:
    if not field_path:
        return None
    value: Any = payload
    for part in field_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _nonempty_line_count(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(bool(line.strip()) for line in handle)


def _stable_json_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

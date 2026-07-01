# Connector Runbook

## Scope

This runbook covers the first real retrieval connector layer for:

- OpenAlex Works API
- arXiv public API

The current FastAPI Mock API response logic is unchanged. These connectors are standalone backend utilities for the next integration step. They do not call any LLM, do not rank papers, and do not replace the existing mock search result endpoints.

## OpenAlex Connector

File:

```text
src/scholar_agent/connectors/openalex.py
```

Function:

```python
search_openalex(query: str, limit: int = 20) -> list[Paper]
search_openalex_detailed(query: str, limit: int = 20) -> ConnectorSearchResult
fetch_openalex_references(paper: Paper, limit: int = 20) -> list[Paper]
```

Behavior:

- Calls the OpenAlex Works API.
- Uses `search` and `per-page` query parameters.
- Applies a 10 second timeout.
- `search_openalex` returns an empty list on network errors, timeout, non-2xx
  response, or malformed top-level JSON.
- `search_openalex_detailed` preserves the same fail-closed behavior but also
  returns `error_message` and `warnings` for diagnostics.
- `search_openalex_detailed` retries transient failures once by default:
  HTTP `429`, HTTP `5xx`, timeout, `URLError`, and related `OSError` failures.
  Retry backoff is intentionally light. Tests inject or monkeypatch sleep so
  retry tests do not wait in real time.
- If retry succeeds, `warnings` keeps a `retried` diagnostic and
  `error_message` remains `None`.
- If retry still fails, `error_message` contains the final error and `warnings`
  contain both the retry diagnostic and final error.
- Skips malformed individual results instead of failing the whole search.
- Returns connector-layer `Paper` objects with `sources=["openalex"]`.

`ConnectorSearchResult` contains:

- `papers`
- `error_message`
- `warnings`

`search_openalex` is a compatibility wrapper around
`search_openalex_detailed(...).papers`.

Optional environment variable:

```bash
export OPENALEX_MAILTO=your-email@example.com
```

When set, the connector includes `mailto` in the OpenAlex request parameters and identifies the caller in the `User-Agent`.

### OpenAlex Reference Fetching

`fetch_openalex_references` is a standalone helper for the future RefChain
stage. It is not connected to `SearchService` yet.

Seed resolution:

1. Prefer `paper.identifiers.openalex_id`.
2. If OpenAlex ID is missing, use `paper.identifiers.doi` to resolve the seed
   work through OpenAlex.
3. If neither identifier exists, return an empty list without making a network
   call.

Reference retrieval:

- Reads the seed work's `referenced_works` field.
- Normalizes OpenAlex work IDs from either full URLs or bare IDs.
- Fetches referenced work metadata from the Works API.
- Parses each referenced work through the same work-to-`Paper` logic used by
  `search_openalex`.
- Applies the caller-provided `limit`.
- Returns `Paper` objects with `sources=["openalex"]`.

Failure handling:

- Timeout, network errors, non-2xx responses, malformed JSON, missing
  `referenced_works`, or unsupported seed identifiers do not raise to callers.
- Seed resolution failures return an empty list.
- Individual reference fetch/parse failures are skipped so other references can
  still be returned.

Current boundaries:

- No full-text PDF parsing.
- No arXiv reference extraction.
- No recursive/multi-layer citation expansion.
- No judgement or reranking inside the connector.
- No SearchService integration yet.

## arXiv Connector

File:

```text
src/scholar_agent/connectors/arxiv.py
```

Function:

```python
search_arxiv(query: str, limit: int = 20) -> list[Paper]
search_arxiv_detailed(query: str, limit: int = 20) -> ConnectorSearchResult
```

Behavior:

- Calls the arXiv public API.
- Uses `search_query=all:<query>`, `start=0`, and `max_results=<limit>`.
- Applies a 10 second timeout.
- `search_arxiv` returns an empty list on network errors, timeout, non-2xx
  response, or XML parse failure.
- `search_arxiv_detailed` preserves the same fail-closed behavior but also
  returns `error_message` and `warnings` for diagnostics.
- `search_arxiv_detailed` retries transient failures once by default:
  HTTP `429`, HTTP `5xx`, timeout, `URLError`, and related `OSError` failures.
  XML parse failures are not retried because they indicate a malformed payload
  already returned by the service.
- If retry succeeds, `warnings` keeps a `retried` diagnostic and
  `error_message` remains `None`.
- If retry still fails, `error_message` contains the final error and `warnings`
  contain both the retry diagnostic and final error.
- Skips malformed individual entries instead of failing the whole search.
- Returns connector-layer `Paper` objects with `sources=["arxiv"]`.

`search_arxiv` is a compatibility wrapper around
`search_arxiv_detailed(...).papers`.

## Manual Real Retrieval Test

Tests do not access the network. To manually call real services during development:

```bash
cd /Users/xs/Documents/Coding/SelfProject/paper-agent/SPAR
PYTHONPATH=src python scripts/dev_search_connectors.py "LLM reranking scientific literature search" --limit 5
```

With OpenAlex polite pool metadata:

```bash
OPENALEX_MAILTO=your-email@example.com \
PYTHONPATH=src python scripts/dev_search_connectors.py "LLM reranking scientific literature search" --limit 5
```

## Current Non-goals

- No LLM calls.
- No ranking or judgement.
- No query evolution.
- No SearchService-level citation expansion.
- No replacement of the existing FastAPI Mock API response logic.
- No frontend changes.

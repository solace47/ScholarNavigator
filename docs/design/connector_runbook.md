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
```

Behavior:

- Calls the OpenAlex Works API.
- Uses `search` and `per-page` query parameters.
- Applies a 10 second timeout.
- Returns an empty list on network errors, timeout, non-2xx response, or malformed top-level JSON.
- Skips malformed individual results instead of failing the whole search.
- Returns connector-layer `Paper` objects with `sources=["openalex"]`.

Optional environment variable:

```bash
export OPENALEX_MAILTO=your-email@example.com
```

When set, the connector includes `mailto` in the OpenAlex request parameters and identifies the caller in the `User-Agent`.

## arXiv Connector

File:

```text
src/scholar_agent/connectors/arxiv.py
```

Function:

```python
search_arxiv(query: str, limit: int = 20) -> list[Paper]
```

Behavior:

- Calls the arXiv public API.
- Uses `search_query=all:<query>`, `start=0`, and `max_results=<limit>`.
- Applies a 10 second timeout.
- Returns an empty list on network errors, timeout, non-2xx response, or XML parse failure.
- Skips malformed individual entries instead of failing the whole search.
- Returns connector-layer `Paper` objects with `sources=["arxiv"]`.

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
- No citation expansion.
- No replacement of the existing FastAPI Mock API response logic.
- No frontend changes.


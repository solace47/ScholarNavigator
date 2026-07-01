# Retriever Runbook

## Scope

This runbook covers the first multi-source retrieval aggregation layer:

- connector-layer paper schema
- paper deduplication
- OpenAlex + arXiv aggregation

The existing FastAPI Mock API response logic is unchanged. This layer does not call any LLM, does not rank papers, does not judge relevance, does not run Query Evolution, and does not replace the mock endpoints.

## Deduplication

File:

```text
src/scholar_agent/core/dedup.py
```

Function:

```python
deduplicate_papers(papers: list[Paper]) -> list[Paper]
```

Deduplication priority:

1. DOI exact match after lowercasing and DOI prefix normalization.
2. arXiv ID exact match after removing version suffixes such as `v1`, `v2`, `v3`.
3. OpenAlex ID exact match after URL/path normalization.
4. Semantic Scholar ID exact match.
5. PubMed ID exact match after URL/path normalization.
6. High title similarity plus compatible year.

Title normalization:

- lowercase
- remove punctuation
- collapse repeated whitespace
- remove common LaTeX command/symbol noise
- trim leading and trailing whitespace

Title-based duplicate matching requires:

- normalized title equality, or similarity ratio >= `0.92`
- both years present
- years equal or differ by at most 1

Merge behavior:

- `sources`: merged and deduplicated while preserving first-seen order
- `identifiers`: missing values are filled from duplicate records
- `urls`: missing values are filled from duplicate records
- `citation_count`: maximum value wins
- `abstract`: longer version wins
- `authors`: longer author list wins
- `venue`: existing non-empty value wins, otherwise fill from duplicate
- `title`: longer or non-placeholder title wins
- `year`: existing non-empty value wins, otherwise fill from duplicate

## Retrieval Aggregator

File:

```text
src/scholar_agent/agents/retriever.py
```

Function:

```python
retrieve_papers(
    query: str,
    limit_per_source: int = 20,
    sources: list[str] | None = None,
) -> RetrievalOutput
```

Default sources:

```python
["openalex", "arxiv"]
```

Supported sources:

- `openalex`
- `arxiv`

Unsupported sources are reported in `warnings` and `source_stats`; they do not raise.

The aggregator uses detailed connector functions:

- `search_openalex_detailed`
- `search_arxiv_detailed`

The older connector wrappers remain available for direct callers:

- `search_openalex`
- `search_arxiv`

Those wrappers still return only `list[Paper]`.

`RetrievalOutput` contains:

- `query`
- `requested_sources`
- `raw_count`
- `deduplicated_count`
- `papers`
- `source_stats`
- `warnings`
- `latency_seconds`

Each `source_stats` item contains:

- `source`
- `returned_count`
- `latency_seconds`
- `error_message`
- `cache_hit`

Failure handling:

- Each source is isolated.
- If a connector returns `ConnectorSearchResult.error_message`, the aggregator
  stores it in `SourceStats.error_message`.
- Connector warnings are appended to `RetrievalOutput.warnings`.
- If one source raises unexpectedly, the aggregator records a warning and
  continues with the remaining sources.
- Deduplication runs after all successful source results are collected.

## In-memory Retrieval Cache

`retrieve_papers` includes a lightweight process-local cache around detailed
connector calls.

Cache key:

```text
source + query + limit_per_source
```

This keeps OpenAlex and arXiv entries isolated, so one source can hit cache while
another source retries normally.

Defaults:

- enabled by default
- TTL: 900 seconds
- max entries: 256
- storage: in-memory only, not shared across processes

Environment variables:

```bash
SCHOLAR_AGENT_RETRIEVAL_CACHE=0
SCHOLAR_AGENT_RETRIEVAL_CACHE_TTL_SECONDS=900
SCHOLAR_AGENT_RETRIEVAL_CACHE_MAX_ENTRIES=256
```

Behavior:

- cache hit: connector is not called, cached papers and connector warnings are
  returned, and `SourceStats.cache_hit=True`
- cache miss: connector runs normally and `SourceStats.cache_hit=False`
- successful detailed connector results are cached
- results with `error_message` are not cached, so transient connector failures
  are retried on the next request
- cache-hit diagnostics are surfaced as `retrieval_cache_hit:{source}` warnings
  without modifying paper content

Tests can call:

```python
from scholar_agent.agents.retriever import clear_retrieval_cache
```

to avoid cross-test state pollution.

## Manual Usage

Python example:

```python
from scholar_agent.agents.retriever import retrieve_papers

output = retrieve_papers(
    "LLM reranking scientific literature search",
    limit_per_source=10,
    sources=["openalex", "arxiv"],
)

print(output.raw_count, output.deduplicated_count)
for paper in output.papers:
    print(paper.title, paper.sources)
```

The retrieval aggregator may call real connectors if used directly. Unit tests mock connector functions and do not access the network.

## Current Non-goals

- No LLM calls.
- No relevance judgement.
- No reranking.
- No query evolution.
- No RefChain expansion.
- No Semantic Scholar connector.
- No PubMed connector.
- No replacement of the existing FastAPI Mock API.
- No frontend changes.

## Future SearchService Integration

A later `SearchService` can wrap `retrieve_papers` as the retrieval stage:

```text
SearchRequest
  -> QueryUnderstandingAgent
  -> retrieve_papers
  -> deduplicate_papers
  -> rule prefilter
  -> JudgementAgent
  -> RerankerAgent
  -> SynthesizerAgent
```

Recommended integration steps:

1. Add a backend service layer that accepts `SearchPlan`.
2. Convert `SearchPlan.source_preferences` into `retrieve_papers(..., sources=...)`.
3. Persist `RetrievalOutput.source_stats` into pipeline trace.
4. Keep connector errors as warnings in final output.
5. Only after tests pass, wire the service into a non-mock API endpoint or a feature-flagged path.

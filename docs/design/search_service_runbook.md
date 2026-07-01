# Search Service Runbook

## Scope

This runbook covers the internal no-LLM `SearchService` pipeline.

Current boundaries:

- No LLM calls.
- No FastAPI Mock API replacement.
- No frontend changes.
- No `third_party` changes.
- RefChain is available as an optional no-LLM stage when
  `enable_refchain=True`.
- Query Evolution is available as an optional no-LLM stage when
  `enable_query_evolution=True`.

The default service can call real retrieval connectors through `retrieve_papers`.
When RefChain is enabled, the default service can call OpenAlex reference
fetching through `fetch_openalex_references`. Unit tests inject fake retrievers
and fake reference fetchers and do not access the network.

## Module Responsibility

File:

```text
src/scholar_agent/services/search_service.py
```

Public function:

```python
from scholar_agent.services.search_service import run_search

output = run_search(
    "LLM reranking for scientific literature retrieval",
    top_k=20,
    run_profile="balanced",
    current_year=2026,
)
```

Injectable service:

```python
from scholar_agent.services.search_service import SearchService

service = SearchService(
    retriever=fake_retriever,
    reference_fetcher=fake_reference_fetcher,
)
output = service.run_search("LLM reranking")
```

Concurrency can be configured at construction time:

```python
service = SearchService(retriever=fake_retriever, max_workers=4)
```

The default `SearchService` constructor still uses `max_workers=4`.
Internal preview endpoints intentionally use a lower default to reduce pressure
on live OpenAlex/arXiv calls during manual frontend/backend validation.

## Pipeline

Execution order:

```text
query
  -> analyze_query
  -> initial retrieve_papers for each SearchSubquery
  -> aggregate initial papers
  -> deduplicate_papers across initial subqueries
  -> judge_papers
  -> rerank_papers
  -> optional evolve_queries
  -> optional retrieve_papers for evolved queries
  -> optional merge initial and evolved papers
  -> optional deduplicate_papers across all papers
  -> optional judge_papers
  -> optional rerank_papers
  -> optional expand_refchain
  -> optional merge references
  -> optional deduplicate_papers across all papers and references
  -> optional judge_papers
  -> optional rerank_papers
  -> SearchServiceOutput
```

Details:

- `analyze_query` generates a `SearchPlan`.
- Each `SearchSubquery` is sent to `retrieve_papers`.
- `retrieve_papers` returns a `RetrievalOutput` per subquery.
- `SearchService` combines all retrieved papers and runs cross-subquery
  `deduplicate_papers`.
- `judge_papers` evaluates deduplicated candidates against
  `SearchPlan.query_analysis`.
- `rerank_papers` produces final ranked papers using `top_k`.
- If `enable_query_evolution=True`, `evolve_queries` uses the initial
  judgement and ranking results to produce short deterministic evolved queries.
- Evolved queries reuse the same retrieval concurrency and failure isolation as
  initial subqueries.
- After evolved retrieval, `SearchService` merges all papers and reruns
  deduplication, judgement, and reranking so evolved results can participate in
  the final ranking.
- If `enable_refchain=True`, `expand_refchain` uses the current ranked papers as
  seed candidates and calls the injected reference fetcher.
- RefChain references are merged into the candidate pool, then deduplicated,
  judged, and reranked so references can participate in the final ranking.

When `enable_query_evolution=False`, the service keeps the original one-pass
behavior and `query_evolution_records` is empty.

When `enable_refchain=False`, the service does not call the reference fetcher
and `refchain_output` is `None`.

## Subquery Concurrency

`SearchService` runs subquery retrieval with a standard-library
`ThreadPoolExecutor`.

Defaults:

- `max_workers=4`
- worker count is capped by the number of generated subqueries
- `max_workers <= 0` is normalized to `1`

Ordering guarantees:

- Subqueries may complete out of order.
- `retrieval_outputs` are stored in original `SearchPlan.subqueries` order.
- Evolved query outputs, when enabled, are appended after initial outputs in
  evolved-query order.
- RefChain does not append to `retrieval_outputs` because it is not a query
  retrieval batch; it appends one `SourceStats(source="refchain")` item when
  enabled.
- `source_stats` and retrieval warnings are aggregated by that same stable order.
- Downstream deduplication, judgement, and reranking receive papers in stable
  subquery order.

Failure handling:

- A single subquery retrieval exception does not fail the whole search.
- The failed subquery is represented as an empty `RetrievalOutput`.
- The empty output includes one `SourceStats` item with `source="subquery"` and
  the error message.
- A warning in the form `subquery_failed:{index}:{error}` is added.
- Other subquery results continue through deduplication, judgement, and rerank.
- A failed evolved query is represented the same way, with
  `source="evolved_query"` and warning
  `evolved_query_failed:{index}:{error}`.
- A failed RefChain seed is isolated inside `RefChainAgent` and is surfaced as
  `refchain_seed_failed:{rank}:{error}`.

## Query Evolution

The optional Query Evolution stage is no-LLM and metadata-only. It calls:

```python
from scholar_agent.agents.query_evolution import evolve_queries
```

Inputs:

- `SearchPlan.query_analysis`
- initial `SearchPlan`
- initial `judgements`
- initial `ranked_papers`
- initial subqueries as `used_queries`

Behavior:

- Only runs when `enable_query_evolution=True`.
- Generates at most the configured number of evolved queries.
- Uses only supported source hints: `openalex`, `arxiv`.
- Skips queries already present in initial `used_queries`.
- Adds `duplicate_evolved_query_skipped` if a duplicate evolved query is filtered
  at the service layer.
- Does not call retrieval itself; SearchService owns retrieval and aggregation.

Current Query Evolution boundaries:

- No LLM calls.
- No external access beyond the normal retriever calls for accepted evolved
  queries.
- No API contract change for the existing Mock API.

## RefChain

The optional RefChain stage is no-LLM and single-layer. It calls:

```python
from scholar_agent.agents.refchain import expand_refchain
```

Inputs:

- `SearchPlan.query_analysis`
- current `ranked_papers`
- injected `reference_fetcher`

Default reference fetcher:

```python
from scholar_agent.connectors.openalex import fetch_openalex_references
```

Behavior:

- Only runs when `enable_refchain=True`.
- Selects highly relevant and high-scoring partially relevant ranked papers as
  seeds.
- Skips seeds without OpenAlex ID or DOI and records a warning.
- Calls the injected fetcher once per eligible seed.
- Limits are enforced by `RefChainAgent`.
- References are merged into the existing candidate pool and pass through
  deduplication, judgement, and reranking before final output.

Current RefChain boundaries:

- No LLM calls.
- No recursive citation traversal.
- No full-text PDF parsing.
- No external access in tests.
- Default manual/internal-preview usage may access OpenAlex when RefChain is
  enabled.

## Output

`SearchServiceOutput` contains:

- `search_plan`
- `retrieval_outputs`
- `query_evolution_records`
- `refchain_output`
- `raw_count`
- `deduplicated_count`
- `judgements`
- `ranked_papers`
- `warnings`
- `source_stats`
- `latency_seconds`

Warnings are aggregated from:

- `SearchPlan.warnings`
- every `RetrievalOutput.warnings`
- every `QueryEvolutionRecord.warnings`
- SearchService duplicate-evolved-query filtering
- every `RefChainOutput.warnings`
- every `JudgementResult.warnings`

Warnings are deduplicated while preserving first-seen order.

## Retriever Injection

`SearchService` accepts a retriever function:

```python
def retriever(
    query: str,
    limit_per_source: int = 20,
    sources: list[str] | None = None,
) -> RetrievalOutput:
    ...
```

This keeps service tests offline and makes future connector experiments easier.

`SearchService` also accepts a reference fetcher:

```python
def reference_fetcher(paper: Paper, limit: int = 20) -> list[Paper]:
    ...
```

The default is `fetch_openalex_references`. Tests should inject a fake
reference fetcher whenever `enable_refchain=True`.

## FastAPI Integration Plan

The service has a standalone preview endpoint for manual backend validation:

```text
POST /api/v1/internal/search/preview
```

Request fields:

- `query`
- `top_k`
- `run_profile`
- `enable_refchain`
- `enable_query_evolution`
- `current_year`

Response fields:

- `query_analysis`
- `search_plan`
- `query_evolution_records`
- `refchain_output`
- `ranked_papers`
- `raw_count`
- `deduplicated_count`
- `warnings`
- `source_stats`
- `latency_seconds`

Debug field behavior:

- `query_evolution_records` is `[]` when `enable_query_evolution=False`.
- `refchain_output` is `null` when `enable_refchain=False`.
- When enabled, these fields expose internal SearchService records for manual
  backend validation. They are not part of the existing Mock API contract.

Important: this preview endpoint constructs `SearchService` with the default
retriever/reference fetcher and a preview-specific `max_workers` value. Unless
tests monkeypatch the service, manual requests may access OpenAlex and arXiv
over the network. If `enable_query_evolution=True`, accepted evolved queries
may cause additional OpenAlex/arXiv retrieval calls. If `enable_refchain=True`,
selected seed papers may cause additional OpenAlex reference metadata calls.

Preview concurrency:

- `/api/v1/internal/search/preview` constructs `SearchService` with
  `max_workers` from `REAL_PREVIEW_MAX_WORKERS`.
- Default preview `max_workers` is `2`.
- Invalid env values fall back to `2`.
- Values below `1` are normalized to `1`.
- This setting does not change the default `SearchService(max_workers=4)` and
  does not affect Mock API endpoints.

The service also has an API-contract preview endpoint:

```text
POST /api/v1/internal/search/preview/api-result
```

It accepts the same request fields as `/api/v1/internal/search/preview`, then
maps `SearchServiceOutput` through:

```python
map_search_service_output_to_api_result(...)
```

Response model:

```text
SearchRunResultResponse
```

This endpoint is for validating that real internal search output can fit the
existing frontend API result shape before replacing any public Mock API route.
Like the raw preview endpoint, manual calls may access OpenAlex/arXiv and, when
RefChain is enabled, OpenAlex reference metadata. Tests monkeypatch
`SearchService` and do not access external services.

This endpoint uses the same preview concurrency setting:

```bash
REAL_PREVIEW_MAX_WORKERS=1
```

Use a lower value when OpenAlex/arXiv rate limits or transient failures are
frequent during manual validation.

The existing Mock API remains unchanged:

```text
POST /api/v1/search/runs
GET /api/v1/search/runs/{run_id}
GET /api/v1/search/runs/{run_id}/result
GET /api/v1/search/runs/{run_id}/events
```

Do not replace these Mock endpoints yet.

Recommended next steps:

1. Add a backend feature flag before routing frontend traffic to real search.
2. Persist run state and progress events before exposing it to the frontend.
3. Keep Mock API as the stable frontend demo path until the service path is
   tested with real connector latency and failures.
4. Add SSE events around every pipeline stage before enabling UI integration.
5. Gate public result endpoint replacement behind an explicit real-search mode.

## Current Non-goals

- No LLM query understanding.
- No LLM judgement.
- No LLM reranking.
- No LLM Query Evolution.
- No LLM RefChain.
- No external search in tests.
- No replacement of Mock API routes.
- No frontend changes.
- No `third_party` changes.

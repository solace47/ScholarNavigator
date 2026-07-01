# Search Service Runbook

## Scope

This runbook covers the internal no-LLM `SearchService` pipeline.

Current boundaries:

- No LLM calls.
- No FastAPI Mock API replacement.
- No frontend changes.
- No `third_party` changes.
- No RefChain execution.
- Query Evolution is available as an optional no-LLM stage when
  `enable_query_evolution=True`.

The default service can call real retrieval connectors through `retrieve_papers`.
Unit tests inject a fake retriever and do not access the network.

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

service = SearchService(retriever=fake_retriever)
output = service.run_search("LLM reranking")
```

Concurrency can be configured at construction time:

```python
service = SearchService(retriever=fake_retriever, max_workers=4)
```

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

When `enable_query_evolution=False`, the service keeps the original one-pass
behavior and `query_evolution_records` is empty.

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
- No RefChain.
- No external access beyond the normal retriever calls for accepted evolved
  queries.
- No API contract change for the existing Mock API.

## Output

`SearchServiceOutput` contains:

- `search_plan`
- `retrieval_outputs`
- `query_evolution_records`
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
- `ranked_papers`
- `raw_count`
- `deduplicated_count`
- `warnings`
- `source_stats`
- `latency_seconds`

Important: this preview endpoint calls the default `SearchService`, which calls
`retrieve_papers`. Unless tests monkeypatch the service, manual requests may
access OpenAlex and arXiv over the network. If `enable_query_evolution=True`,
accepted evolved queries may cause additional OpenAlex/arXiv retrieval calls.

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
2. Map `SearchServiceOutput` into the existing API response contract.
3. Persist run state and progress events before exposing it to the frontend.
4. Keep Mock API as the stable frontend demo path until the service path is
   tested with real connector latency and failures.
5. Add SSE events around every pipeline stage before enabling UI integration.

## Current Non-goals

- No LLM query understanding.
- No LLM judgement.
- No LLM reranking.
- No LLM Query Evolution.
- No RefChain execution.
- No external search in tests.
- No replacement of Mock API routes.
- No frontend changes.
- No `third_party` changes.

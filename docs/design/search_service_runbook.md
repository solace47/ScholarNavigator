# Search Service Runbook

## Scope

This runbook covers the internal no-LLM `SearchService` pipeline.

Current boundaries:

- No LLM calls.
- No FastAPI Mock API replacement.
- No frontend changes.
- No `third_party` changes.
- No RefChain execution.
- No Query Evolution execution.

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

## Pipeline

Execution order:

```text
query
  -> analyze_query
  -> retrieve_papers for each SearchSubquery
  -> aggregate papers
  -> deduplicate_papers across subqueries
  -> judge_papers
  -> rerank_papers
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

## Output

`SearchServiceOutput` contains:

- `search_plan`
- `retrieval_outputs`
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

Do not wire this directly into the existing Mock API yet.

Recommended next steps:

1. Add a backend feature flag or a separate endpoint for real internal search.
2. Map `SearchServiceOutput` into the existing API response contract.
3. Persist run state and progress events before exposing it to the frontend.
4. Keep Mock API as the stable frontend demo path until the service path is
   tested with real connector latency and failures.
5. Add SSE events around every pipeline stage before enabling UI integration.

## Current Non-goals

- No LLM query understanding.
- No LLM judgement.
- No LLM reranking.
- No external search in tests.
- No API route changes.
- No frontend changes.
- No `third_party` changes.


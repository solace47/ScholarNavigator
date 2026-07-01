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
- Citation-backed synthesis is available as a no-LLM final stage when
  `enable_synthesis=True`; it is enabled by default.

The default service can call real retrieval connectors through `retrieve_papers`.
When RefChain is enabled, the default service can call OpenAlex reference
fetching through `fetch_openalex_references`. Unit tests inject fake retrievers
and fake reference fetchers and do not access the network.

The default retriever includes a process-local in-memory cache for detailed
OpenAlex/arXiv connector results. Cache hits are represented in
`SourceStats.cache_hit` and can be surfaced by API mappers as
`cost_report.cache_hit_count`.

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
    enable_synthesis=True,
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
  -> optional synthesize_answer
  -> SearchServiceOutput
```

Details:

- `analyze_query` generates a `SearchPlan`.
- Each `SearchSubquery` is sent to `retrieve_papers`.
- `retrieve_papers` returns a `RetrievalOutput` per subquery.
- `retrieve_papers` may return cached per-source connector results when the
  same `source + query + limit_per_source` was recently requested.
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
- If `enable_synthesis=True`, `synthesize_answer` runs after the final rerank
  and stores a `SynthesisOutput` on `SearchServiceOutput.synthesis_output`.

When `enable_query_evolution=False`, the service keeps the original one-pass
behavior and `query_evolution_records` is empty.

When `enable_refchain=False`, the service does not call the reference fetcher
and `refchain_output` is `None`.

When `enable_synthesis=False`, the service skips synthesis and
`synthesis_output` is `None`.

## Synthesis

The final synthesis stage is no-LLM and citation-backed. It calls:

```python
from scholar_agent.agents.synthesis import synthesize_answer
```

Inputs:

- final `ranked_papers`
- ranked-paper evidence rows
- `SearchServiceOutput.warnings`
- `source_stats`
- optional `refchain_output`

Behavior:

- Runs after all optional Query Evolution and RefChain reranking is complete.
- Uses only evidence sources `title`, `abstract`, `venue`, and `metadata`.
- Generates deterministic citation keys such as `R1`, `R2`, and `R3`.
- Adds source warnings/errors to synthesis limitations.
- Returns insufficient evidence rather than fabricating conclusions when no
  valid evidence rows exist.
- Does not call LLMs, read PDFs, or access the network.

API boundary:

- The internal raw preview endpoint can show `synthesis_output`.
- The `/api/v1/internal/search/preview/api-result` mapper does not expose a
  synthesis field yet.
- Existing `/api/v1/search/runs` Mock API behavior is unchanged.

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

`source_stats` preserve each retriever-emitted `SourceStats.cache_hit` value.
This allows downstream API mapping to report cache effectiveness without
changing the meaning of `search_api_call_count`, which still counts source
attempt records represented in `source_stats`.

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

The default retriever cache can be configured with:

```bash
SCHOLAR_AGENT_RETRIEVAL_CACHE=0
SCHOLAR_AGENT_RETRIEVAL_CACHE_TTL_SECONDS=900
SCHOLAR_AGENT_RETRIEVAL_CACHE_MAX_ENTRIES=256
```

Failed connector results with `error_message` are not cached, so temporary
OpenAlex/arXiv failures can be retried by later SearchService calls.

`SearchService` also accepts a reference fetcher:

```python
def reference_fetcher(paper: Paper, limit: int = 20) -> list[Paper]:
    ...
```

The default is `fetch_openalex_references`. Tests should inject a fake
reference fetcher whenever `enable_refchain=True`.

## Batch Search CLI

`scripts/run_search_batch.py` runs the same internal `SearchService` pipeline
over a local JSONL query file and writes one JSON object per input case. It does
not change the Mock API, Real Search API, or frontend behavior.

Example input:

```json
{"case_id":"case_001","query":"latest LLM reranking methods for scientific literature retrieval","top_k":10,"run_profile":"balanced","current_year":2026,"enable_query_evolution":true,"enable_refchain":false}
{"query":"survey of agentic scientific paper search","top_k":5}
```

Required field:

- `query`

Optional per-row fields:

- `case_id`; missing or blank values become `row_1`, `row_2`, and so on.
- `top_k`
- `run_profile`
- `current_year`
- `enable_query_evolution`
- `enable_refchain`

Run:

```bash
PYTHONPATH=src python scripts/run_search_batch.py \
  --input datasets/my_queries.jsonl \
  --output outputs/batch_runs/result.jsonl \
  --top-k 10 \
  --run-profile balanced \
  --current-year 2026 \
  --enable-query-evolution \
  --max-workers 2
```

CLI defaults are used when a row omits the corresponding field. Per-row fields
override CLI defaults. `--max-workers` is passed to
`SearchService(max_workers=...)`.

Each output line has the shape:

```json
{"case_id":"case_001","query":"...","status":"succeeded","result":{ "...": "SearchRunResultResponse JSON" },"error":null,"latency_seconds":1.23}
```

Succeeded rows run `SearchService.run_search(..., enable_synthesis=True)` and
map the internal output through `map_search_service_output_to_api_result` with a
debug run id in the form `batch_{case_id}`.

Error behavior:

- The default behavior is continue-on-error. A failed row writes
  `status="failed"`, `result=null`, and the error message, then the batch keeps
  processing later rows.
- Use `--fail-fast` to stop after the first per-row failure and return non-zero.
- Empty or missing `query` is treated as a per-row failure.
- Missing input files, non-file input paths, malformed JSONL, or non-object
  JSONL rows return non-zero before any SearchService calls.

Manual runs may access OpenAlex/arXiv through the default retriever and OpenAlex
reference metadata when `enable_refchain=True`. Tests monkeypatch
`SearchService` and do not access the network.

### Batch Summary CLI

`scripts/summarize_search_batch.py` reads the JSONL produced by
`run_search_batch.py` and renders a Markdown report. It does not call
`SearchService`, access the network, or change API/frontend behavior.

Print to stdout:

```bash
PYTHONPATH=src python scripts/summarize_search_batch.py \
  --input outputs/batch_runs/result.jsonl
```

Write to a Markdown file:

```bash
PYTHONPATH=src python scripts/summarize_search_batch.py \
  --input outputs/batch_runs/result.jsonl \
  --output outputs/batch_runs/summary.md \
  --top-n 10
```

The report includes:

- total cases, succeeded/failed counts, and success rate
- latency average/min/max
- total API calls, search API calls, cache hits, and estimated token counts
- per-case counts for highly relevant and partially relevant papers
- synthesis status per case
- top queries by latency
- top papers by repeated title
- missing evidence / warning counts
- separate `source_error...` counts
- failed cases with query and error

Invalid input paths, malformed JSONL, or non-object JSONL rows return non-zero.
Rows with `status="succeeded"` and `result=null` are summarized as zero-paper
rows and add a `succeeded_result_missing` warning count.

### Batch Evaluation CLI

`scripts/evaluate_search_batch.py` reads the JSONL produced by
`run_search_batch.py` plus a local gold/qrels JSONL file and computes ranking
metrics. It only reads local files and reuses the project evaluation metrics; it
does not run SearchService or change API/frontend behavior.

Gold/qrels JSONL format:

```json
{"case_id":"case_001","relevant_papers":[{"title":"...","year":2025,"doi":"10.xxxx/example","arxiv_id":"2501.00001","openalex_id":"W123","semantic_scholar_id":"S2...","pubmed_id":"PMID..."}]}
```

Run:

```bash
PYTHONPATH=src python scripts/evaluate_search_batch.py \
  --batch-results outputs/batch_runs/result.jsonl \
  --gold datasets/my_gold/qrels.jsonl \
  --output outputs/batch_runs/eval.json \
  --k 5 \
  --k 10 \
  --include-partial
```

By default, the ranked list uses only `highly_relevant_papers`. The
`--include-partial` flag appends `partially_relevant_papers`.

Metrics:

- Recall@K
- Precision@K
- MRR
- nDCG@K

Failed rows are reported in `failed_cases` and excluded from metric averages.
Batch rows without gold are reported in `missing_gold_cases`. Gold cases absent
from the batch output are reported in `missing_result_cases`.

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

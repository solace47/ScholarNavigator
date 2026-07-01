# Evaluation Runbook

## Scope

This runbook covers the offline evaluation primitives:

- `src/scholar_agent/core/evaluation_schemas.py`
- `src/scholar_agent/evaluation/metrics.py`
- `src/scholar_agent/evaluation/offline_evaluator.py`

Current boundaries:

- SearchService is only used through injected fake fixtures.
- No real OpenAlex, arXiv, Semantic Scholar, PubMed, or LLM calls.
- No frontend changes.
- No API changes.
- No `third_party` changes.

## Schemas

`EvalGoldPaper` stores one gold paper with optional identifiers:

- DOI
- arXiv ID
- OpenAlex ID
- Semantic Scholar ID
- PubMed ID
- title and year fallback
- `relevance_grade`

`EvalQuery` stores one offline evaluation case:

- `query_id`
- `query`
- `gold_papers`
- `top_k_values`, defaulting to `[5, 10, 20]`
- optional run metadata such as `run_profile` and `current_year`

`EvalMetricSet` stores metric summaries for future evaluator output. The current
metric helpers return plain floats or dictionaries, and a future evaluator can
assemble them into this schema.

`EvalGroupResult` stores one query under one feature group:

- `baseline`
- `query_evolution`
- `refchain`

It includes the metric set, canonical ranked paper IDs, warnings, source stats,
candidate counts, latency, and failure information.

`EvalSuiteResult` stores all query results plus aggregate metrics per feature
group.

## Canonical Paper Matching

`canonical_paper_id` uses the following priority:

1. DOI, lowercased and stripped of DOI URL prefixes.
2. arXiv ID, lowercased and stripped of version suffixes such as `v1`.
3. OpenAlex ID.
4. Semantic Scholar ID.
5. PubMed ID.
6. normalized `title + year` fallback.

The function accepts project `Paper`, `RankedPaper`, `EvalGoldPaper`, or mapping
objects. This keeps tests and future fixture loaders simple.

## Metrics

Implemented metrics:

- `recall_at_k`
- `precision_at_k`
- `mrr`
- `ndcg_at_k`
- `candidate_count_metrics`
- `error_rate_metrics`

All metrics are deterministic and handle empty gold sets or empty ranked lists
without raising.

`ndcg_at_k` supports both binary relevance and graded relevance through
`EvalGoldPaper.relevance_grade`. The planned default cutoffs are `K=5`, `K=10`,
and `K=20`.

## Candidate And Error Metrics

`candidate_count_metrics` reports:

- raw candidate count
- deduplicated count
- ranked count
- duplicate count
- duplicate ratio
- returned count by source

`error_rate_metrics` reports:

- source call count
- source error count
- source error rate
- warning count
- query warning rate
- failed case rate

These fields consume `SearchServiceOutput.source_stats` and
`SearchServiceOutput.warnings` in the offline evaluator. The pure metric module
does not import or call `SearchService`.

## Offline SearchService Evaluator

Use `evaluate_search_service_offline` with injected fixtures:

```python
from scholar_agent.evaluation.offline_evaluator import evaluate_search_service_offline

result = evaluate_search_service_offline(
    eval_queries,
    retriever=fake_retriever,
    reference_fetcher=fake_reference_fetcher,
)
```

Default comparison groups:

| Group | Query Evolution | RefChain |
| --- | --- | --- |
| `baseline` | off | off |
| `query_evolution` | on | off |
| `refchain` | on | on |

The evaluator creates a `SearchService` with the provided fake retriever and fake
reference fetcher for every group run. It does not use the default real
`retrieve_papers` or `fetch_openalex_references` paths.

The returned `EvalSuiteResult` is structured and does not print:

- `query_results[*].group_results[group].metrics`
- `query_results[*].group_results[group].ranked_paper_ids`
- `query_results[*].group_results[group].warnings`
- `query_results[*].group_results[group].source_stats`
- `aggregate_metrics[group]`

If a group run raises, the evaluator records a failed `EvalGroupResult` with
zero ranking metrics and `failed_case_rate=1.0`; other groups and queries can
continue.

## Fixture Requirements

Fake retrievers should return deterministic `RetrievalOutput` objects:

- papers
- source stats
- warnings
- latency

Fake reference fetchers should return deterministic `Paper` objects and should
never call OpenAlex. To verify no real connector is used, tests can pass fake
functions that record calls or raise if called in the wrong group.

The evaluator does not depend on LitSearch or AstaBench real data. Those
datasets remain design references only until a local fixture snapshot is
explicitly prepared.

## Next Integration Step

The next evaluation implementation should add artifact persistence and reporting:

1. Load fixture queries and gold papers from JSONL.
2. Load retrieval/reference fixture maps from disk.
3. Persist raw `SearchServiceOutput` artifacts.
4. Score saved artifacts using the pure metrics in this module.
5. Generate Markdown/CSV comparison tables.

Tests for that layer must continue to block network access and must not call
real connectors.

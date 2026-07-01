# Evaluation Runbook

## Scope

This runbook covers the offline evaluation primitives:

- `src/scholar_agent/core/evaluation_schemas.py`
- `src/scholar_agent/evaluation/metrics.py`
- `src/scholar_agent/evaluation/offline_evaluator.py`
- `src/scholar_agent/evaluation/fixture_loader.py`
- `scripts/eval_search_service.py`
- `scripts/summarize_eval_results.py`

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

## Fixture Loader

The conventional local fixture directory contains:

```text
datasets/eval_fixtures/<name>/
  search_cases.jsonl
  retrieval_outputs.json
  reference_outputs.json
```

`search_cases.jsonl` stores one `EvalQuery` per line.

`retrieval_outputs.json` can store either:

- `{ "outputs": [RetrievalOutput, ...] }`
- a mapping of query string to `RetrievalOutput` payload

The loader normalizes query keys by lowercasing and collapsing whitespace. If a
query is missing, the generated fake retriever returns an empty
`RetrievalOutput` with a `fixture_missing_retrieval:<query>` warning and source
error message. This keeps offline evaluation observable and prevents accidental
fallback to real connectors.

`reference_outputs.json` can store either:

- `{ "references": [{ "seed_id": "doi:...", "papers": [Paper, ...] }] }`
- a mapping of canonical seed ID to paper list

Reference seed IDs should use the same canonical format as
`canonical_paper_id`, for example `doi:10.123/example`.

The committed sample fixture is intentionally tiny and handwritten:

```text
datasets/eval_fixtures/sample/
```

It is only for smoke testing the evaluator and scripts. It is not LitSearch or
AstaBench data.

## Scripts

Run the sample offline evaluation:

```bash
PYTHONPATH=src python scripts/eval_search_service.py \
  --fixtures-dir datasets/eval_fixtures/sample \
  --output-root outputs/eval_runs \
  --run-id sample
```

This writes:

```text
outputs/eval_runs/sample/result.json
```

Generate a Markdown summary:

```bash
PYTHONPATH=src python scripts/summarize_eval_results.py \
  outputs/eval_runs/sample/result.json
```

This writes:

```text
outputs/eval_runs/sample/summary.md
```

The summary compares `baseline`, `query_evolution`, and `refchain` on:

- Recall@5/10/20
- Precision@5/10/20
- MRR
- nDCG@5/10/20
- raw and deduplicated candidate counts
- warning count
- source error rate

## Next Integration Step

The next evaluation implementation should add artifact persistence and reporting:

1. Add richer offline fixtures covering multiple query types.
2. Persist raw `SearchServiceOutput` artifacts in addition to scored summaries.
3. Add CSV output for spreadsheet comparison.
4. Add a regression threshold file for CI.

Tests for that layer must continue to block network access and must not call
real connectors.

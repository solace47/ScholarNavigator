# Evaluation Runbook

## Scope

This runbook covers the first offline evaluation primitives:

- `src/scholar_agent/core/evaluation_schemas.py`
- `src/scholar_agent/evaluation/metrics.py`

Current boundaries:

- No SearchService integration.
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

These fields are designed to consume future `SearchServiceOutput.source_stats`
and `SearchServiceOutput.warnings`, but this module does not import or call
`SearchService`.

## Next Integration Step

The next evaluation implementation should add an offline evaluator that:

1. Loads fixture queries and gold papers.
2. Injects fake retriever/reference fetcher into `SearchService`.
3. Runs `baseline`, `query_evolution`, and `refchain` groups.
4. Persists raw run artifacts.
5. Scores saved artifacts using the pure metrics in this module.

Tests for that layer must continue to block network access and must not call
real connectors.

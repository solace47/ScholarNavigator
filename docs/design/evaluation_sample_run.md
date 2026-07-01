# Evaluation Sample Run

## Scope

This document records one sample offline evaluation run for reporting and demo
purposes.

Run date: 2026-07-01

Important boundaries:

- This uses a small handwritten fake fixture.
- This is not a complete LitSearch or AstaBench benchmark.
- No real OpenAlex, arXiv, Semantic Scholar, PubMed, or LLM calls were made.
- No frontend, `third_party`, or API files were changed.

## Fixture

Fixture directory:

```text
datasets/eval_fixtures/sample
```

Fixture files:

- `datasets/eval_fixtures/sample/search_cases.jsonl`
- `datasets/eval_fixtures/sample/retrieval_outputs.json`
- `datasets/eval_fixtures/sample/reference_outputs.json`

The sample contains one query:

```text
latest LLM reranking methods for scientific literature retrieval
```

The gold set contains three fake papers:

- `doi:10.123/baseline`
- `doi:10.123/evolved`
- `doi:10.123/refchain`

## Commands

Evaluation command:

```bash
PYTHONPATH=src python scripts/eval_search_service.py \
  --fixtures-dir datasets/eval_fixtures/sample \
  --output-root outputs/eval_runs \
  --run-id sample
```

Summary command:

```bash
PYTHONPATH=src python scripts/summarize_eval_results.py \
  outputs/eval_runs/sample/result.json
```

Generated files:

- `outputs/eval_runs/sample/result.json`
- `outputs/eval_runs/sample/summary.md`

## Metrics

| Group | Recall@5 | Recall@10 | Recall@20 | Precision@5 | Precision@10 | Precision@20 | MRR | nDCG@5 | nDCG@10 | nDCG@20 | raw_count | deduplicated_count | warning_count | source_error_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 0.333 | 0.333 | 0.333 | 0.200 | 0.100 | 0.050 | 1.000 | 0.542 | 0.542 | 0.542 | 3 | 1 | 1 | 0.333 |
| query_evolution | 0.667 | 0.667 | 0.667 | 0.400 | 0.200 | 0.100 | 1.000 | 0.884 | 0.884 | 0.884 | 6 | 2 | 1 | 0.167 |
| refchain | 1.000 | 1.000 | 1.000 | 0.600 | 0.300 | 0.150 | 1.000 | 1.000 | 1.000 | 1.000 | 8 | 3 | 1 | 0.143 |

## Ranked IDs

| Group | Top ranked IDs | Warnings |
| --- | --- | --- |
| baseline | `doi:10.123/baseline` | `fixture simulated source warning` |
| query_evolution | `doi:10.123/baseline`, `doi:10.123/evolved` | `fixture simulated source warning` |
| refchain | `doi:10.123/baseline`, `doi:10.123/evolved`, `doi:10.123/refchain` | `fixture simulated source warning` |

## Interpretation

In this sample fixture:

- Baseline finds only the baseline gold paper, so Recall@20 is `0.333`.
- Query Evolution introduces the evolved fake paper, improving Recall@20 to
  `0.667` and nDCG@20 to `0.884`.
- RefChain adds the reference fake paper, improving Recall@20 and nDCG@20 to
  `1.000`.
- Candidate count grows from `3 raw / 1 deduplicated` to
  `8 raw / 3 deduplicated`.
- The warning and source error fields remain visible across all groups, showing
  that fixture-level connector observability is preserved in the offline report.

Conclusion: Query Evolution and RefChain improve the sample fixture results.
This conclusion only validates the offline evaluator flow and feature-group
comparison mechanics; it is not evidence of benchmark performance on real
LitSearch, AstaBench, or contest data.

# Evaluation Design

## Scope

This document designs the offline evaluation plan for the current no-LLM
backend search pipeline. It is a design-only document:

- No business code is implemented in this round.
- No FastAPI API contract is changed.
- No frontend files are changed.
- No `third_party` files are changed.
- No LLM calls or external network access are required for the proposed MVP
  evaluation path.

The target system under evaluation is `SearchService`, including the existing
optional feature flags:

- `baseline`: `enable_query_evolution=false`, `enable_refchain=false`
- `query_evolution`: `enable_query_evolution=true`, `enable_refchain=false`
- `refchain`: `enable_query_evolution=true`, `enable_refchain=true`

The `refchain` group follows the current preview validation convention: Query
Evolution first expands the query set, then RefChain expands citations from the
ranked seeds.

## Signals From LitSearch

The local `datasets/LitSearch` checkout contains benchmark code and README, but
not the downloaded HuggingFace query/corpus artifacts. The README describes 597
realistic literature search queries for recent ML/NLP papers, plus separate
`query`, `corpus_clean`, and `corpus_s2orc` dataset configurations.

LitSearch is useful for this project in four ways:

1. Query set design
   - It provides realistic natural-language literature search needs.
   - The queries are close to the contest task: complex academic search rather
     than keyword-only web search.

2. Retrieval metrics
   - `datasets/LitSearch/utils/utils.py` includes `calculate_recall` and
     `calculate_ndcg`.
   - The retrieval script writes per-query ranked `retrieved` lists, which maps
     directly to our `ranked_papers`.
   - For our MVP evaluator, LitSearch-style ranked output can support
     `Recall@K`, `Precision@K`, `MRR`, and `nDCG@K` when a local qrels/gold set
     is available.

3. Reranking evaluation shape
   - `eval/reranking/rerank.py` reranks a fixed candidate list and preserves
     the original retrieval result file as the artifact boundary.
   - For this project, the equivalent boundary should be a serialized
     `SearchServiceOutput` plus derived metrics. The evaluator should not call
     the live preview endpoint.

4. One-hop expansion comparison
   - `eval/onehop/get_onehop_union.py` expands the top retrieved papers with
     citations before reranking.
   - This is directly relevant to our single-layer RefChain flag. We should
     evaluate whether RefChain improves recall without flooding the candidate
     pool or hurting rank quality.

LitSearch should not be copied as-is. Its GPT-based reranking is out of scope
for the current no-LLM backend, and the local checkout does not include enough
data to run a complete LitSearch benchmark offline without first preparing a
local fixture snapshot.

## Signals From AstaBench

AstaBench is broader than this project, but its PaperFindingBench and tooling
provide several design patterns worth borrowing.

Useful ideas:

1. Task / solver / scorer separation
   - AstaBench treats a benchmark as dataset plus scorer, and a solver as the
     agent implementation.
   - Our MVP should similarly separate `EvalCase`, `SearchService` execution,
     and metric calculation.

2. Structured output as the scoring boundary
   - PaperFindingBench expects ordered result objects with paper IDs and
     evidence snippets.
   - Our `RankedPaper` already has rank, score, paper metadata, ranking reason,
     and judgement evidence. Evaluation should consume this structured output
     rather than unstructured text.

3. Ranking matters
   - PaperFindingBench explicitly states that order matters.
   - Its scoring code uses recall and a rank-sensitive score. Our MVP should
     report both set-based metrics and rank-sensitive metrics.

4. Different query types need different scoring assumptions
   - PaperFindingBench distinguishes specific/navigational, metadata, semantic,
     and LitQA2-derived search tasks.
   - Our evaluator should support at least `known_paper`, `metadata`, and
     `semantic` query categories, even if MVP fixtures initially use only binary
     relevance labels.

5. Cost and reliability are part of evaluation
   - AstaBench emphasizes cost logging and fair tool constraints.
   - Our SearchService already returns `latency_seconds`, `source_stats`,
     `warnings`, `raw_count`, and `deduplicated_count`; these should be first
     class evaluation metrics, not debug-only fields.

6. Date/corpus constraints
   - AstaBench literature tools use date restrictions to make historical tasks
     reproducible.
   - Our offline evaluation should pin `current_year`, fixture versions, and
     corpus snapshots so live database drift cannot change scores.

7. Solve then score
   - AstaBench supports a decoupled solve/score flow with logs as stable
     artifacts.
   - Our evaluator should write raw run artifacts first, then compute metrics
     from those artifacts. This makes metric changes auditable.

Not adopted in MVP:

- AstaBench's LLM relevance judgement path.
- InspectAI integration.
- leaderboard packaging.
- remote Asta MCP tools.
- scoring with external Semantic Scholar calls.

## MVP Evaluation Goals

The current project should evaluate these backend abilities first:

1. Query understanding stability
   - The same query and `current_year` produce the same `SearchPlan`.
   - `run_profile`, `enable_query_evolution`, and `enable_refchain` are recorded
     in run artifacts.

2. Candidate acquisition
   - Enough candidates are retrieved for each query.
   - Connector or fixture failures are reflected in `source_stats` and
     `warnings`.
   - `raw_count`, `deduplicated_count`, and duplicate ratio are tracked.

3. Deduplication quality proxy
   - Relevant papers from multiple sources collapse to one candidate.
   - Duplicate merging does not drop identifiers, sources, or useful metadata.

4. Judgement and ranking quality
   - Relevant papers appear in top ranks.
   - Irrelevant high-citation papers do not dominate the ranking.
   - Evidence and ranking reasons are present for ranked papers.

5. Feature-flag impact
   - Query Evolution should improve candidate coverage or top-k quality enough
     to justify extra calls.
   - RefChain should improve recall when seed papers have supported identifiers,
     while keeping latency and candidate growth bounded.

6. Operational robustness
   - Per-source error rate and warning rate are visible.
   - Failed subqueries, evolved queries, or RefChain seeds do not fail the whole
     suite.

## Comparison Groups

Each eval case should be run with the same query, `top_k`, `run_profile`,
`current_year`, offline retriever fixture, and offline reference fixture.

| Group | Query Evolution | RefChain | Purpose |
| --- | --- | --- | --- |
| `baseline` | off | off | Measures the base pipeline: query understanding, retrieval, dedup, judgement, rerank. |
| `query_evolution` | on | off | Measures whether evolved queries add relevant candidates and improve ranking. |
| `refchain` | on | on | Measures whether citation expansion improves recall after query evolution. |

Optional diagnostic group:

| Group | Query Evolution | RefChain | Purpose |
| --- | --- | --- | --- |
| `refchain_only` | off | on | Isolates citation expansion from evolved query effects. This is useful for debugging but should not be the primary contest comparison group unless needed. |

The existing manual validation docs show why this split matters:

- `feature_flag_preview_validation.md` showed Query Evolution increasing raw and
  deduplicated candidate counts for the sample query.
- `connector_observability_validation.md` showed OpenAlex 503 errors surfacing
  in `source_stats.error_message` and top-level `warnings`.

The evaluator should preserve those fields so metric tables explain both
quality and failure modes.

## Metrics

All ranking metrics operate on canonical paper IDs. The canonical ID should use
DOI first, then arXiv ID without version, then OpenAlex ID, Semantic Scholar ID,
PubMed ID, and finally normalized title plus year for local fixtures.

### Recall@K

```text
Recall@K = relevant papers in top K / total relevant papers for the query
```

Use when the fixture contains a finite gold set. Report at `K=5`, `K=10`, and
`K=20` by default.

### Precision@K

```text
Precision@K = relevant papers in top K / K
```

Use to detect candidate flooding from Query Evolution or RefChain.

### MRR

```text
MRR = 1 / rank of the first relevant paper
```

Use especially for known-paper or navigational queries.

### nDCG@K

```text
DCG@K = sum((2^rel_i - 1) / log2(i + 1))
nDCG@K = DCG@K / ideal DCG@K
```

If labels are binary, use `rel_i=1` for relevant and `0` for not relevant. If
later fixtures include graded labels, map them to:

- `3`: highly relevant
- `2`: partially relevant
- `1`: weakly relevant
- `0`: irrelevant or insufficient evidence

### Latency

Track:

- `SearchServiceOutput.latency_seconds`
- optional wall-clock runtime measured by the evaluator
- per-source latency from `source_stats`

Report median, p90, and max per suite.

### Error Rate

Recommended fields:

```text
source_error_rate = source_stats with error_message / total source_stats
query_warning_rate = cases with warnings / total cases
failed_case_rate = cases where SearchService raised / total cases
```

Offline fixture tests should include controlled failures so warning aggregation
is covered.

### Candidate Count

Track:

- `raw_count`
- `deduplicated_count`
- `duplicate_ratio = 1 - deduplicated_count / raw_count`
- per-source returned count
- Query Evolution generated query count
- RefChain seed count and reference count

This is the main guardrail against high-recall strategies that generate noisy
or expensive candidate pools.

## Offline Evaluation Strategy

The MVP evaluator should avoid real OpenAlex, arXiv, Semantic Scholar, PubMed,
or LLM calls.

Recommended flow:

1. Build or hand-author a small fixture suite
   - `query_id`
   - `query`
   - `top_k`
   - `run_profile`
   - `current_year`
   - `gold_papers`
   - optional graded relevance labels
   - optional expected metadata constraints

2. Build offline retrieval fixtures
   - Map `(subquery, sources, limit_per_source)` or normalized query text to a
     deterministic `RetrievalOutput`.
   - Include successful OpenAlex/arXiv-like outputs and failure cases.
   - Include duplicates across subqueries and sources.

3. Build offline reference fixtures
   - Map seed canonical paper ID to a deterministic list of reference `Paper`
     objects.
   - Include seeds with missing identifiers, seeds with references, and seeds
     whose reference fetch fails.

4. Run SearchService with dependency injection
   - Inject fake `retriever`.
   - Inject fake `reference_fetcher`.
   - Use fixed `current_year`.
   - Use `max_workers=1` for strict trace debugging and `max_workers=4` for
     concurrency regression tests.

5. Persist artifacts
   - Write one JSON result per `(eval_case, group)`.
   - Include full `SearchServiceOutput`.
   - Include evaluator version, fixture version, git SHA, Python version, and
     run config.

6. Score from artifacts
   - Metric calculation should not rerun SearchService.
   - This allows scorer changes without changing solve outputs.

This strategy mirrors AstaBench's solve/score separation while staying small
and repo-native.

## Proposed Files For A Future Implementation

No files below are created in this round. They are proposed for the next coding
phase.

Schemas:

- `src/scholar_agent/core/evaluation_schemas.py`
  - `EvalQuery`
  - `EvalGoldPaper`
  - `EvalRunConfig`
  - `EvalGroupResult`
  - `EvalMetricSet`
  - `EvalSuiteResult`

Evaluator modules:

- `src/scholar_agent/evaluation/metrics.py`
  - `recall_at_k`
  - `precision_at_k`
  - `mrr`
  - `ndcg_at_k`
  - `candidate_count_metrics`
  - `error_rate_metrics`
- `src/scholar_agent/evaluation/offline_fixtures.py`
  - load eval cases
  - load retrieval fixtures
  - load reference fixtures
  - canonicalize paper IDs
- `src/scholar_agent/evaluation/search_service_evaluator.py`
  - run baseline / query_evolution / refchain groups
  - persist raw artifacts
  - score saved artifacts

Scripts:

- `scripts/eval_search_service.py`
  - runs the offline suite and writes artifacts.
- `scripts/summarize_eval_results.py`
  - prints Markdown/CSV comparison tables.
- `scripts/build_litsearch_snapshot.py`
  - future helper for converting downloaded LitSearch records into local
    fixtures. This script should not download data during normal tests.

Tests:

- `tests/test_evaluation_metrics.py`
  - validates metric formulas, empty gold sets, ties, and graded labels.
- `tests/test_offline_search_evaluator.py`
  - validates group comparison and artifact output with fake retriever/fetcher.
- `tests/test_litsearch_fixture_loader.py`
  - validates local fixture parsing only; no HuggingFace or network calls.
- `tests/test_evaluation_no_network.py`
  - monkeypatches connector entry points to fail if called.

Optional fixture directories:

- `datasets/eval_fixtures/search_cases.jsonl`
- `datasets/eval_fixtures/retrieval_outputs/*.json`
- `datasets/eval_fixtures/reference_outputs/*.json`
- `outputs/eval_runs/<run_id>/`

The exact fixture location should be decided before implementation. Large or
generated artifacts should not be committed unless they are intentionally small.

## Data Model Notes

`EvalGoldPaper` should store multiple identifiers when available:

- DOI
- arXiv ID
- OpenAlex ID
- Semantic Scholar corpus ID
- PubMed ID
- normalized title
- year
- relevance grade

This allows the evaluator to match papers even when OpenAlex and arXiv use
different IDs for the same work.

`EvalGroupResult` should include both quality and cost fields:

- `metrics`
- `raw_count`
- `deduplicated_count`
- `source_stats`
- `warnings`
- `latency_seconds`
- `query_evolution_records`
- `refchain_output`

## Current Non-Goals

The MVP evaluation should not include:

- LLM-as-judge.
- manual annotation platform.
- large-scale online evaluation against live OpenAlex/arXiv.
- leaderboard submission.
- InspectAI integration.
- PaSa-style RL/SFT/PPO evaluation.
- full-text PDF evidence scoring.
- frontend UX evaluation.
- answer synthesis or citation attribution scoring.

These can be revisited after the backend search API replaces or augments the
Mock API path.

## Risks And Controls

| Risk | Control |
| --- | --- |
| Live academic APIs drift or fail. | Use offline retriever and reference fixtures for scoring. |
| Query Evolution improves recall by flooding candidates. | Report Precision@K, nDCG@K, raw/deduplicated counts, and duplicate ratio. |
| RefChain adds noisy references. | Track reference count, seed count, source stats, and rank-sensitive metrics. |
| Identifier mismatch hides relevant papers. | Use canonical ID matching with DOI/arXiv/OpenAlex/S2/PubMed/title-year fallback. |
| Metrics become hard to reproduce. | Persist solve artifacts and score from artifacts only. |
| Performance regressions are hidden by averages. | Report median, p90, max latency, source error rate, and warning rate. |

## Actual Reference Paths

LitSearch:

- `datasets/LitSearch/README.md`
- `datasets/LitSearch/eval/retrieval/evaluate_index.py`
- `datasets/LitSearch/eval/retrieval/build_index.py`
- `datasets/LitSearch/eval/retrieval/kv_store.py`
- `datasets/LitSearch/eval/reranking/rerank.py`
- `datasets/LitSearch/eval/onehop/get_onehop_union.py`
- `datasets/LitSearch/utils/utils.py`

AstaBench:

- `third_party/asta-bench/README.md`
- `third_party/asta-bench/astabench/config/v1.0.0.yml`
- `third_party/asta-bench/astabench/cli.py`
- `third_party/asta-bench/scripts/eval_then_score.sh`
- `third_party/asta-bench/scripts/summarize_scores.py`
- `third_party/asta-bench/astabench/evals/paper_finder/README.md`
- `third_party/asta-bench/astabench/evals/paper_finder/data.json`
- `third_party/asta-bench/astabench/evals/paper_finder/datamodel.py`
- `third_party/asta-bench/astabench/evals/paper_finder/eval.py`
- `third_party/asta-bench/astabench/evals/paper_finder/task.py`
- `third_party/asta-bench/astabench/evals/paper_finder/relevance.py`
- `third_party/asta-bench/astabench/evals/paper_finder/paper_finder_utils.py`
- `third_party/asta-bench/astabench/tools/search.py`
- `third_party/asta-bench/astabench/tools/submission.py`
- `third_party/asta-bench/astabench/tools/README.md`
- `third_party/asta-bench/astabench/evals/sqa/precision_eval.py`
- `third_party/asta-bench/astabench/evals/sqa/citation_eval.py`

Current project:

- `docs/design/search_service_runbook.md`
- `docs/design/feature_flag_preview_validation.md`
- `docs/design/connector_observability_validation.md`
- `src/scholar_agent/services/search_service.py`

## Files Not Readable Or Not Present

No referenced local file failed to read.

However, the following data artifacts were not present in the local checkout and
were not downloaded because this round forbids external network access:

- LitSearch HuggingFace `query`, `corpus_clean`, and `corpus_s2orc` data.
- AstaBench full HuggingFace PaperFindingBench validation/test data.
- AstaBench downloaded normalizer reference artifacts used by its semantic
  PaperFindingBench scorer.

The design above therefore treats LitSearch and AstaBench as reference
frameworks and proposes a local fixture layer before running any reproducible
project-specific evaluation.

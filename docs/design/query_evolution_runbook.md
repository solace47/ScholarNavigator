# Query Evolution Runbook

## Scope

`QueryEvolutionAgent` is a deterministic, no-LLM query expansion component. It
generates evolved search queries from already judged and ranked papers.
It is available as an optional Real Search stage when
`enable_query_evolution=True`.

Current boundaries:

- No LLM calls.
- No network access.
- No RefChain.
- No frontend changes.
- No `third_party` changes.

## Module Responsibility

Files:

```text
src/scholar_agent/core/search_schemas.py
src/scholar_agent/agents/query_evolution.py
```

Public function:

```python
from scholar_agent.agents.query_evolution import evolve_queries

record = evolve_queries(
    query_analysis=search_plan.query_analysis,
    search_plan=search_plan,
    judgements=judgements,
    ranked_papers=ranked_papers,
    used_queries={subquery.query for subquery in search_plan.subqueries},
)
```

## Schemas

The internal pipeline schema now includes:

- `QueryEvolutionOptions`
- `EvolvedSubquery`
- `QueryEvolutionRecord`

`EvolvedSubquery.source_hints` is validated against the currently supported
sources:

- `openalex`
- `arxiv`
- `semantic_scholar`
- `pubmed`

## Rule-Based Seed Selection

The agent selects seed papers from:

1. `ranked_papers` in ranking order.
2. `judgements` in input order as a fallback for papers not present in
   `ranked_papers`.

Eligible seeds:

- `highly_relevant`
- `partially_relevant` with score at or above `min_seed_score`

Ineligible seeds:

- `weakly_relevant`
- `irrelevant`
- `insufficient_evidence`

Default options:

- `max_evolved_queries=3`
- `max_seed_papers=5`
- `min_seed_score=0.45`

## Query Generation Rules

The rules are intentionally conservative:

- Use `QueryAnalysis.constraints.must_include_terms`.
- Use `QueryAnalysis.constraints.methods`.
- Use `QueryAnalysis.constraints.datasets`.
- Use matched terms from judgement/reranking.
- Use short title keywords from seed papers.
- Apply simple intent templates such as `survey review`, `recent advances`,
  `comparison benchmark`, or `dataset benchmark evaluation`.

The agent does not:

- Translate long Chinese queries into complex English sentences.
- Invent methods, datasets, venues, or sources.
- Use abstracts to create long natural-language queries.
- Use random sampling.

## Deduplication

Generated queries are compared against:

- `used_queries`
- `search_plan.query_analysis.original_query`
- every `SearchPlan.subqueries[*].query`
- queries generated earlier in the same record

Deduplication is case-insensitive and collapses repeated whitespace.

If no eligible seed exists, the record contains:

```text
warnings=["no_relevant_seed"]
```

If all generated candidates are duplicates, the record contains:

```text
warnings=["no_new_evolved_query"]
```

## SearchService Integration

`QueryEvolutionAgent` only generates `EvolvedSubquery` records. It does not call
`retrieve_papers`.

SearchService integration:

1. Run the existing initial pipeline.
2. Call `evolve_queries` when `enable_query_evolution=True`.
3. Convert each `EvolvedSubquery` into a retrieval call.
4. Merge evolved retrieval outputs with initial candidates.
5. Run cross-query `deduplicate_papers`.
6. Re-run `judge_papers`.
7. Re-run `rerank_papers`.

The stage is surfaced through Real Search diagnostics and final result mapping
when enabled.

## Tests

Covered by:

```text
tests/test_query_evolution.py
```

The tests verify:

- relevant seeds produce evolved queries
- no relevant seeds produce a warning
- `used_queries` deduplication
- `max_evolved_queries`
- supported source hints only
- deterministic output
- no LLM or network dependency

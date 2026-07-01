# Reranker Runbook

## Scope

This runbook covers the deterministic no-LLM `RerankerAgent`.

Current boundaries:

- No LLM calls.
- No network access.
- No external venue or author authority database.
- No FastAPI Mock API replacement.
- No frontend changes.
- No `third_party` changes.

## Module Responsibility

Files:

```text
src/scholar_agent/core/search_schemas.py
src/scholar_agent/agents/reranker.py
prompts/reranking.md
```

`RerankerAgent` consumes `JudgementResult` objects and produces ranked
`RankedPaper` objects. It does not judge relevance itself; it preserves the
judgement category, evidence, matched terms, and warnings.

Public function:

```python
from scholar_agent.agents.reranker import rerank_papers

ranked = rerank_papers(query_analysis, judged_papers, top_k=20)
```

## Internal Schemas

`src/scholar_agent/core/search_schemas.py` defines:

- `RerankScoreBreakdown`
- `RankedPaper`

`RerankScoreBreakdown` includes:

- `relevance_score`
- `authority_score`
- `timeliness_score`
- `metadata_score`
- `final_score`
- component weights

`RankedPaper` includes:

- `rank`
- `paper`
- `final_score`
- `category`
- `score_breakdown`
- `ranking_reason`
- `evidence`
- `matched_terms`
- `warnings`

## No-LLM Reranking Rules

Signals:

- Relevance: `JudgementResult.score`.
- Authority: `citation_count`, number of sources, identifier completeness, and
  venue metadata.
- Timeliness: `Paper.year` compared with query time constraints when present;
  otherwise recency relative to the current year.
- Metadata completeness: title, abstract, year, venue, sources, identifiers.

Default weights:

```text
relevance=0.72
authority=0.13
timeliness=0.10
metadata=0.05
```

Intent-specific weights:

- `recent_progress`: increases timeliness weight.
- `survey`: increases authority weight.

Category multipliers reduce the final score for weaker categories:

- `highly_relevant`: strongest multiplier.
- `partially_relevant`: modest reduction.
- `weakly_relevant`: larger reduction.
- `irrelevant`: strong reduction.
- `insufficient_evidence`: strongest reduction.

The final score is clipped to `[0, 1]` and rounded to four decimals.

## Sorting Rules

Sorting is deterministic:

1. Category tier, so `irrelevant` and `insufficient_evidence` stay behind
   relevant categories.
2. Higher final score.
3. Higher relevance score.
4. Higher citation count.
5. Newer year.
6. Title alphabetically.
7. Original input index.

Ranks start at `1`, and `top_k` is applied after sorting.

## Ranking Reason

`ranking_reason` is generated only from supplied metadata:

- category
- judgement score
- citation count
- source count
- identifier count
- venue
- year
- intent-specific weighting note

It does not claim external venue prestige, author reputation, or any fact not
present in the paper metadata.

## Connecting to Judgement and Future API Service

Recommended service-layer flow:

```text
QueryUnderstandingAgent
  -> SearchPlan
  -> retrieve_papers for each SearchSubquery
  -> cross-subquery deduplicate_papers
  -> JudgementAgent
  -> RerankerAgent
  -> API response mapper
```

Example:

```python
judgements = judge_papers(plan.query_analysis, retrieval.papers)
ranked = rerank_papers(plan.query_analysis, judgements, top_k=plan.top_k)
```

The current FastAPI Mock API should remain unchanged until a service layer and
feature flag are added.

## Current Non-goals

- No LLM reranking.
- No pairwise comparison.
- No embedding-based ranking.
- No external citation or venue enrichment.
- No API response contract changes.
- No frontend changes.


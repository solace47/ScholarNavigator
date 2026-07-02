# Judgement Runbook

## Scope

This runbook covers the deterministic no-LLM `JudgementAgent`.

Current boundaries:

- No LLM calls.
- No network access.
- No full-text PDF reading.
- No frontend changes.
- No `third_party` changes.

## Module Responsibility

Files:

```text
src/scholar_agent/core/search_schemas.py
src/scholar_agent/agents/judgement.py
prompts/relevance_judgement.md
```

`JudgementAgent` evaluates retrieved `Paper` metadata against a `QueryAnalysis`.
It returns stable `JudgementResult` records in the same order as the input papers.

Public function:

```python
from scholar_agent.agents.judgement import judge_papers

results = judge_papers(query_analysis, papers)
```

## Internal Schemas

`src/scholar_agent/core/search_schemas.py` defines:

- `EvidenceItem`
- `JudgementResult`

`JudgementResult` includes:

- `paper`
- `score`
- `category`
- `reasoning`
- `evidence`
- `matched_terms`
- `warnings`

`EvidenceItem.source` is restricted to:

- `title`
- `abstract`
- `venue`
- `metadata`

## No-LLM Scoring Rules

The current scorer only uses:

- `QueryAnalysis.original_query`
- `QueryAnalysis.constraints`
- `QueryAnalysis.domain`
- `Paper.title`
- `Paper.abstract`
- `Paper.venue`
- `Paper.year`
- `Paper.sources`
- `Paper.identifiers`

Signals:

- Original query keyword matches.
- `must_include_terms` matches.
- `methods` matches.
- `datasets` matches.
- Title matches are weighted higher than abstract matches.
- Venue constraint match adds a small bonus.
- Time range match adds a small bonus.
- Papers clearly outside the requested time range are penalized.
- Domain terms add a small bonus when supported by title or abstract.

The score is deterministic, clipped to `[0, 1]`, and rounded to four decimals.
No random numbers are used.

## Category Rules

Default thresholds:

- `score >= 0.72` -> `highly_relevant`
- `score >= 0.45` -> `partially_relevant`
- `score >= 0.25` -> `weakly_relevant`
- `score < 0.25` -> `irrelevant`

Special case:

- If title and abstract are both empty, the category is `insufficient_evidence` regardless of score.

Thresholds can be overridden through `judge_papers(...)`, but they must satisfy:

```text
0 <= threshold_weak <= threshold_partial <= threshold_high <= 1
```

## Time Range Handling

When `query_analysis.constraints.time_range` is present:

- Paper year within range: bonus and metadata evidence.
- Paper year earlier than `start_year`: penalty and metadata evidence.
- Paper year later than `end_year`: smaller penalty and metadata evidence.
- Missing paper year: warning `missing_year_for_time_range`.

If no time range is present, year is not penalized.

## Evidence Rules

Evidence can only come from supplied metadata.

Allowed evidence:

- `title`: the paper title.
- `abstract`: a short sentence or snippet from the abstract containing a matched term.
- `venue`: the venue string when it matches a venue constraint.
- `metadata`: compact metadata such as `year=2024`.

Evidence should be short. The scorer does not copy long abstracts and does not
invent evidence from outside the metadata.

Warnings:

- `missing_title`
- `missing_abstract`
- `missing_year_for_time_range`

## Connecting to retrieve_papers and Reranker

Recommended service-layer flow:

```text
QueryUnderstandingAgent
  -> SearchPlan
  -> retrieve_papers for each SearchSubquery
  -> cross-subquery deduplicate_papers
  -> JudgementAgent
  -> RerankerAgent
```

Example:

```python
plan = analyze_query(user_query, current_year=2026)
retrieval = retrieve_papers(
    plan.subqueries[0].query,
    limit_per_source=plan.limit_per_source,
    sources=plan.selected_sources,
)
judgements = judge_papers(plan.query_analysis, retrieval.papers)
```

The future Reranker should consume `JudgementResult` rather than raw `Paper`
objects so it can use relevance score, category, evidence, and warnings.

## Current Non-goals

- No LLM relevance judgement.
- No semantic embeddings.
- No pairwise paper comparison.
- No reranking.
- No full-text evidence extraction.
- No API response contract changes.
- No frontend changes.

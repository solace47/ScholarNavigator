# Query Understanding Runbook

## Scope

This runbook covers the first deterministic `QueryUnderstandingAgent` implementation.

Current boundaries:

- No LLM calls.
- No real retrieval calls from this module.
- No frontend changes.
- No `third_party` changes.

## Module Responsibility

Files:

```text
src/scholar_agent/core/search_schemas.py
src/scholar_agent/agents/query_understanding.py
prompts/query_understanding.md
```

`QueryUnderstandingAgent` converts a user academic query into an internal `SearchPlan`.
The plan is designed to be consumed later by the retrieval layer without mixing internal pipeline models into `core/api_schemas.py`.

Public function:

```python
from scholar_agent.agents.query_understanding import analyze_query

plan = analyze_query(
    "请检索近三年 LLM reranking 在科研论文搜索中的代表性论文",
    top_k=20,
    run_profile="balanced",
    current_year=2026,
)
```

## Internal Schemas

`src/scholar_agent/core/search_schemas.py` defines:

- `TimeRange`
- `QueryConstraint`
- `QueryAnalysis`
- `SearchSubquery`
- `SearchPlan`
- `QueryUnderstandingOptions`

`SearchPlan` includes:

- `query_analysis`
- `subqueries`
- `selected_sources`
- `limit_per_source`
- `top_k`
- `run_profile`
- `enable_refchain`
- `enable_query_evolution`
- `warnings`

Currently allowed sources are only:

- `openalex`
- `arxiv`

Semantic Scholar and PubMed are intentionally not returned as selected sources because their connectors are not implemented yet.

## Fallback Rules

### Language Detection

The rule-based detector returns:

- `zh`: contains Chinese characters and no Latin letters.
- `en`: contains Latin letters and no Chinese characters.
- `mixed`: contains both Chinese characters and Latin letters.
- `unknown`: no Chinese or Latin signal.

### Intent Detection

Supported intents:

- `survey`
- `recent_progress`
- `method_comparison`
- `benchmark_or_dataset`
- `application`
- `paper_finding`
- `general`

Examples:

- `综述`, `survey`, `review`, `related work` -> `survey`
- `最新`, `recent`, `latest`, `SOTA`, `近年` -> `recent_progress`
- `对比`, `compare`, `comparison`, `versus`, `vs` -> `method_comparison`
- `benchmark`, `dataset`, `数据集`, `评测` -> `benchmark_or_dataset`
- `应用`, `application`, `deployment` -> `application`
- `找论文`, `find papers`, `representative papers` -> `paper_finding`

### Domain Detection

Supported domains:

- `computer_science`
- `machine_learning`
- `biomedical`
- `general_science`

Examples:

- `LLM`, `RAG`, `reranking`, `retrieval`, `agent`, `transformer`, `NLP`, `CV`, `深度学习` -> `machine_learning`
- `algorithm`, `database`, `software`, `information retrieval` -> `computer_science`
- `protein`, `gene`, `clinical`, `PubMed`, `biomedical`, `医学`, `生物` -> `biomedical`

Biomedical queries currently still use implemented sources only and include `pubmed_not_implemented` in warnings.

### Time Range Detection

Supported forms:

- `since 2020`
- `after 2020`
- `2021-2024`
- `from 2021 to 2024`
- `last 5 years`
- `recent`, `latest`, `SOTA`, `最新`, `近年`, `近几年`, `近三年`

For recent markers, the rule uses `current_year` if supplied. Tests should pass `current_year` to keep results reproducible.

Example:

```python
analyze_query("近三年 LLM reranking", current_year=2026)
```

returns a time range with:

```text
start_year=2023
end_year=2026
```

### Venue Extraction

Recognized venue tokens:

- `ACL`
- `EMNLP`
- `NAACL`
- `SIGIR`
- `WWW`
- `KDD`
- `NeurIPS`
- `ICLR`
- `ICML`
- `CVPR`
- `ICCV`
- `ECCV`
- `AAAI`
- `IJCAI`

### Source Selection

Current default:

```python
["openalex", "arxiv"]
```

Rules:

- Machine learning and computer science queries use `openalex` and `arxiv`.
- Biomedical queries do not return `pubmed`; they add `pubmed_not_implemented`.
- Queries mentioning Semantic Scholar add `semantic_scholar_not_implemented`.
- `SearchPlan.selected_sources` and each `SearchSubquery.source_hints` reject unsupported sources.

### Run Profiles

- `fast`: fewer subqueries, smaller `limit_per_source`.
- `balanced`: default subquery and limit profile.
- `high_recall`: more subqueries, larger `limit_per_source`.
- `evaluation`: deterministic stable profile, no randomness.

### Subquery Generation

The agent generates 1 to 5 subqueries. Each subquery has:

- `query`
- `source_hints`
- `priority`
- `purpose`

Subquery purposes include:

- `original_query`
- `normalized_keywords`
- `recent_progress_expansion`
- `survey_expansion`
- `method_comparison_expansion`
- `benchmark_dataset_expansion`
- `application_expansion`
- `paper_finding_expansion`
- `domain_ml_expansion`
- `domain_cs_expansion`
- `domain_biomedical_expansion`
- `constraint_expansion`

Chinese queries preserve the original query. When reliable keyword signals exist, the agent adds a short English keyword query using known academic terms instead of inventing a complex translation.

## Connecting to retrieve_papers

The current retriever function is:

```python
retrieve_papers(
    query: str,
    limit_per_source: int = 20,
    sources: list[str] | None = None,
) -> RetrievalOutput
```

Future service-layer usage:

```python
plan = analyze_query(user_query, top_k=20, run_profile="balanced")

for subquery in plan.subqueries:
    output = retrieve_papers(
        subquery.query,
        limit_per_source=plan.limit_per_source,
        sources=subquery.source_hints or plan.selected_sources,
    )
```

After collecting retrieval outputs, the service layer should deduplicate across all subqueries and then pass candidates to future Judgement and Reranker modules.

## Current Non-goals

- No LLM query planning.
- No LLM translation.
- No relevance judgement.
- No reranking.
- No Query Evolution.
- No RefChain.
- No PubMed connector.
- No Semantic Scholar connector.
- No FastAPI API contract changes.
- No frontend changes.

# API Mapper Runbook

## Scope

This runbook covers:

```text
src/scholar_agent/services/api_mapper.py
```

The mapper converts internal `SearchServiceOutput` objects into the existing
public `SearchRunResultResponse` schema. It is a preparation layer for future
real-search API integration.

Current boundaries:

- The mapper is not connected to any FastAPI route.
- Existing `/api/v1/search/runs` Mock API behavior is unchanged.
- No frontend changes.
- No `third_party` changes.
- No LLM calls.
- No network access.

## Public Entry Point

```python
from scholar_agent.services.api_mapper import map_search_service_output_to_api_result

api_result = map_search_service_output_to_api_result(
    run_id,
    search_service_output,
    status="succeeded",
    partial=False,
)
```

Return type:

```python
SearchRunResultResponse
```

## Field Mapping

### Paper

Internal `core.paper_schemas.Paper` maps to `core.api_schemas.Paper`:

- `title`
- `authors`
- `year`
- `venue`
- `abstract`
- `identifiers`
- `urls`
- `sources`

The current API paper schema does not expose `citation_count`, so that field is
not included in the mapped API result.

### RankedPaper

Internal `core.search_schemas.RankedPaper` maps to
`core.api_schemas.RankedPaper`:

- `rank`
- `paper`
- `final_score` -> `relevance_score`
- `category`
- `matched_terms` -> `matched_constraints`
- `ranking_reason`
- `evidence`

### QueryAnalysis

Internal query analysis maps to:

- `intent` -> `intent_type`
- `domain`
- methods, datasets, domains, and must-include terms -> `research_topics`
- time range, venues, methods, datasets, terms, language, and expansion flag ->
  `constraints`

### SearchPlan

Internal search plan maps to:

- subquery strings plus Query Evolution generated query strings ->
  `expanded_queries`
- selected sources -> `source_preferences`
- retrieval output count plus optional RefChain stage -> `max_rounds`

## Paper Classification

Mapped final lists follow the current frontend contract:

- `highly_relevant` -> `highly_relevant_papers`
- `partially_relevant` and `weakly_relevant` ->
  `partially_relevant_papers`
- `irrelevant` and `insufficient_evidence` are excluded from visible final
  paper lists

Filtered irrelevant or insufficient-evidence papers are represented in
`missing_evidence` so the frontend can still surface diagnostic context.

## Cost Report

The mapper generates a no-LLM cost report:

- `api_call_count`: number of source stats records
- `search_api_call_count`: number of source stats records
- `llm_call_count`: `0`
- token estimates: `0`
- `latency_seconds`: `SearchServiceOutput.latency_seconds`
- `cache_hit_count`: `0`
- `search_rounds`: retrieval output count plus optional RefChain stage
- `judged_paper_count`: number of internal judgement results

## Debug And Missing Evidence

The mapper adds important diagnostic signals to `missing_evidence`:

- `SearchServiceOutput.warnings`
- `source_stats[*].error_message`
- Query Evolution generated-query summary, warnings, and skipped reasons
- RefChain seed/reference summary, warnings, and skipped reasons
- filtered irrelevant or insufficient-evidence papers

This keeps the public response useful for frontend debugging without expanding
the API schema yet.

## Derived Structures

`method_clusters` are generated only from method constraints and matched terms.
If there is not enough metadata, no cluster is returned.

`timeline` groups visible mapped papers by publication year.

`citation_graph` contains ranked paper nodes and RefChain reference edges when
available. It does not infer citation relationships from external knowledge.

## Current Limitations

- No route uses this mapper yet.
- `citation_count` is not present in the API paper schema.
- Method clusters are simple deterministic groupings, not semantic topic
  clusters.
- Citation graph only uses RefChain edge metadata already present in
  `SearchServiceOutput`.
- API year is required, so papers without year map to `0`.

## Future Integration

Recommended next steps:

1. Add a feature flag for real-search API mode.
2. Persist real SearchService run state.
3. Use this mapper in the result endpoint only when real-search mode is enabled.
4. Keep Mock API behavior available until frontend and demo flows are stable.
5. Add SSE stage events around real pipeline execution before frontend rollout.

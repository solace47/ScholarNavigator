# API Mapper Runbook

## Scope

This runbook covers:

```text
src/scholar_agent/services/api_mapper.py
```

The mapper converts internal `SearchServiceOutput` objects into the public
`SearchRunResultResponse` schema used by the Real Search result lifecycle.

Current boundaries:

- The mapper is used by Real Search result storage/response code and batch
  tooling.
- `SearchRunResultResponse.synthesis` is optional and may be `null`.
- No `third_party` changes.
- The mapper itself does not call LLMs or access the network.

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

## Real Search API Usage

The mapper feeds the Real Search result endpoint:

```text
GET /api/v1/real/search/runs/{run_id}/result
```

Response model:

```python
SearchRunResultResponse
```

The create endpoint starts `SearchService`, stores the mapped result after
successful background execution, and the result endpoint returns that stored
response:

```python
SearchService().run_search(...)
map_search_service_output_to_api_result(...)
```

Real Search run ids use:

```text
run_real_
```

Manual Real Search calls may access configured external connectors through the
default `SearchService`. Tests monkeypatch `SearchService` and do not access the
network.

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

### Synthesis

Internal `core.synthesis_schemas.SynthesisOutput` maps to the optional
`core.api_schemas.SynthesisOutput` field on `SearchRunResultResponse`.

Mapped fields:

- `answer_summary`
- `status`
- `key_findings`
- `evidence_table`
- `citation_coverage`
- `limitations`
- `warnings`

If `SearchServiceOutput.synthesis_output` is `None`, the API response uses
`synthesis=None`. Synthesis limitations are not duplicated into
`missing_evidence`; that field remains focused on retrieval, source, Query
Evolution, RefChain, and filtered-paper diagnostics.

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

The mapper generates a cost report:

- `api_call_count`: number of source stats records
- `search_api_call_count`: number of source stats records
- `llm_call_count`: SearchService LLM calls when enabled, otherwise `0`
- token usage/estimates: SearchService values when available, otherwise `0`
- `latency_seconds`: `SearchServiceOutput.latency_seconds`
- `cache_hit_count`: count of source stats with `cache_hit=True`
- `search_rounds`: retrieval output count plus optional RefChain stage
- `judged_paper_count`: number of internal judgement results

## Debug And Missing Evidence

The mapper adds important diagnostic signals to `missing_evidence`:

- `SearchServiceOutput.warnings`
- `source_stats[*].error_message`
- Query Evolution generated-query summary, warnings, and skipped reasons
- RefChain seed/reference summary, warnings, and skipped reasons
- filtered irrelevant or insufficient-evidence papers

This keeps the public response useful for frontend debugging while synthesis is
available as a separate optional field.

## Derived Structures

`method_clusters` are generated only from method constraints and matched terms.
If there is not enough metadata, no cluster is returned.

`timeline` groups visible mapped papers by publication year.

`citation_graph` contains ranked paper nodes and RefChain reference edges when
available. It does not infer citation relationships from external knowledge.

## Current Limitations

- `citation_count` is not present in the API paper schema.
- Method clusters are simple deterministic groupings, not semantic topic
  clusters.
- Citation graph only uses RefChain edge metadata already present in
  `SearchServiceOutput`.
- API year is required, so papers without year map to `0`.

## Future Improvements

Recommended next steps:

1. Preserve richer score/debug fields in a dedicated diagnostics schema when
   they are needed by reviewers or offline analysis.
2. Add semantic clustering only after deterministic grouping stops being
   sufficient.
3. Keep API result mapping deterministic and avoid fabricating external facts.

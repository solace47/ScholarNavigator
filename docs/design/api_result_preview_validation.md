# API Result Preview Validation

Date: 2026-07-01

## Scope

This record validates the real runtime response shape of:

```text
POST /api/v1/internal/search/preview/api-result
```

The endpoint was checked as a manual integration preview for mapping `SearchServiceOutput` into the existing `SearchRunResultResponse` API structure. No frontend code, third-party code, Mock API behavior, or LLM path was changed.

## Backend Command

```bash
PYTHONPATH=src uvicorn scholar_agent.app.main:app --host 127.0.0.1 --port 8000
```

## Request

```json
{
  "query": "latest LLM reranking methods for scientific literature retrieval",
  "top_k": 10,
  "run_profile": "balanced",
  "current_year": 2026,
  "enable_query_evolution": true,
  "enable_refchain": false
}
```

## Response Summary

| Field | Observed value |
| --- | --- |
| HTTP status | `200` |
| `run_id` | `run_preview_7ee71dd1adcd` |
| `status` | `succeeded` |
| `query_analysis.intent_type` | `recent_progress` |
| `search_plan.expanded_queries` count | `3` |
| `highly_relevant_papers` count | `0` |
| `partially_relevant_papers` count | `0` |
| `method_clusters` count | `0` |
| `timeline` count | `0` |
| `citation_graph.nodes` count | `0` |
| `citation_graph.edges` count | `0` |

## Missing Evidence

First 10 entries observed:

```json
[
  "OpenAlex search failed: HTTP Error 503: Service Unavailable",
  "arXiv search failed: The read operation timed out",
  "no_relevant_seed",
  "source_error:openalex:OpenAlex search failed: HTTP Error 503: Service Unavailable",
  "source_error:arxiv:arXiv search failed: The read operation timed out",
  "query_evolution:round=1:seed_count=0:generated_count=0",
  "query_evolution_warning:no_relevant_seed",
  "query_evolution_skipped:no_relevant_seed"
]
```

## Cost Report

```json
{
  "api_call_count": 6,
  "search_api_call_count": 6,
  "llm_call_count": 0,
  "estimated_input_tokens": 0,
  "estimated_output_tokens": 0,
  "estimated_total_tokens": 0,
  "latency_seconds": 11.021125750034116,
  "cache_hit_count": 0,
  "search_rounds": 3,
  "judged_paper_count": 0
}
```

## Schema Check

The saved JSON response was validated with:

```bash
PYTHONPATH=src python -c "import json; from pathlib import Path; from scholar_agent.core.api_schemas import SearchRunResultResponse; data=json.loads(Path('/tmp/spar_api_result_preview.json').read_text(encoding='utf-8')); SearchRunResultResponse.model_validate(data); print('schema_valid=true')"
```

Result:

```text
schema_valid=true
```

This confirms the preview response conforms to the existing `SearchRunResultResponse` model.

## External Access Observed

- OpenAlex search was attempted and failed with `HTTP Error 503: Service Unavailable`.
- arXiv search was attempted and failed with `The read operation timed out`.
- OpenAlex references were not requested because `enable_refchain=false`.
- No LLM call was made.

## Conclusion

The `api-result` preview endpoint returned HTTP 200 and produced a valid `SearchRunResultResponse` structure. The result contains no ranked papers in this run because both live retrieval sources failed at network/API level. The mapper still exposed connector failures through `missing_evidence` and `cost_report`, which is useful for frontend debugging and later real-search rollout.

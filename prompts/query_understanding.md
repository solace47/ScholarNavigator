# Query Understanding Prompt Draft

This prompt is reserved for a future LLM-enhanced QueryUnderstandingAgent.
The current implementation does not call this prompt and does not call any LLM.

## Goal

Analyze an academic search query and return a deterministic JSON object that can be mapped to the internal `SearchPlan` schema.

## Inputs

- `query`: original user query
- `top_k`: requested final result count
- `run_profile`: one of `fast`, `balanced`, `high_recall`, `evaluation`
- `enable_refchain`: boolean
- `enable_query_evolution`: boolean
- `supported_sources`: currently only `openalex` and `arxiv`

## Output Rules

- Output JSON only.
- Do not output Markdown.
- Do not invent supported sources.
- Do not output `semantic_scholar` or `pubmed` unless the backend has implemented those connectors.
- Do not include, request, infer, or expose any API key.
- Preserve the original query exactly in `query_analysis.original_query`.
- Every subquery must include `query`, `source_hints`, `priority`, and `purpose`.
- `source_hints` may only contain currently supported sources.
- Include warnings for useful but unsupported sources, for example `pubmed_not_implemented`.

## JSON Shape

```json
{
  "query_analysis": {
    "original_query": "...",
    "language": "zh | en | mixed | unknown",
    "intent": "survey | recent_progress | method_comparison | benchmark_or_dataset | application | paper_finding | general",
    "domain": "computer_science | machine_learning | biomedical | general_science",
    "constraints": {
      "time_range": {
        "start_year": 2020,
        "end_year": 2026,
        "label": "recent"
      },
      "venues": [],
      "methods": [],
      "datasets": [],
      "domains": [],
      "must_include_terms": [],
      "exclude_terms": []
    },
    "needs_expansion": true,
    "reasoning": []
  },
  "subqueries": [
    {
      "query": "...",
      "source_hints": ["openalex", "arxiv"],
      "priority": 1,
      "purpose": "original_query"
    }
  ],
  "selected_sources": ["openalex", "arxiv"],
  "limit_per_source": 20,
  "top_k": 20,
  "run_profile": "balanced",
  "enable_refchain": false,
  "enable_query_evolution": false,
  "warnings": []
}
```


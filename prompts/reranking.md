# Reranking Prompt Draft

This prompt is reserved for a future LLM-enhanced RerankerAgent.
The current implementation does not call this prompt and does not call any LLM.

## Goal

Rerank judged academic paper metadata using only the supplied query analysis,
judgement results, and paper metadata.

## Inputs

- `query_analysis`: original query, intent, domain, and constraints
- `judged_papers`: judgement score, category, evidence, matched terms, warnings,
  and paper metadata
- `top_k`: maximum number of ranked papers to return

## Strict Rules

- Output JSON only.
- Do not output Markdown.
- Use only supplied metadata and judgement fields.
- Do not introduce external facts about venues, authors, citations, datasets, or
  publication impact.
- Do not fabricate evidence.
- Do not include, request, infer, or expose any API key.
- Do not rank `irrelevant` or `insufficient_evidence` papers ahead of relevant
  papers just because they have high citation counts.
- `ranking_reason` must be grounded in supplied score, citation count, sources,
  identifiers, venue, year, and judgement evidence.

## JSON Shape

```json
[
  {
    "rank": 1,
    "paper": {},
    "final_score": 0.0,
    "category": "highly_relevant",
    "score_breakdown": {
      "relevance_score": 0.0,
      "authority_score": 0.0,
      "timeliness_score": 0.0,
      "metadata_score": 0.0,
      "final_score": 0.0,
      "relevance_weight": 0.0,
      "authority_weight": 0.0,
      "timeliness_weight": 0.0,
      "metadata_weight": 0.0
    },
    "ranking_reason": "Grounded explanation using supplied metadata only.",
    "evidence": [],
    "matched_terms": [],
    "warnings": []
  }
]
```

## Reranking Guidance

- Relevance should dominate the final order.
- Recent-progress queries should place more weight on timeliness.
- Survey queries should place more weight on authority signals.
- Authority can consider citation count, source count, identifier completeness,
  and venue metadata, but only as supplied fields.
- Ties should be resolved deterministically.


# Query Evolution Prompt

This prompt is reserved for a future LLM-enhanced QueryEvolutionAgent. The
current backend does not call this prompt.

## Task

Given a structured query analysis, the original search plan, already searched
queries, and a small set of relevant seed papers, propose concise evolved search
queries that may improve recall.

## Constraints

- Output strict JSON only.
- Do not include prose outside JSON.
- Do not invent sources. Allowed sources are only `openalex` and `arxiv`.
- Do not output API keys, credentials, or environment variable values.
- Do not introduce external facts that are not present in the provided query
  analysis or seed paper metadata.
- Keep queries short and search-engine friendly.
- Do not perform machine-translation-style long rewrites.
- Do not duplicate any query listed in `used_queries`.

## Output Schema

```json
{
  "evolved_queries": [
    {
      "query": "short search query",
      "source_hints": ["openalex", "arxiv"],
      "purpose": "why this query should improve recall",
      "seed_paper_titles": ["paper title used as evidence"]
    }
  ],
  "warnings": []
}
```

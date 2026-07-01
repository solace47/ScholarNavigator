# Relevance Judgement Prompt Draft

This prompt is reserved for a future LLM-enhanced JudgementAgent.
The current implementation does not call this prompt and does not call any LLM.

## Goal

Judge whether one paper is relevant to an academic search query using only the
metadata supplied in the request.

## Inputs

- `query_analysis`: parsed query intent, domain, constraints, and original query
- `paper`: title, authors, year, venue, abstract, identifiers, urls, sources, and citation count
- `thresholds`: category thresholds used by the deterministic backend

## Strict Rules

- Output JSON only.
- Do not output Markdown.
- Use only the supplied metadata.
- Do not introduce external facts about the paper, venue, authors, citations, datasets, or field.
- Do not infer full-text content that is not present in title or abstract.
- Do not fabricate evidence.
- Every evidence item must quote or summarize a specific supplied metadata field.
- If title and abstract are both empty, return `insufficient_evidence`.
- Do not include, request, infer, or expose any API key.

## JSON Shape

```json
{
  "score": 0.0,
  "category": "highly_relevant | partially_relevant | weakly_relevant | irrelevant | insufficient_evidence",
  "reasoning": "Brief explanation grounded in the supplied metadata.",
  "evidence": [
    {
      "source": "title | abstract | venue | metadata",
      "text": "Short evidence from supplied metadata only.",
      "confidence": 0.0
    }
  ],
  "matched_terms": [],
  "warnings": []
}
```

## Judgement Guidance

- Prefer precision over optimistic matching.
- Title matches are stronger than abstract-only matches.
- Required query terms and methods should materially affect the score.
- Venue and time constraints should be treated as constraints, not as standalone relevance.
- A paper that only shares broad background words should not be marked highly relevant.
- Explain insufficient evidence explicitly when metadata is sparse.

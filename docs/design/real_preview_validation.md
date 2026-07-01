# Real Internal Search Preview Validation

## Scope

This document records a manual validation run for:

```text
POST /api/v1/internal/search/preview
```

The existing FastAPI backend was already running on:

```text
http://127.0.0.1:8000
```

Process check showed:

```text
uvicorn scholar_agent.app.main:app --reload --host 127.0.0.1 --port 8000
```

## Request

Validation time:

```text
2026-07-01 12:00:20 CST
```

Payload:

```json
{
  "query": "latest LLM reranking methods for scientific literature retrieval",
  "top_k": 10,
  "run_profile": "balanced",
  "current_year": 2026
}
```

Client command type:

```text
Python urllib POST request
```

## Result Summary

- Success: yes
- HTTP status: 200
- Real external access attempted: yes, OpenAlex and arXiv
- Client elapsed seconds: 31.8388
- Backend reported latency_seconds: 31.7857
- raw_count: 10
- deduplicated_count: 10
- ranked_papers returned: 10
- warnings: []

## Query Understanding

Detected query analysis:

```json
{
  "language": "en",
  "intent": "recent_progress",
  "domain": "machine_learning",
  "time_range": {
    "start_year": 2023,
    "end_year": 2026,
    "label": "recent"
  }
}
```

Generated subqueries:

1. `latest LLM reranking methods for scientific literature retrieval`
2. `LLM reranking methods scientific literature retrieval`
3. `recent advances LLM reranking methods scientific literature retrieval 2023-2026`

Selected sources:

```text
openalex, arxiv
```

limit_per_source:

```text
10
```

## Top 5 Ranked Papers

| Rank | Title | Year | Sources | final_score |
| --- | --- | ---: | --- | ---: |
| 1 | CoRank: LLM-Based Compact Reranking with Document Features for Scientific Retrieval | 2025 | arxiv | 0.9043 |
| 2 | Passage Query Methods for Retrieval and Reranking in Conversational Agents | 2025 | arxiv | 0.8653 |
| 3 | LegalMALR:Multi-Agent Query Understanding and LLM-Based Reranking for Chinese Statute Retrieval | 2026 | arxiv | 0.8646 |
| 4 | DS@GT at TREC TOT 2025: Bridging Vague Recollection with Fusion Retrieval and Learned Reranking | 2026 | arxiv | 0.8386 |
| 5 | Tensor Manifold-Based Graph-Vector Fusion for AI-Native Academic Literature Retrieval | 2026 | arxiv | 0.7703 |

## Source Stats

| Source | returned_count | latency_seconds | error_message |
| --- | ---: | ---: | --- |
| openalex | 0 | 0.7103 | null |
| arxiv | 0 | 10.3142 | null |
| openalex | 0 | 0.6953 | null |
| arxiv | 10 | 9.0955 | null |
| openalex | 0 | 0.6497 | null |
| arxiv | 0 | 10.2994 | null |

## Notes

- The preview endpoint successfully exercised the real internal pipeline.
- OpenAlex was queried for all three subqueries but returned zero results.
- arXiv was queried for all three subqueries and returned 10 results for the
  normalized keyword subquery.
- No warnings or connector errors were returned.
- No LLM was called by this endpoint.
- The existing Mock API endpoints were not modified or replaced.


# Connector Observability Validation

## Scope

This document records a manual validation of connector error observability for
the backend search validation path that later became the Real Search lifecycle.

Validation date: 2026-07-01

No frontend files or `third_party` files were changed. No LLM calls were made.

## Backend Command

```bash
PYTHONPATH=src uvicorn scholar_agent.app.main:app \
  --host 127.0.0.1 \
  --port 8000
```

## Request

```json
{
  "query": "latest LLM reranking methods for scientific literature retrieval",
  "top_k": 10,
  "run_profile": "balanced",
  "current_year": 2026,
  "enable_query_evolution": false,
  "enable_refchain": false
}
```

## Result Summary

- HTTP status: `200`
- client elapsed time: `1.613s`
- `raw_count`: `30`
- `deduplicated_count`: `11`
- `latency_seconds`: `1.5753940839786083`

Warnings:

```text
OpenAlex search failed: HTTP Error 503: Service Unavailable
```

## Source Stats

| Source | Returned | Latency | Error message |
| --- | ---: | ---: | --- |
| openalex | 0 | 0.799448s | OpenAlex search failed: HTTP Error 503: Service Unavailable |
| arxiv | 10 | 0.737844s | null |
| openalex | 0 | 0.797701s | OpenAlex search failed: HTTP Error 503: Service Unavailable |
| arxiv | 10 | 0.745553s | null |
| openalex | 0 | 0.798581s | OpenAlex search failed: HTTP Error 503: Service Unavailable |
| arxiv | 10 | 0.733343s | null |

## Top 5 Ranked Papers

| Rank | Title | Year | Sources | Final score |
| ---: | --- | ---: | --- | ---: |
| 1 | CoRank: LLM-Based Compact Reranking with Document Features for Scientific Retrieval | 2025 | arxiv | 0.9043 |
| 2 | Passage Query Methods for Retrieval and Reranking in Conversational Agents | 2025 | arxiv | 0.8653 |
| 3 | LegalMALR:Multi-Agent Query Understanding and LLM-Based Reranking for Chinese Statute Retrieval | 2026 | arxiv | 0.8646 |
| 4 | DS@GT at TREC TOT 2025: Bridging Vague Recollection with Fusion Retrieval and Learned Reranking | 2026 | arxiv | 0.8386 |
| 5 | Tensor Manifold-Based Graph-Vector Fusion for AI-Native Academic Literature Retrieval | 2026 | arxiv | 0.7703 |

## Observability Check

OpenAlex was actually called and returned `HTTP Error 503: Service Unavailable`.
The error is now visible in both:

- `source_stats[*].error_message`
- top-level `warnings`

arXiv was actually called and returned papers. Its `error_message` fields were
`null`.

This confirms the detailed connector path is propagating connector failures into
the retrieval output and internal preview response.

## Known Issue

Warnings are deduplicated at the `SearchService` level, so three repeated
OpenAlex 503 failures appear once in top-level `warnings`. Per-call detail is
still visible in `source_stats`.

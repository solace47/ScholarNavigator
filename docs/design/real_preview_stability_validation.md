# Real Preview Stability Validation

Date: 2026-07-01

## Scope

This record documents a manual stability retest of:

```text
POST /api/v1/internal/search/preview/api-result
```

The backend was started with `REAL_PREVIEW_MAX_WORKERS=1` to reduce concurrent pressure on live OpenAlex/arXiv calls. No frontend code, backend business code, third-party code, Mock API behavior, or LLM path was changed.

## Backend Command

```bash
REAL_PREVIEW_MAX_WORKERS=1 \
PYTHONPATH=src uvicorn scholar_agent.app.main:app --host 127.0.0.1 --port 8000
```

## Request

The following request was sent twice consecutively:

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

## Call 1

| Field | Observed value |
| --- | --- |
| HTTP status | `200` |
| `run_id` | `run_preview_dcf94b03eeec` |
| `status` | `succeeded` |
| `highly_relevant_papers` count | `10` |
| `partially_relevant_papers` count | `0` |
| `search_plan.expanded_queries` count | `6` |
| Retry warning visible | `true` |
| OpenAlex error visible | `true` |
| arXiv error visible | `false` |
| `cost_report.latency_seconds` | `21.126222416991368` |
| Client elapsed seconds | `21.16619424999226` |

First 10 `missing_evidence` entries:

```json
[
  "OpenAlex search transient error on attempt 1/2: HTTP Error 503: Service Unavailable; retried",
  "OpenAlex search failed: HTTP Error 503: Service Unavailable",
  "source_error:openalex:OpenAlex search failed: HTTP Error 503: Service Unavailable",
  "query_evolution:round=1:seed_count=5:generated_count=3"
]
```

Cost report:

```json
{
  "api_call_count": 12,
  "search_api_call_count": 12,
  "llm_call_count": 0,
  "estimated_input_tokens": 0,
  "estimated_output_tokens": 0,
  "estimated_total_tokens": 0,
  "latency_seconds": 21.126222416991368,
  "cache_hit_count": 0,
  "search_rounds": 6,
  "judged_paper_count": 23
}
```

Top 5 papers:

| Rank | Title | Year | Sources | Relevance score |
| --- | --- | --- | --- | --- |
| 1 | CoRank: LLM-Based Compact Reranking with Document Features for Scientific Retrieval | 2025 | `arxiv` | `0.9043` |
| 2 | RankArena: A Unified Platform for Evaluating Retrieval, Reranking and RAG with Human and LLM Feedback | 2025 | `arxiv` | `0.8913` |
| 3 | Rethinking LLM Parametric Knowledge as Post-retrieval Confidence for Dynamic Retrieval and Reranking | 2025 | `arxiv` | `0.8913` |
| 4 | Scientific Paper Retrieval with LLM-Guided Semantic-Based Ranking | 2025 | `arxiv` | `0.8783` |
| 5 | Passage Query Methods for Retrieval and Reranking in Conversational Agents | 2025 | `arxiv` | `0.8653` |

## Call 2

| Field | Observed value |
| --- | --- |
| HTTP status | `200` |
| `run_id` | `run_preview_6ad9e10df56f` |
| `status` | `succeeded` |
| `highly_relevant_papers` count | `10` |
| `partially_relevant_papers` count | `0` |
| `search_plan.expanded_queries` count | `6` |
| Retry warning visible | `true` |
| OpenAlex error visible | `true` |
| arXiv error visible | `false` |
| `cost_report.latency_seconds` | `16.04681004199665` |
| Client elapsed seconds | `16.073193332995288` |

First 10 `missing_evidence` entries:

```json
[
  "OpenAlex search transient error on attempt 1/2: HTTP Error 503: Service Unavailable; retried",
  "OpenAlex search failed: HTTP Error 503: Service Unavailable",
  "source_error:openalex:OpenAlex search failed: HTTP Error 503: Service Unavailable",
  "query_evolution:round=1:seed_count=5:generated_count=3"
]
```

Cost report:

```json
{
  "api_call_count": 12,
  "search_api_call_count": 12,
  "llm_call_count": 0,
  "estimated_input_tokens": 0,
  "estimated_output_tokens": 0,
  "estimated_total_tokens": 0,
  "latency_seconds": 16.04681004199665,
  "cache_hit_count": 0,
  "search_rounds": 6,
  "judged_paper_count": 23
}
```

Top 5 papers:

| Rank | Title | Year | Sources | Relevance score |
| --- | --- | --- | --- | --- |
| 1 | CoRank: LLM-Based Compact Reranking with Document Features for Scientific Retrieval | 2025 | `arxiv` | `0.9043` |
| 2 | RankArena: A Unified Platform for Evaluating Retrieval, Reranking and RAG with Human and LLM Feedback | 2025 | `arxiv` | `0.8913` |
| 3 | Rethinking LLM Parametric Knowledge as Post-retrieval Confidence for Dynamic Retrieval and Reranking | 2025 | `arxiv` | `0.8913` |
| 4 | Scientific Paper Retrieval with LLM-Guided Semantic-Based Ranking | 2025 | `arxiv` | `0.8783` |
| 5 | Passage Query Methods for Retrieval and Reranking in Conversational Agents | 2025 | `arxiv` | `0.8653` |

## External Access

The two preview calls accessed real external retrieval sources through the backend:

- OpenAlex was attempted for each search round.
- OpenAlex returned `HTTP Error 503: Service Unavailable`; the retry/backoff diagnostic was visible in `missing_evidence`.
- arXiv returned usable candidate papers in both calls.
- No arXiv error was visible in the mapped `missing_evidence` for these runs.
- OpenAlex references were not requested because `enable_refchain=false`.
- No LLM call was made.

Backend logs showed repeated retry diagnostics:

```text
OpenAlex search transient error on attempt 1/2: HTTP Error 503: Service Unavailable; retried
OpenAlex search failed: HTTP Error 503: Service Unavailable
```

## Interpretation

Compared with the previous Real Preview validation where both OpenAlex and arXiv failed, the reduced preview concurrency and retry diagnostics produced a more usable outcome:

- The API returned `HTTP 200` for both calls.
- arXiv successfully supplied ranked candidates.
- OpenAlex failures remained visible and explainable.
- Retry/backoff behavior was visible in the frontend-compatible API response via `missing_evidence`.

## Test Result

Command:

```bash
PYTHONPATH=src pytest -q
```

Result:

```text
127 passed, 1 warning in 1.04s
```

The warning is the existing FastAPI/TestClient Starlette deprecation warning.

## Code Change Status

- Frontend source code: not changed.
- Backend business code: not changed.
- `third_party`: not changed.
- Mock API behavior: not changed.

## Known Issues

- OpenAlex still returned `503` in both real calls. The retry/backoff layer makes the failure observable and resilient, but it cannot recover from a persistent upstream outage.
- `REAL_PREVIEW_MAX_WORKERS=1` improves external-source pressure at the cost of higher latency. The two observed request latencies were about 21.1s and 16.0s.

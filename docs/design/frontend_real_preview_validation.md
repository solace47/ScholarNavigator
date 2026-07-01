# Frontend Real Preview Validation

Date: 2026-07-01

## Scope

This record documents a manual end-to-end validation of the ScholarNavigator frontend with both search modes:

- `Mock Demo`
- `Real Preview`

No feature code was intentionally changed. The only committed artifact from this round should be this validation document. The Next.js build temporarily rewrote `frontend/next-env.d.ts`, and that generated change was restored.

## Startup Commands

Backend:

```bash
PYTHONPATH=src uvicorn scholar_agent.app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd frontend
npm run dev
```

Browser URL:

```text
http://localhost:3000
```

Initial page load confirmed:

- `ScholarNavigator` brand was visible.
- `Mock Demo` and `Real Preview` mode controls were visible.
- Backend status showed `backend ready`.

## Mock Demo Validation

Input:

- Mode: `Mock Demo`
- Query: default example query
- Existing defaults were used for `top_k`, `run_profile`, and feature toggles.

Observed result:

| Check | Result |
| --- | --- |
| `run_id` appeared | Passed, `run_3235f31131bd` |
| Run status | `succeeded` |
| Mock SSE/status visible | Passed, `SSE Events` included `run_started` and `run_completed` |
| Result cards visible | Passed |
| Visible paper cards | `5` |
| Highly relevant badge | `3 highly relevant` |
| Partially relevant badge | `2 partially relevant` |

Conclusion: `Mock Demo` mode successfully kept the original mock run/SSE/result flow.

## Real Preview Validation

Input:

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

Observed result:

| Check | Result |
| --- | --- |
| `run_id` prefix | Passed, `run_preview_fb51633c687c` |
| Run status | `succeeded` |
| Preview transport copy | Passed, displayed `Real Preview 使用一次性 REST 响应，不开启 SSE 事件流。` |
| Result cards visible | No cards returned in this run |
| Empty-candidate diagnostic | Passed, displayed `检索源失败/无候选` |
| `missing_evidence` visible | Passed |
| Cost summary | `API calls=6`, `Tokens=0`, `Latency=11.1s`, `Cache hits=0` |

Visible `missing_evidence` diagnostics:

```text
OpenAlex search failed: HTTP Error 503: Service Unavailable
arXiv search failed: HTTP Error 429: Unknown Error
arXiv search failed: The read operation timed out
no_relevant_seed
source_error:openalex:OpenAlex search failed: HTTP Error 503: Service Unavailable
source_error:arxiv:arXiv search failed: HTTP Error 429: Unknown Error
source_error:arxiv:arXiv search failed: The read operation timed out
query_evolution:round=1:seed_count=0:generated_count=0
query_evolution_warning:no_relevant_seed
query_evolution_skipped:no_relevant_seed
```

Search plan shown in the UI:

```text
latest LLM reranking methods for scientific literature retrieval
LLM reranking methods scientific literature retrieval
recent advances LLM reranking methods scientific literature retrieval 2023-2026
```

Conclusion: `Real Preview` mode successfully called the real preview endpoint and rendered the existing `SearchRunResultResponse` shape. This run produced no paper cards because OpenAlex/arXiv returned API/network errors, and the frontend correctly surfaced the diagnostics instead of showing a blank result.

## External Access

Real Preview did access real retrieval sources through the backend:

- OpenAlex was attempted and returned `HTTP Error 503: Service Unavailable`.
- arXiv was attempted and returned both `HTTP Error 429: Unknown Error` and a read timeout across subqueries.
- OpenAlex references were not accessed because `enable_refchain=false`.
- No LLM call was made.

## Verification Commands

Frontend lint:

```bash
cd frontend && npm run lint
```

Result:

```text
eslint .
```

Exit code: `0`.

Frontend build:

```bash
cd frontend && npm run build
```

Result:

```text
Compiled successfully
Finished TypeScript
Generating static pages (3/3)
```

Exit code: `0`.

Backend tests:

```bash
PYTHONPATH=src pytest -q
```

Result:

```text
121 passed, 1 warning in 0.95s
```

The warning is the existing FastAPI/TestClient Starlette deprecation warning.

## Code Change Status

- Frontend source code: not changed in this validation round.
- Backend source code: not changed.
- `third_party`: not changed.
- Mock API behavior: not changed.

## Known Issues

- Real Preview depends on live OpenAlex/arXiv availability. This run returned no candidates because OpenAlex responded with `503` and arXiv responded with `429`/timeout.
- The frontend behavior is correct for this failure mode: the result area shows `检索源失败/无候选` and exposes `missing_evidence`.

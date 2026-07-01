# Frontend Synthesis Validation

Date: 2026-07-01

## Scope

This document records manual end-to-end validation of the optional
Citation-backed Synthesis panel in the ScholarNavigator frontend.

No frontend feature code, backend business code, `third_party` code, or Mock API
behavior was changed during this validation.

## Commands

Backend:

```bash
REAL_PREVIEW_MAX_WORKERS=1 \
PYTHONPATH=src uvicorn scholar_agent.app.main:app --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd frontend && npm run dev
```

Browser URL:

```text
http://localhost:3000
```

## Mock Demo Validation

Steps:

1. Selected `Mock Demo`.
2. Used the default Chinese sample query.
3. Clicked the search button.

Observed result:

- Mock run succeeded.
- `run_id` appeared: `run_873c3e1ea443`.
- Mock SSE events appeared in Run Progress.
- Mock paper cards rendered normally.
- No `Citation-backed Synthesis` panel appeared.
- This is expected because Mock Demo returns `synthesis=null` or omits the
  optional synthesis field.
- Browser console reported no warning/error logs.

## Real Preview Validation

Steps:

1. Selected `Real Preview`.
2. Query:

   ```text
   latest LLM reranking methods for scientific literature retrieval
   ```

3. Settings:
   - `top_k=10`
   - `run_profile=balanced`
   - `current_year=2026`
   - `enable_query_evolution=true`
   - `enable_refchain=false`
4. Clicked `启动 Real Preview`.

Observed result:

- Request returned successfully.
- `run_id` appeared: `run_preview_5e6803a8be34`.
- Results area rendered paper cards.
- Citation-backed Synthesis panel appeared above the paper lists.
- The panel displayed the MVP disclaimer:

  ```text
  规则版 metadata/evidence-row synthesis；当前 MVP 不代表系统已读取全文 PDF。
  ```

Top visible paper cards:

1. `CoRank: LLM-Based Compact Reranking with Document Features for Scientific Retrieval`
2. `RankArena: A Unified Platform for Evaluating Retrieval, Reranking and RAG with Human and LLM Feedback`
3. `Rethinking LLM Parametric Knowledge as Post-retrieval Confidence for Dynamic Retrieval and Reranking`
4. `Scientific Paper Retrieval with LLM-Guided Semantic-Based Ranking`
5. `Passage Query Methods for Retrieval and Reranking in Conversational Agents`

## Synthesis Fields

Status:

```text
succeeded
```

Answer summary, first 500 characters:

```text
For the query "latest LLM reranking methods for scientific literature retrieval", the current machine_learning search evidence supports a recent_progress synthesis around retrieval, reranking, LLM, methods. The strongest citation-backed candidates are [R1], [R2], [R3]. 5 finding(s) were generated only from ranked-paper evidence rows.
```

Displayed key findings count:

```text
5
```

Displayed evidence table count:

```text
22
```

Displayed citation coverage counters:

| Counter | Value |
| --- | --- |
| ranked_paper_count | `10` |
| cited_paper_count | `8` |
| evidence_row_count | `22` |
| coverage_ratio | `0.8` |
| source_error_count | `6` |

Limitations / warnings shown in the panel:

```text
OpenAlex search transient error on attempt 1/2: HTTP Error 503: Service Unavailable; retried
OpenAlex search failed: HTTP Error 503: Service Unavailable
source_error:openalex:OpenAlex search failed: HTTP Error 503: Service Unavailable
refchain_not_enabled_or_not_available
full_text_evidence_unavailable
metadata_evidence_used
```

Evidence table preview showed rows with:

- `citation_key`
- `rank`
- `year`
- `evidence_source`
- `paper_title`
- `evidence_text`

Example row:

```text
R1 / rank 1 / 2025 / abstract
CoRank: LLM-Based Compact Reranking with Document Features for Scientific Retrieval
However, standard LLM listwise reranking faces challenges in the scientific domain.
```

## Missing Evidence

The Results area also displayed `Missing Evidence` diagnostics:

```text
OpenAlex search transient error on attempt 1/2: HTTP Error 503: Service Unavailable; retried
OpenAlex search failed: HTTP Error 503: Service Unavailable
source_error:openalex:OpenAlex search failed: HTTP Error 503: Service Unavailable
query_evolution:round=1:seed_count=5:generated_count=3
```

## External Access

Real Preview accessed live retrieval sources through the backend:

- OpenAlex was attempted and returned `HTTP Error 503: Service Unavailable`.
- OpenAlex retry/backoff diagnostics were visible in the UI.
- arXiv did not surface an error and returned usable paper cards.
- OpenAlex references were not requested because `enable_refchain=false`.

No LLM call was made. The UI cost metrics continued to show zero token usage.

## Test Results

Commands:

```bash
cd frontend && npm run lint
cd frontend && npm run build
PYTHONPATH=src pytest -q
```

Results:

```text
npm run lint: passed
npm run build: passed
pytest: 140 passed, 1 warning
```

The warning is the existing FastAPI/TestClient Starlette deprecation warning.

## Code Change Status

- Frontend feature code: not changed during validation.
- Backend business code: not changed.
- `third_party`: not changed.
- Mock API behavior: not changed.
- Added this validation document only.

## Known Issues

- OpenAlex returned persistent `503` during this validation. The retry/backoff
  path remained visible and did not prevent arXiv-backed results from rendering.
- The current Synthesis panel displays core citation coverage counters, not
  every internal coverage field.
- `third_party/paper-qa` still has an existing staged deletion of
  `tests/stub_data/.DS_Store` from earlier cleanup work; this validation did not
  modify it.

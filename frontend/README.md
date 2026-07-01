# ScholarNavigator Frontend

Next.js + TypeScript + Tailwind CSS prototype for ScholarNavigator.

## Requirements

- Node.js 20+
- npm 10+
- FastAPI backend running at `http://localhost:8000`

## Install

```bash
cd frontend
npm install
```

## Configure Backend URL

The frontend defaults to:

```text
http://localhost:8000
```

Override it with:

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

## Start Development Server

```bash
npm run dev
```

Open:

```text
http://localhost:3000
```

## Build

```bash
npm run build
```

## Lint

```bash
npm run lint
```

## Backend

Start the mock backend from the repository root:

```bash
PYTHONPATH=src uvicorn scholar_agent.app.main:app --reload --host 127.0.0.1 --port 8000
```

## Search Modes

The workbench has two modes:

- `Mock Demo`: keeps the original mock run lifecycle under `/api/v1/search/runs`, including mock run creation, run status, result fetch, and mock SSE events.
- `Real Preview`: uses the asynchronous real run lifecycle under `/api/v1/real/search/runs`, including real run creation, queued/running/succeeded/failed status polling, result fetch after completion, and real search SSE events.

`Real Preview` may access real OpenAlex and arXiv through the backend. It still does not read, store, or display API keys in the frontend.

The backend still exposes `POST /api/v1/internal/search/preview/api-result` for
debugging the mapper path, but it is no longer the frontend's primary Real
Preview path.

If real retrieval returns no visible papers, check the `missing_evidence` diagnostics in the Results panel. Network failures such as OpenAlex 503 or arXiv timeout are surfaced there when the backend reports them.

If the SSE connection fails while status polling continues, the UI keeps the
current run status/result and records an `sse_error` event instead of clearing
the results.

`Real Preview` also supports cancelling a queued/running real search run. The UI
calls `POST /api/v1/real/search/runs/{run_id}/cancel`, stops polling, closes the
SSE connection, and keeps already received events visible. The current backend
cannot force-kill connector calls that are already executing; cancellation marks
the run as `cancelled`, ignores any later background result, and stops the
frontend from waiting for completion.

## Synthesis Panel

`Real Preview` may return an optional `synthesis` object in `SearchRunResultResponse`.
When present, the Results area renders a citation-backed synthesis panel above the
paper lists with:

- `answer_summary`
- `status`
- key findings with citation keys and confidence
- citation coverage counters
- limitations and warnings
- the first evidence-table rows

`Mock Demo` defaults to `synthesis: null`, so the panel is hidden and the mock
flow remains unchanged.

The current synthesis MVP is rule-based and grounded in ranked-paper metadata
and evidence rows. It is citation-backed, but it does not mean the system has
read full-text PDFs.

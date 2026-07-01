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

If port `3000` is already in use, Next.js may switch to another port such as
`3001`. The backend CORS defaults allow common local frontend ports:

- `http://localhost:3000`
- `http://127.0.0.1:3000`
- `http://localhost:3001`
- `http://127.0.0.1:3001`
- `http://localhost:5173`
- `http://127.0.0.1:5173`

To add a custom frontend origin, start the backend with:

```bash
SCHOLAR_AGENT_CORS_ORIGINS=http://localhost:4321 \
PYTHONPATH=src uvicorn scholar_agent.app.main:app --host 127.0.0.1 --port 8000
```

`SCHOLAR_AGENT_CORS_ORIGINS` is comma-separated. Values are trimmed, empty
entries are ignored, and configured origins are merged with the default
allowlist.

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

When backend retrieval cache is enabled, repeated Real Preview runs in the same
backend process may report `cache_hit_count` in the cost report and show cache
hit diagnostics in `missing_evidence`.

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

## Citation Graph Panel

The Results area also renders a Citation Graph panel when
`SearchRunResultResponse.citation_graph.nodes` or `.edges` is non-empty.

The panel shows:

- node and edge counts
- node list with `label`, `id`, and optional `rank`
- edge list with `source`, `target`, and `relation`
- an explicit empty-edge state when nodes exist but no edges were returned

The graph panel only displays structured citation graph / RefChain metadata
returned by the backend. The frontend does not infer missing citation
relationships or create graph edges on its own.

## Result Export

When a search result is loaded, the Results header shows:

- `Export JSON`
- `Export Markdown`

`Export JSON` downloads the complete current `SearchRunResultResponse` object as:

```text
scholar-navigator-result-{run_id}.json
```

`Export Markdown` downloads a readable report as:

```text
scholar-navigator-result-{run_id}.md
```

The Markdown report includes run metadata, query analysis, expanded queries,
cost report, optional synthesis, highly relevant papers, partially relevant
papers, citation graph nodes/edges, and missing evidence diagnostics.

Both export actions run entirely in the browser with `Blob` and
`URL.createObjectURL`. They use the result already present on the page, do not
trigger a new search, do not upload data to the backend, and do not access any
external service.

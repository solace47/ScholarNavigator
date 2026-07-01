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

- `Mock Demo`: keeps the original `/api/v1/search/runs` flow, including mock run creation, run status, result fetch, and SSE events.
- `Real Preview`: calls `POST /api/v1/internal/search/preview/api-result` once and renders the returned `SearchRunResultResponse` with the same result UI.

`Real Preview` may access real OpenAlex and arXiv through the backend. It still does not read, store, or display API keys in the frontend.

If real retrieval returns no visible papers, check the `missing_evidence` diagnostics in the Results panel. Network failures such as OpenAlex 503 or arXiv timeout are surfaced there when the backend reports them.

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

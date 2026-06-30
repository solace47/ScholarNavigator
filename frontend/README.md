# ScholarNavigator Frontend

Next.js + TypeScript + Tailwind CSS prototype for the SPAR FastAPI Mock API.

## Requirements

- Node.js 20+
- npm 10+
- FastAPI Mock API running at `http://localhost:8000`

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

The frontend only calls the backend mock API. It does not read, store, or display API keys.

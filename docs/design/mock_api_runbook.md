# Mock API Runbook

## 1. Scope

This backend is a FastAPI mock skeleton for frontend integration. It does not call OpenAlex, arXiv, Semantic Scholar, PubMed, or any LLM provider. API keys are not required and must not be placed in frontend code.

## 2. Install Dependencies

From the project root:

```bash
python3 -m pip install -r requirements.txt
```

If you only need the mock API dependencies in an already prepared environment:

```bash
python3 -m pip install fastapi uvicorn pytest httpx
```

## 3. Start FastAPI

From the project root:

```bash
PYTHONPATH=src uvicorn scholar_agent.app.main:app --reload --host 127.0.0.1 --port 8000
```

OpenAPI docs:

```text
http://127.0.0.1:8000/docs
```

Health check:

```bash
curl http://127.0.0.1:8000/api/v1/health
```

## 4. Create a Mock Search Run

```bash
curl -X POST http://127.0.0.1:8000/api/v1/search/runs \
  -H "Content-Type: application/json" \
  -d '{
    "query": "请帮我搜索关于 LLM reranking 的代表性论文",
    "constraints": {
      "time_range": {"start_year": 2020, "end_year": 2026},
      "venues": ["ACL", "EMNLP", "SIGIR"],
      "must_have_terms": ["reranking"],
      "paper_types": ["method", "benchmark"]
    },
    "source_preferences": ["openalex", "arxiv", "semantic_scholar"],
    "top_k": 20
  }'
```

Then query the run:

```bash
curl http://127.0.0.1:8000/api/v1/search/runs/<run_id>
curl http://127.0.0.1:8000/api/v1/search/runs/<run_id>/result
```

SSE event stream:

```bash
curl -N http://127.0.0.1:8000/api/v1/search/runs/<run_id>/events
```

## 5. Run Tests

```bash
PYTHONPATH=src pytest -q
```


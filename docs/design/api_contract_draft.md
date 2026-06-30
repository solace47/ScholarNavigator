# 后端 API Contract 草案

## 1. 约定

本文件是前后端分离的 API 草案，后续实现时应以 Pydantic 模型和 OpenAPI schema 固化。

基础约定：

- Base path：`/api/v1`
- 数据格式：JSON
- 实时事件：优先 SSE，必要时再补 WebSocket
- 时间格式：ISO 8601
- ID 格式：后端生成稳定字符串，例如 `run_...`、`eval_...`
- API Key：只存在后端环境变量中，前端不传、不读、不存

通用错误：

```json
{
  "error": {
    "code": "CONNECTOR_TIMEOUT",
    "message": "OpenAlex request timed out.",
    "retryable": true,
    "details": {
      "connector": "openalex",
      "timeout_seconds": 10
    }
  }
}
```

## 2. Health 与安全配置

### GET `/api/v1/health`

返回后端健康状态。

响应：

```json
{
  "status": "ok",
  "version": "0.1.0",
  "time": "2026-07-01T12:00:00+08:00"
}
```

### GET `/api/v1/runtime/config`

返回前端可展示的安全配置，不包含任何密钥。

响应：

```json
{
  "llm": {
    "provider": "openai-compatible",
    "model": "configured-model-name",
    "available": true
  },
  "connectors": [
    {
      "name": "openalex",
      "available": true,
      "requires_key": false
    },
    {
      "name": "semantic_scholar",
      "available": false,
      "requires_key": true,
      "reason": "missing_api_key"
    }
  ],
  "limits": {
    "max_top_k": 100,
    "max_search_rounds": 3,
    "max_candidate_papers": 300,
    "max_latency_seconds": 120
  },
  "features": {
    "query_evolution": true,
    "refchain": true,
    "evaluation": true,
    "sse": true
  }
}
```

## 3. Search Run API

### POST `/api/v1/search/runs`

创建一次论文检索任务。

请求：

```json
{
  "query": "Find recent papers on LLM reranking for scientific literature search.",
  "locale": "zh-CN",
  "constraints": {
    "time_range": {
      "start_year": 2020,
      "end_year": 2026
    },
    "venues": ["ACL", "EMNLP", "SIGIR"],
    "must_have_terms": ["reranking"],
    "excluded_terms": [],
    "datasets": [],
    "paper_types": ["method", "benchmark"]
  },
  "source_preferences": ["openalex", "arxiv", "semantic_scholar"],
  "run_profile": "balanced",
  "top_k": 20,
  "budgets": {
    "max_search_rounds": 2,
    "max_candidate_papers": 200,
    "max_llm_calls": 20,
    "max_total_tokens": 50000,
    "max_latency_seconds": 90
  },
  "options": {
    "enable_query_evolution": true,
    "enable_refchain": true,
    "refchain_depth": 1,
    "return_markdown": true,
    "return_json": true,
    "stream_events": true
  }
}
```

响应：

```json
{
  "run_id": "run_01HXYZ",
  "status": "queued",
  "created_at": "2026-07-01T12:00:00+08:00",
  "links": {
    "self": "/api/v1/search/runs/run_01HXYZ",
    "events": "/api/v1/search/runs/run_01HXYZ/events",
    "result": "/api/v1/search/runs/run_01HXYZ/result"
  }
}
```

### GET `/api/v1/search/runs`

查询检索任务列表。

查询参数：

- `status`
- `limit`
- `cursor`

响应：

```json
{
  "items": [
    {
      "run_id": "run_01HXYZ",
      "query": "Find recent papers on LLM reranking...",
      "status": "succeeded",
      "created_at": "2026-07-01T12:00:00+08:00",
      "finished_at": "2026-07-01T12:01:10+08:00",
      "summary": {
        "highly_relevant_count": 12,
        "partially_relevant_count": 8,
        "latency_seconds": 70.2
      }
    }
  ],
  "next_cursor": null
}
```

### GET `/api/v1/search/runs/{run_id}`

查询任务状态和摘要。

响应：

```json
{
  "run_id": "run_01HXYZ",
  "status": "running",
  "current_stage": "judgement",
  "progress": {
    "completed_stages": ["query_understanding", "retrieval", "deduplication"],
    "candidate_paper_count": 145,
    "judged_paper_count": 40
  },
  "cost_report": {
    "api_call_count": 8,
    "search_api_call_count": 6,
    "llm_call_count": 2,
    "estimated_input_tokens": 9000,
    "estimated_output_tokens": 1600,
    "estimated_total_tokens": 10600,
    "latency_seconds": 31.4,
    "cache_hit_count": 12,
    "search_rounds": 1,
    "judged_paper_count": 40
  },
  "created_at": "2026-07-01T12:00:00+08:00",
  "updated_at": "2026-07-01T12:00:31+08:00"
}
```

### POST `/api/v1/search/runs/{run_id}/cancel`

取消运行中的检索任务。

响应：

```json
{
  "run_id": "run_01HXYZ",
  "status": "cancelled",
  "cancelled_at": "2026-07-01T12:00:35+08:00"
}
```

## 4. Search Events API

### GET `/api/v1/search/runs/{run_id}/events`

SSE 事件流。事件格式：

```text
event: stage_started
data: {"run_id":"run_01HXYZ","stage":"retrieval","timestamp":"2026-07-01T12:00:05+08:00"}
```

推荐事件类型：

| Event | 说明 |
|---|---|
| `run_started` | 检索任务开始 |
| `stage_started` | 阶段开始 |
| `stage_completed` | 阶段完成 |
| `query_plan_created` | 查询理解与子查询生成完成 |
| `connector_called` | 调用检索源 |
| `connector_completed` | 检索源返回 |
| `candidates_updated` | 候选论文数量变化 |
| `judgement_updated` | 相关性判断进度变化 |
| `cost_updated` | 成本统计变化 |
| `warning` | 降级或部分失败 |
| `error` | 错误 |
| `run_completed` | 检索任务完成 |

示例：

```json
{
  "run_id": "run_01HXYZ",
  "stage": "retrieval",
  "connector": "openalex",
  "query": "LLM reranking scientific literature search",
  "returned_count": 50,
  "latency_ms": 842,
  "cache_hit": false,
  "timestamp": "2026-07-01T12:00:08+08:00"
}
```

## 5. Search Result API

### GET `/api/v1/search/runs/{run_id}/result`

获取最终结构化结果。若任务未完成，可返回当前可用部分结果并标记 `partial=true`。

响应：

```json
{
  "run_id": "run_01HXYZ",
  "status": "succeeded",
  "partial": false,
  "query_analysis": {
    "intent_type": "paper_finding",
    "domain": "computer science",
    "research_topics": ["LLM reranking", "scientific literature search"],
    "constraints": {
      "time_range": {
        "start_year": 2020,
        "end_year": 2026
      },
      "venues": ["ACL", "EMNLP", "SIGIR"]
    }
  },
  "search_plan": {
    "expanded_queries": [
      "LLM reranking scientific literature search",
      "large language model reranker academic paper retrieval"
    ],
    "source_preferences": ["openalex", "arxiv", "semantic_scholar"],
    "max_rounds": 2
  },
  "highly_relevant_papers": [
    {
      "rank": 1,
      "paper": {
        "title": "Example Paper Title",
        "authors": ["A. Author", "B. Author"],
        "year": 2025,
        "venue": "ACL",
        "abstract": "Short abstract...",
        "identifiers": {
          "doi": "10.0000/example",
          "arxiv_id": "2501.00000",
          "semantic_scholar_id": "s2-id",
          "openalex_id": "W0000000000",
          "pubmed_id": null
        },
        "urls": {
          "landing_page": "https://example.org/paper",
          "pdf": "https://example.org/paper.pdf"
        },
        "sources": ["openalex", "semantic_scholar"]
      },
      "relevance_score": 0.92,
      "category": "highly_relevant",
      "matched_constraints": ["topic", "time_range", "venue"],
      "ranking_reason": "The paper directly studies LLM reranking for academic retrieval and matches the requested venue range.",
      "evidence": [
        {
          "source": "abstract",
          "text": "Evidence summary, not a long verbatim quote.",
          "confidence": 0.86
        }
      ]
    }
  ],
  "partially_relevant_papers": [],
  "method_clusters": [
    {
      "name": "LLM-as-reranker",
      "paper_ranks": [1, 3, 5],
      "summary": "Papers that use LLMs to score or reorder retrieved candidates."
    }
  ],
  "timeline": [
    {
      "year": 2025,
      "paper_ranks": [1, 2],
      "summary": "Recent work focuses on agentic query rewriting and reranking."
    }
  ],
  "citation_graph": {
    "nodes": [
      {
        "id": "W0000000000",
        "label": "Example Paper Title",
        "rank": 1
      }
    ],
    "edges": []
  },
  "missing_evidence": [],
  "cost_report": {
    "api_call_count": 12,
    "search_api_call_count": 8,
    "llm_call_count": 4,
    "estimated_input_tokens": 18000,
    "estimated_output_tokens": 3200,
    "estimated_total_tokens": 21200,
    "latency_seconds": 70.2,
    "cache_hit_count": 18,
    "search_rounds": 2,
    "judged_paper_count": 50
  }
}
```

### GET `/api/v1/search/runs/{run_id}/export`

导出结果。

查询参数：

- `format=json | markdown | csv`

响应：

- `application/json`
- `text/markdown`
- `text/csv`

## 6. Evaluation API

### POST `/api/v1/evaluations`

创建评测任务。

请求：

```json
{
  "dataset": "litsearch",
  "split": "dev",
  "sample_limit": 50,
  "top_k": 20,
  "run_profile": "balanced",
  "ablation": {
    "disable_query_understanding": false,
    "disable_query_evolution": false,
    "disable_refchain": false,
    "disable_llm_judgement": false,
    "disable_reranker": false,
    "disable_cache": false
  },
  "budgets": {
    "max_search_rounds": 2,
    "max_candidate_papers": 200,
    "max_llm_calls": 20,
    "max_total_tokens": 50000,
    "max_latency_seconds": 90
  }
}
```

响应：

```json
{
  "evaluation_id": "eval_01HXYZ",
  "status": "queued",
  "created_at": "2026-07-01T12:00:00+08:00",
  "links": {
    "self": "/api/v1/evaluations/eval_01HXYZ",
    "events": "/api/v1/evaluations/eval_01HXYZ/events",
    "report": "/api/v1/evaluations/eval_01HXYZ/report"
  }
}
```

### GET `/api/v1/evaluations/{evaluation_id}`

查询评测任务状态。

响应：

```json
{
  "evaluation_id": "eval_01HXYZ",
  "status": "running",
  "dataset": "litsearch",
  "split": "dev",
  "progress": {
    "total": 50,
    "completed": 12,
    "failed": 1
  },
  "partial_metrics": {
    "precision_at_20": 0.42,
    "recall_at_20": 0.35,
    "f1_at_20": 0.38,
    "recall_at_5": 0.18,
    "recall_at_10": 0.27,
    "recall_at_50": 0.49
  },
  "cost_report": {
    "api_call_count": 144,
    "llm_call_count": 36,
    "estimated_total_tokens": 190000,
    "latency_seconds": 840.0,
    "cache_hit_count": 230
  }
}
```

### GET `/api/v1/evaluations/{evaluation_id}/events`

SSE 评测事件流。

事件类型：

- `evaluation_started`
- `example_started`
- `example_completed`
- `metrics_updated`
- `cost_updated`
- `warning`
- `error`
- `evaluation_completed`

### GET `/api/v1/evaluations/{evaluation_id}/report`

获取评测报告。

响应：

```json
{
  "evaluation_id": "eval_01HXYZ",
  "dataset": "litsearch",
  "split": "dev",
  "sample_count": 50,
  "metrics": {
    "precision_at_20": 0.45,
    "recall_at_20": 0.39,
    "f1_at_20": 0.42,
    "recall_at_5": 0.21,
    "recall_at_10": 0.31,
    "recall_at_50": 0.52
  },
  "efficiency": {
    "avg_api_call_count": 11.2,
    "avg_llm_call_count": 3.0,
    "avg_estimated_total_tokens": 4200,
    "avg_latency_seconds": 18.6,
    "avg_candidate_paper_count": 156,
    "avg_final_paper_count": 20
  },
  "examples": [
    {
      "example_id": "litsearch_0001",
      "query": "Example query",
      "run_id": "run_01HABC",
      "metrics": {
        "precision_at_20": 0.5,
        "recall_at_20": 0.4,
        "f1_at_20": 0.44
      },
      "error": null
    }
  ]
}
```

## 7. Cache、Cost 与 Logs API

### GET `/api/v1/cost/runs/{run_id}`

获取单次运行成本。

响应：

```json
{
  "run_id": "run_01HXYZ",
  "cost_report": {
    "api_call_count": 12,
    "search_api_call_count": 8,
    "llm_call_count": 4,
    "estimated_input_tokens": 18000,
    "estimated_output_tokens": 3200,
    "estimated_total_tokens": 21200,
    "latency_seconds": 70.2,
    "cache_hit_count": 18,
    "search_rounds": 2,
    "judged_paper_count": 50
  },
  "stage_breakdown": [
    {
      "stage": "retrieval",
      "latency_seconds": 9.4,
      "api_call_count": 8
    },
    {
      "stage": "judgement",
      "latency_seconds": 31.2,
      "llm_call_count": 3,
      "estimated_total_tokens": 16000
    }
  ]
}
```

### GET `/api/v1/cache/stats`

响应：

```json
{
  "entries": {
    "search_response_cache": 120,
    "paper_detail_cache": 640,
    "llm_cache": 80,
    "judgement_cache": 320,
    "rerank_cache": 40
  },
  "hit_rate": {
    "search_response_cache": 0.61,
    "paper_detail_cache": 0.74,
    "llm_cache": 0.28
  }
}
```

### POST `/api/v1/cache/invalidate`

让后端按 scope 清理缓存。该接口应仅在本地开发或受控演示环境开放。

请求：

```json
{
  "scope": "search_response_cache",
  "older_than_days": 7
}
```

响应：

```json
{
  "scope": "search_response_cache",
  "deleted_count": 42
}
```

### GET `/api/v1/logs`

查询结构化日志摘要。

查询参数：

- `run_id`
- `level`
- `stage`
- `limit`
- `cursor`

响应：

```json
{
  "items": [
    {
      "timestamp": "2026-07-01T12:00:08+08:00",
      "level": "warning",
      "run_id": "run_01HXYZ",
      "stage": "retrieval",
      "message": "Semantic Scholar unavailable; fallback to OpenAlex and arXiv.",
      "error_code": "CONNECTOR_UNAVAILABLE"
    }
  ],
  "next_cursor": null
}
```

## 8. 推荐枚举

### Run status

```text
queued | running | succeeded | failed | cancelled
```

### Run profile

```text
fast | balanced | high_recall | evaluation
```

### Search stage

```text
query_understanding
retrieval
deduplication
prefilter
judgement
query_evolution
refchain
reranking
synthesis
```

### Paper relevance category

```text
highly_relevant | partially_relevant | weakly_relevant | irrelevant | insufficient_evidence
```

### Connector name

```text
openalex | arxiv | semantic_scholar | pubmed
```

## 9. Contract 测试建议

- 请求 JSON 能被 Pydantic 模型解析。
- 响应 JSON 与 OpenAPI schema 一致。
- SSE event 至少包含 `run_id`、`timestamp`、`event type`。
- 错误响应统一包含 `code`、`message`、`retryable`。
- 所有接口不返回 API Key。
- `result` 中每篇论文至少包含标题、年份、来源信息和相关性解释。
- `cost_report` 在成功、失败、取消时都尽量返回。

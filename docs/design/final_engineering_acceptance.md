# 最终工程验收记录

执行时间：2026-07-01 20:24:20 CST

本轮只做工程验收验证和记录。未修改 backend 功能代码、frontend 功能代码或
`third_party`。未调用 LLM。Real Search 与 Batch CLI 验证中真实访问了
OpenAlex/arXiv；本次 OpenAlex 多次返回 `503 Service Unavailable`，arXiv
可返回候选论文。该情况按外部检索源波动记录，不视为系统崩溃。

## A. 测试与构建

后端测试：

```bash
PYTHONPATH=src pytest -q
```

结果：

```text
190 passed, 1 warning in 1.89s
```

说明：warning 为既有 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`。

前端 lint：

```bash
cd frontend && npm run lint
```

结果：通过。

前端 build：

```bash
cd frontend && npm run build
```

结果：通过。Next.js 16.2.9 Turbopack production build 成功。

## B. Runtime Config

后端启动命令：

```bash
SCHOLAR_AGENT_RETRIEVAL_CACHE=1 \
REAL_SEARCH_MAX_WORKERS=1 \
REAL_SEARCH_BACKGROUND_WORKERS=2 \
PYTHONPATH=src uvicorn scholar_agent.app.main:app --host 127.0.0.1 --port 8000
```

检查命令：

```bash
curl http://127.0.0.1:8000/api/v1/runtime/config
```

结果摘要：

- HTTP 200。
- `mode=hybrid`。
- `llm.available=false`，`model=mock-no-llm`。
- `mock.available=true`，`reason=mock_demo_path`。
- `openalex.available=true`，`reason=implemented_for_real_search`。
- `arxiv.available=true`，`reason=implemented_for_real_search`。
- `semantic_scholar.available=false`，`reason=not_implemented`。
- `pubmed.available=false`，`reason=not_implemented`。
- `features.real_search=true`。
- `features.real_search_cancel=true`。
- `features.real_search_sse=true`。
- `features.retrieval_cache=true`。
- `features.batch_cli=true`。

结论：通过。Runtime config 已如实反映 Mock Demo + Real Search 双路径能力。

## C. Mock Demo API

验证接口：

```text
POST /api/v1/search/runs
GET /api/v1/search/runs/{run_id}
GET /api/v1/search/runs/{run_id}/result
GET /api/v1/search/runs/{run_id}/events
```

结果摘要：

- `POST /api/v1/search/runs` 返回 HTTP 201。
- run_id：`run_c40e28de187d`。
- status endpoint 返回 HTTP 200，`status=succeeded`。
- result endpoint 返回 HTTP 200。
- `highly_relevant_papers=3`。
- `partially_relevant_papers=2`。
- `synthesis=null`，符合 Mock Demo 默认行为。
- events endpoint 返回 HTTP 200，包含 `run_completed` 和 `connector_completed`。

结论：通过。Mock API 行为正常，未被 Real Search 变更影响。

## D. Real Search API

验证请求：

```json
{
  "query": "latest LLM reranking methods for scientific literature retrieval",
  "top_k": 10,
  "run_profile": "balanced",
  "constraints": {
    "time_range": {
      "end_year": 2026
    }
  },
  "options": {
    "enable_query_evolution": true,
    "enable_refchain": false,
    "stream_events": true
  }
}
```

结果摘要：

- `POST /api/v1/real/search/runs` 返回 HTTP 201。
- run_id：`run_real_9405c2c72f01`。
- 轮询状态最终为 `succeeded`，`current_stage=synthesis`。
- result endpoint 返回 HTTP 200。
- `latency_seconds=19.74022820906248`。
- `cache_hit_count=0`。
- `highly_relevant_papers=10`。
- `partially_relevant_papers=0`。
- events endpoint 返回 HTTP 200。
- events 包含：
  - `connector_completed`
  - `warning`
  - `cost_updated`

外部源诊断：

```text
OpenAlex search transient error on attempt 1/2: HTTP Error 503: Service Unavailable; retried
OpenAlex search failed: HTTP Error 503: Service Unavailable
source_error:openalex:OpenAlex search failed: HTTP Error 503: Service Unavailable
```

同时 arXiv 返回候选，Real Search 仍完成并返回结果。该情况说明 connector
错误可观测性有效，外部服务波动不会导致系统整体崩溃。

结论：通过，带外部源波动诊断。

## E. Real Search Cancel

验证流程：

1. 创建一个 Real Search run。
2. 立即调用：

   ```text
   POST /api/v1/real/search/runs/{run_id}/cancel
   ```

结果摘要：

- run_id：`run_real_66ad8d3e0f48`。
- cancel endpoint 返回 HTTP 200。
- cancel response `status=cancelled`。
- status endpoint 最终 `status=cancelled`。
- result endpoint 返回 HTTP 409。
- detail：`run cancelled`。

结论：通过。取消语义正常，cancelled run 不返回 result。

## F. 前端手动 Smoke

前端启动命令：

```bash
cd frontend
PORT=3001 npm run dev
```

说明：本机 `3000` 端口已有其它进程占用，本次使用后端默认 CORS 支持的
`3001` 端口验证。

访问地址：

```text
http://localhost:3001
```

Header 检查：

- 显示 `Mock + Real Search`。
- 显示 `Hybrid Runtime`。
- 显示 `no-LLM`。
- 显示 `backend ready`。

Mock Demo UI：

- 可创建 mock run。
- run_id 显示正常。
- `SSE Events` 正常展示。
- mock 论文卡片正常展示。
- Mock Demo 默认不显示 Synthesis Panel，符合预期。

Real Preview UI：

- 使用 query：

  ```text
  latest LLM reranking methods for scientific literature retrieval
  ```

- 参数：
  - `top_k=10`
  - `run_profile=balanced`
  - `current_year=2026`
  - `enable_query_evolution=true`
  - `enable_refchain=false`
- run_id：`run_real_0e6fb8791f67`。
- 前端显示 `Real Search Events`。
- 最终状态：`succeeded`。
- `API calls=12`。
- `latency=11.4s`。
- `cache hits=6`。
- 展示论文结果。
- 展示 OpenAlex 503 诊断。
- 展示 Citation-backed Synthesis Panel。
- 展示 Citation Graph Panel。
- 有 result 时 `Export JSON` / `Export Markdown` 按钮可见。

结论：通过。Real Preview 能展示运行过程、结果或诊断；外部 OpenAlex 503
被清楚呈现，不导致白屏或前端崩溃。

## G. Batch CLI

临时工作目录：

```text
/tmp/spar_acceptance_batch
```

输入文件：

```text
/tmp/spar_acceptance_batch/queries.jsonl
```

输入内容两条：

```json
{"case_id":"case_001","query":"latest LLM reranking methods for scientific literature retrieval","top_k":3,"run_profile":"fast","current_year":2026,"enable_query_evolution":false,"enable_refchain":false}
{"case_id":"case_002","query":"benchmark papers for scientific literature search agents","top_k":3,"run_profile":"fast","current_year":2026,"enable_query_evolution":false,"enable_refchain":false}
```

运行批量搜索：

```bash
SCHOLAR_AGENT_RETRIEVAL_CACHE=1 PYTHONPATH=src python scripts/run_search_batch.py \
  --input /tmp/spar_acceptance_batch/queries.jsonl \
  --output /tmp/spar_acceptance_batch/result.jsonl \
  --top-k 3 \
  --run-profile fast \
  --current-year 2026 \
  --max-workers 1
```

运行汇总：

```bash
PYTHONPATH=src python scripts/summarize_search_batch.py \
  --input /tmp/spar_acceptance_batch/result.jsonl \
  --output /tmp/spar_acceptance_batch/summary.md \
  --top-n 5
```

准备最小 gold/qrels：

```text
/tmp/spar_acceptance_batch/gold.jsonl
```

说明：本次 smoke 使用每个 case 第一篇返回论文生成最小 gold，仅用于验证评测
CLI 链路，不代表真实 benchmark。

运行评测：

```bash
PYTHONPATH=src python scripts/evaluate_search_batch.py \
  --batch-results /tmp/spar_acceptance_batch/result.jsonl \
  --gold /tmp/spar_acceptance_batch/gold.jsonl \
  --output /tmp/spar_acceptance_batch/eval.json \
  --k 1 \
  --k 3 \
  --include-partial
```

输出文件：

- `/tmp/spar_acceptance_batch/result.jsonl`
- `/tmp/spar_acceptance_batch/summary.md`
- `/tmp/spar_acceptance_batch/gold.jsonl`
- `/tmp/spar_acceptance_batch/eval.json`

批量搜索结果：

| case_id | status | latency_seconds | highly relevant | partially relevant | error |
| --- | --- | ---: | ---: | ---: | --- |
| case_001 | succeeded | 6.965 | 3 | 0 | - |
| case_002 | succeeded | 6.068 | 3 | 0 | - |

批量汇总结果：

- Total cases: 2
- Succeeded: 2
- Failed: 0
- Success rate: 100.0%
- Latency avg/min/max: 6.517 / 6.068 / 6.965 seconds
- Total API calls: 8
- Search API calls: 8
- Cache hits: 0

OpenAlex 在 batch 中同样返回 503 诊断；arXiv 返回候选，两个 case 均成功。

评测结果：

- `evaluated_case_count=2`
- `failed_cases=[]`
- `missing_gold_cases=[]`
- `missing_result_cases=[]`
- `Recall@1=1.0`
- `Recall@3=1.0`
- `Precision@1=1.0`
- `Precision@3=0.3333333333333333`
- `MRR=1.0`
- `nDCG@1=1.0`
- `nDCG@3=1.0`

结论：Batch CLI、Markdown 汇总 CLI、JSON 评测 CLI 均通过 smoke 验证。

## 外网与 LLM

- 是否调用 LLM：否。
- 是否访问外网：是，仅 Real Search API、Real Preview 和 Batch CLI 的
  OpenAlex/arXiv 真实检索验证。
- 外网状态：OpenAlex 多次 `503 Service Unavailable`；arXiv 可返回候选。
- 处理结果：OpenAlex 错误进入 `missing_evidence`、source stats 和 SSE events；
  系统继续返回 arXiv 结果。

## 已知问题

1. OpenAlex 在本次验收中多次返回 `503 Service Unavailable`，属于外部服务波动。
2. 当前系统仍是 no-LLM 规则版 MVP，不读取全文 PDF。
3. Batch smoke 的 gold/qrels 是临时最小 gold，只验证评测链路，不代表正式
   LitSearch/AstaBench benchmark。
4. 本次前端 dev server 使用 `3001`，因为本机 `3000` 端口已被其它进程占用。

## 最终状态

验收完成后停止了本地 FastAPI 和 Next.js dev server。随后执行：

```bash
git status --short
```

预期仅包含本文件：

```text
?? docs/design/final_engineering_acceptance.md
```

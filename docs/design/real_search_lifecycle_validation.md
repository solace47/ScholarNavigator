# Real Search Lifecycle Validation

## Scope

本次验证目标是手动检查真实 Real Search 生命周期，包括：

- 异步 run 创建与状态轮询
- SSE 事件回放
- API result 结构
- 前端 Mock Demo 回归
- 前端 Real Preview 结果展示
- citation-backed synthesis panel
- retrieval cache hit
- Real Search cancel

本次只做验证和记录，不修改业务代码、不修改 frontend/backend 功能代码、不修改
`third_party`。

## Commands

后端启动命令：

```bash
SCHOLAR_AGENT_RETRIEVAL_CACHE=1 \
REAL_SEARCH_MAX_WORKERS=1 \
REAL_SEARCH_BACKGROUND_WORKERS=2 \
PYTHONPATH=src uvicorn scholar_agent.app.main:app --host 127.0.0.1 --port 8000
```

前端要求启动命令：

```bash
cd frontend && npm run dev
```

实际执行说明：

- 本机 `localhost:3000` 已被一个 `Nuxt`/`com.docke` 进程占用。
- 直接执行 `npm run dev` 时，Next.js 自动切到 `localhost:3001`。
- 后端 CORS 当前允许 `localhost:3000` 和 `localhost:5173`，不允许
  `localhost:3001`，因此 3001 页面健康检查显示 backend offline。
- 为了完成浏览器验证，改用 CORS 已允许的端口：

```bash
cd frontend && NEXT_DISABLE_DEVTOOLS=1 PORT=5173 npm run dev
```

补充说明：

- 未设置 `NEXT_DISABLE_DEVTOOLS=1` 时，in-app browser 中出现 Next dev runtime
  报错：`Cannot assign to read only property 'stackTraceLimit' of function
  'function Error() { [native code] }'`，页面正文为空。
- 加上 `NEXT_DISABLE_DEVTOOLS=1` 后，页面可正常渲染并显示
  `backend ready`。

## Mock Demo Regression

后端 API 验证：

- `POST /api/v1/search/runs` 返回 `201`
- mock run id: `run_b614151a611b`
- `GET /api/v1/search/runs/{run_id}` 返回 `200`
- status: `succeeded`
- `GET /api/v1/search/runs/{run_id}/result` 返回 `200`
- mock result 包含 mock paper
- `GET /api/v1/search/runs/{run_id}/events` 返回 mock SSE 事件：
  `run_started`, `stage_started`, `stage_completed`,
  `connector_completed`, `run_completed`

前端 UI 验证：

- Mock Demo 模式可以启动。
- 页面显示 mock SSE。
- 页面显示 mock 论文卡片，包含 `SPAR:` 标题。
- Mock Demo 默认不显示 Synthesis Panel，符合 `synthesis=null/undefined`
  的预期。
- Mock Demo 不受 Real Search API、cache、cancel 改动影响。

## Real Preview Baseline

请求参数：

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

API 验证结果：

- `POST /api/v1/real/search/runs` 返回 `201`
- run id: `run_real_7f375fe50c01`
- 初始 status: `running`
- 初始 stage: `retrieval`
- 最终 status: `succeeded`
- result HTTP status: `200`
- SSE replay 包含：
  `run_started`, `stage_started`, `stage_completed`, `run_completed`

结果摘要：

- highly relevant papers: `10`
- partially relevant papers: `0`
- synthesis: present
- synthesis status: `succeeded`
- synthesis findings: `5`
- synthesis evidence rows: `22`
- citation coverage ratio: `0.8`
- `cost_report.search_api_call_count`: `12`
- `cost_report.cache_hit_count`: `0`
- `cost_report.search_rounds`: `6`
- `cost_report.judged_paper_count`: `23`
- latency: `19.12s`

Top 5 papers:

| Rank | Title | Year | Sources | Score |
| --- | --- | ---: | --- | ---: |
| 1 | CoRank: LLM-Based Compact Reranking with Document Features for Scientific Retrieval | 2025 | arxiv | 0.9043 |
| 2 | RankArena: A Unified Platform for Evaluating Retrieval, Reranking and RAG with Human and LLM Feedback | 2025 | arxiv | 0.8913 |
| 3 | Rethinking LLM Parametric Knowledge as Post-retrieval Confidence for Dynamic Retrieval and Reranking | 2025 | arxiv | 0.8913 |
| 4 | Scientific Paper Retrieval with LLM-Guided Semantic-Based Ranking | 2025 | arxiv | 0.8783 |
| 5 | Passage Query Methods for Retrieval and Reranking in Conversational Agents | 2025 | arxiv | 0.8653 |

`missing_evidence` 前几条：

- `OpenAlex search transient error on attempt 1/2: HTTP Error 503: Service Unavailable; retried`
- `OpenAlex search failed: HTTP Error 503: Service Unavailable`
- `source_error:openalex:OpenAlex search failed: HTTP Error 503: Service Unavailable`
- `query_evolution:round=1:seed_count=5:generated_count=3`

前端 UI 验证：

- Real Preview 创建 run 后显示 `run_real_*`。
- 初始页面状态可见 `queued` / `running`。
- Run Progress 显示 `Real Search Events`。
- 成功后状态显示 `succeeded`。
- Results 区域显示论文卡片。
- Results 区域显示 Citation-backed Synthesis Panel。
- cost cards 显示 API calls、latency、cache hits 等统计。

## Real Preview Cache Hit

同一个后端进程内，不重启后端，使用相同 query 和相同参数再次运行 Real Preview。

API 验证结果：

- run id: `run_real_d33ab0aaa140`
- 初始 status: `running`
- 最终 status: `succeeded`
- result HTTP status: `200`
- `cost_report.cache_hit_count`: `6`
- latency: `12.05s`
- baseline latency: `19.12s`
- latency 有明显下降。

`missing_evidence` 前几条：

- `OpenAlex search transient error on attempt 1/2: HTTP Error 503: Service Unavailable; retried`
- `OpenAlex search failed: HTTP Error 503: Service Unavailable`
- `retrieval_cache_hit:arxiv`
- `source_error:openalex:OpenAlex search failed: HTTP Error 503: Service Unavailable`
- `query_evolution:round=1:seed_count=5:generated_count=3`

结果展示：

- highly relevant papers: `10`
- partially relevant papers: `0`
- synthesis: present
- synthesis status: `succeeded`
- top 5 papers 与 baseline 一致。

前端 UI 验证：

- 使用页面 Real Preview 再次运行时，run id: `run_real_b09c3309ecd4`
- 初始显示 `queued` / `running`。
- 取消按钮可见。
- 最终显示 `succeeded`。
- Run Progress 显示 `CACHE HITS = 6`。
- Events 和 missing evidence 中可见 `retrieval_cache_hit`。
- 论文卡片和 Citation-backed Synthesis Panel 均可展示。

## Cancel Validation

API 取消验证：

- run id: `run_real_ed2c9fd75ab5`
- `POST /api/v1/real/search/runs/{run_id}/cancel` 返回 `200`
- cancel response status: `cancelled`
- cancel response current_stage: `cancelled`
- 等待后台检索后再次查询 status，仍为 `cancelled`
- `GET /api/v1/real/search/runs/{run_id}/result` 返回 `409`
- result detail: `run cancelled`
- SSE replay 包含：
  - `warning`，payload message 为 `run cancelled`
  - `run_completed`，payload status 为 `cancelled`

前端 UI 取消验证：

- 使用不同 query 启动 Real Preview：
  `latest graph neural retrieval expansion methods for scientific paper recommendation cancellation validation`
- run id: `run_real_4068ffed0f9b`
- 启动后快速点击“取消 Real Search”。
- 取消按钮调用成功，页面 status 显示 `cancelled`。
- 取消后不再显示“取消 Real Search”按钮。
- Results 区域没有更新为新论文卡片。
- 15 秒后后端 status 仍为 `cancelled`，result 仍为 `409 run cancelled`，
  后台结果没有覆盖 cancelled 状态。
- 前端在取消成功后按当前设计关闭 SSE，因此页面不会继续追加 cancel 后的
  `run_completed`；后端 SSE replay 已确认包含 `run_completed(status=cancelled)`。

## External Access

本次真实访问了 OpenAlex 和 arXiv。

观测到：

- arXiv 返回了论文候选，最终 top papers 来源为 `arxiv`。
- OpenAlex 多次返回 `HTTP Error 503: Service Unavailable`。
- retry/backoff 诊断和最终 source error 均进入 `missing_evidence`。

本次没有调用 LLM。

## Verification Commands

```bash
PYTHONPATH=src pytest -q
cd frontend && npm run lint
cd frontend && npm run build
```

结果：

- `PYTHONPATH=src pytest -q`: `157 passed, 1 warning`
- `cd frontend && npm run lint`: passed
- `cd frontend && npm run build`: passed

## Code Changes

本次未修改业务代码、frontend 功能代码、backend 功能代码或
`third_party`。

新增/修改文件：

- `docs/design/real_search_lifecycle_validation.md`

## Known Issues

- 本机 `localhost:3000` 被其他进程占用，实际前端验证使用
  `localhost:5173`。
- 直接使用自动切换出的 `localhost:3001` 会触发 CORS 限制，因为后端当前
  未允许该 origin。
- in-app browser 中 Next dev runtime 在未禁用 DevTools 时出现
  `stackTraceLimit` 只读属性报错；使用 `NEXT_DISABLE_DEVTOOLS=1` 后页面正常。
- OpenAlex 在本次验证中持续出现 503；系统能通过 warning/source error
  暴露诊断，并继续使用 arXiv 结果完成 Real Search。
- 取消能力不能强杀已经发出的外部 connector 请求；当前语义是标记 run 为
  cancelled，并在后台返回时忽略结果，防止状态被覆盖。

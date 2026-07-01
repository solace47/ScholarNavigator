# 评审阅读与运行快速指南

## 推荐阅读顺序

1. `docs/final/project_delivery_summary.md`  
   快速了解项目目标、架构、已实现功能和边界。
2. `docs/final/contest_technical_report_draft.md`  
   阅读完整技术报告初稿。
3. `docs/final/demo_script.md`  
   查看演示流程和现场兜底方案。
4. `docs/design/frontend_synthesis_validation.md`  
   查看前端 Synthesis Panel 的端到端验证记录。
5. `docs/design/real_preview_stability_validation.md`  
   查看 Real Preview 在降并发和 retry/backoff 下的真实检索表现。
6. `docs/design/evaluation_runbook.md`  
   查看离线评测 schema、metrics、fixtures 和脚本说明。

## 安装依赖

后端：

```bash
python3 -m pip install -r requirements.txt
```

前端：

```bash
cd frontend
npm install
```

## 启动后端

推荐评审演示使用低并发 Real Search，并启用 retrieval cache：

```bash
SCHOLAR_AGENT_RETRIEVAL_CACHE=1 \
REAL_SEARCH_MAX_WORKERS=1 \
REAL_SEARCH_BACKGROUND_WORKERS=2 \
PYTHONPATH=src uvicorn scholar_agent.app.main:app --host 127.0.0.1 --port 8000
```

后端 OpenAPI 文档：

```text
http://127.0.0.1:8000/docs
```

## 启动前端

```bash
cd frontend
npm run dev
```

浏览器打开：

```text
http://localhost:3000
```

如果 `3000` 被占用，Next.js 可能自动切到 `3001`。也可以显式使用
`5173`：

```bash
cd frontend
PORT=5173 npm run dev
```

后端默认 CORS allowlist 已支持：

- `http://localhost:3000`
- `http://127.0.0.1:3000`
- `http://localhost:3001`
- `http://127.0.0.1:3001`
- `http://localhost:5173`
- `http://127.0.0.1:5173`

如需增加自定义前端地址，可在启动后端时设置：

```bash
SCHOLAR_AGENT_CORS_ORIGINS=http://localhost:4321 \
PYTHONPATH=src uvicorn scholar_agent.app.main:app --host 127.0.0.1 --port 8000
```

`SCHOLAR_AGENT_CORS_ORIGINS` 使用逗号分隔，会与默认 allowlist 合并。

如果后端地址不同：

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

## 跑测试

后端测试：

```bash
PYTHONPATH=src pytest -q
```

前端 lint：

```bash
cd frontend && npm run lint
```

前端 build：

```bash
cd frontend && npm run build
```

当前记录中的预期结果：

- `pytest`：应全部通过，具体数量以当前测试集为准
- `npm run lint`：通过
- `npm run build`：通过

## 查看 Mock Demo

1. 打开前端页面。
2. 选择 `Mock Demo`。
3. 使用默认中文示例 query。
4. 点击启动搜索。
5. 观察：
   - run_id 出现。
   - Run Progress 展示 mock SSE 和阶段状态。
   - Results 展示 mock 论文卡片。
   - Synthesis Panel 不展示，这是预期行为，因为 Mock Demo 默认 `synthesis=null`。

## 查看 Real Preview

1. 选择 `Real Preview`。
2. 输入：

   ```text
   latest LLM reranking methods for scientific literature retrieval
   ```

3. 推荐参数：
   - `top_k=10`
   - `run_profile=balanced`
   - `current_year=2026`
   - `enable_query_evolution=true`
   - `enable_refchain=false`
4. 点击启动 Real Preview。
5. 观察：
   - run_id 以 `run_real_` 开头。
   - Run Progress 展示 `Real Search Events`。
   - queued/running/succeeded/failed 状态会通过轮询更新。
   - 如果 OpenAlex / arXiv 返回论文，Results 展示真实论文卡片。
   - 如果外部源失败，Results 展示“检索源失败/无候选”和 `missing_evidence` 诊断。
   - 如果返回 `synthesis`，Results 上方展示 Citation-backed Synthesis Panel。

Real Preview 支持：

- asynchronous real run lifecycle：`/api/v1/real/search/runs`
- SSE event replay：`/api/v1/real/search/runs/{run_id}/events`
- cancelling queued/running runs：`POST /api/v1/real/search/runs/{run_id}/cancel`
- retrieval cache diagnostics：`cost_report.cache_hit_count`

注意：Real Preview 会通过后端真实访问 OpenAlex / arXiv，可能受到 OpenAlex 503、arXiv 429 或 timeout 影响。

## 查看 Synthesis Panel

Real Preview 返回 `synthesis` 时，论文列表上方会展示 Citation-backed Synthesis Panel。

重点查看：

- `status`
- `answer_summary`
- key findings 和 citation keys
- citation coverage counters
- limitations / warnings
- evidence table

当前 MVP 边界：

- Synthesis 是规则版 metadata/evidence-row synthesis。
- 不代表系统读取了全文 PDF。
- 不调用 LLM。
- source error 和 metadata-only 限制会显示在 limitations 中。

## 离线评测样例

运行 sample fake fixture：

```bash
PYTHONPATH=src python scripts/eval_search_service.py \
  --fixtures-dir datasets/eval_fixtures/sample \
  --output-root outputs/eval_runs \
  --run-id sample
```

生成 Markdown 摘要：

```bash
PYTHONPATH=src python scripts/summarize_eval_results.py \
  outputs/eval_runs/sample/result.json
```

说明：该 sample 是小型手写 fake fixture，只用于验证评测链路，不是完整 LitSearch / AstaBench benchmark。

## 批量运行 SearchService

如果需要从本地 JSONL query 文件批量运行真实 SearchService，可使用：

```bash
PYTHONPATH=src python scripts/run_search_batch.py \
  --input path/to/queries.jsonl \
  --output outputs/batch_runs/result.jsonl \
  --top-k 10 \
  --run-profile balanced \
  --current-year 2026 \
  --enable-query-evolution \
  --max-workers 2
```

输入 JSONL 每行至少包含 `query`：

```json
{"case_id":"case_001","query":"latest LLM reranking methods for scientific literature retrieval","top_k":10,"run_profile":"balanced","current_year":2026,"enable_query_evolution":true,"enable_refchain":false}
{"query":"survey of agentic scientific paper search"}
```

输出 JSONL 每行包含：

- `case_id`
- `query`
- `status`
- `result`：成功时为 `SearchRunResultResponse` JSON，失败时为 `null`
- `error`
- `latency_seconds`

默认会继续处理单条失败并写出 failed 行；使用 `--fail-fast` 可在第一条失败后返回非零。缺失或空白 `case_id` 会自动生成 `row_1`、`row_2`。空 query 作为单条 failed 输出；非法 JSONL 或缺失输入文件会直接返回非零。

注意：该脚本调用默认 `SearchService` 时可能真实访问 OpenAlex / arXiv；启用 RefChain 时还可能访问 OpenAlex references。测试中通过 monkeypatch 替换 `SearchService`，不访问外网。

批量结果可汇总成 Markdown：

```bash
PYTHONPATH=src python scripts/summarize_search_batch.py \
  --input outputs/batch_runs/result.jsonl \
  --output outputs/batch_runs/summary.md \
  --top-n 10
```

不传 `--output` 时会直接打印到 stdout。汇总报告包含成功率、延迟、API
调用、cache hit、token 估计、逐 case 简表、Top papers、missing evidence /
warning 统计、`source_error` 统计和 failed cases。该汇总脚本只读取本地
JSONL，不访问外网。

## 关键边界提醒

- 当前系统是 no-LLM 规则版 MVP。
- 当前没有读取全文 PDF。
- 当前没有完整接入 LitSearch / AstaBench benchmark。
- 前端不读取、不保存、不展示任何 API Key。
- Mock Demo API 行为保持不变；Real Preview 走独立 Real Search lifecycle。
- OpenAlex / arXiv 网络错误会被记录并展示，不代表系统逻辑崩溃。

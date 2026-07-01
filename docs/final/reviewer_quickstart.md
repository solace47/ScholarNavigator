# 评审阅读与运行快速指南

## 推荐阅读顺序

1. `docs/final/project_delivery_summary.md`  
   快速了解项目目标、Real Search only 架构、已实现功能和边界。
2. `docs/final/contest_technical_report_draft.md`  
   阅读完整技术报告初稿。
3. `docs/final/demo_script.md`  
   查看演示流程和现场诊断说明。
4. `docs/final/submission_manifest.md`  
   查看最终提交文件地图和检查命令。
5. `docs/final/final_submission_readiness.md`  
   注意：该文件是切换到 Real Search only 之前的 readiness 历史记录，当前需要重新验收。
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

检查 runtime config：

```bash
curl http://127.0.0.1:8000/api/v1/runtime/config
```

预期看到：

- `mode` 为 `real_search`。
- 默认 `llm.available=false`，表示未配置后端 LLM provider。
- 如果后端设置了 OpenAI-compatible LLM 环境变量并启用 Query Understanding / Judgement，则 `llm.available=true`，并可看到 `features.llm_query_understanding=true` / `features.llm_judgement=true`。
- `openalex` / `arxiv` connector 可用于 Real Search。
- `semantic_scholar` / `pubmed` 仍为未实现或不可用。
- 不再包含产品级示例 connector。

OpenAlex / arXiv 可用于 Real Search 是预期；它们仍可能受外部服务 `503`、`429` 或 timeout 影响，相关诊断会进入 Real Search events、`missing_evidence` 和 source stats。

可选 LLM Query Understanding / Judgement 只在后端配置，不需要也不允许前端读取 key。推荐复制模板到项目根目录 `.env`：

```bash
cp .env.example .env
```

然后编辑 `.env`：

```dotenv
SCHOLAR_AGENT_LLM_PROVIDER=openai_compatible
SCHOLAR_AGENT_LLM_BASE_URL=https://api.openai.com/v1
SCHOLAR_AGENT_LLM_API_KEY=...
SCHOLAR_AGENT_LLM_MODEL=gpt-4.1-mini
SCHOLAR_AGENT_ENABLE_LLM_QUERY_UNDERSTANDING=1
SCHOLAR_AGENT_ENABLE_LLM_JUDGEMENT=1
SCHOLAR_AGENT_LLM_JUDGEMENT_BATCH_SIZE=8
```

FastAPI 后端启动时会自动读取项目根目录 `.env`；真实 shell/部署环境变量优先，不会被 `.env` 覆盖。

当前 LLM 只用于 Query Understanding 和 Judgement。LLM Judgement 只判断候选论文 metadata，不读取全文 PDF，不生成新论文。禁用、配置缺失或调用失败时，系统会回到规则版 Query Understanding / Judgement，并在 warnings / Real Search Events / missing evidence 中显示明确诊断；不会返回示例数据。

Real Search 的 in-memory run store 会自动清理 terminal runs。可选环境变量：

```bash
REAL_SEARCH_RUN_TTL_SECONDS=3600
REAL_SEARCH_MAX_STORED_RUNS=200
```

清理只删除 `succeeded` / `failed` / `cancelled`，不会删除 `queued` / `running`。

## 启动前端

```bash
cd frontend
npm run dev
```

浏览器打开：

```text
http://localhost:3000
```

如果 `3000` 被占用，Next.js 可能自动切到 `3001`。后端默认 CORS allowlist 已支持：

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

## 跑测试

```bash
PYTHONPATH=src pytest -q
cd frontend && npm run lint
cd frontend && npm run build
```

## 查看 Real Search

1. 打开前端页面。
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
4. 点击启动 Real Search。
5. 观察：
   - run_id 以 `run_real_` 开头。
   - Run Progress 展示 `Real Search Events`。
   - queued/running/succeeded/failed 状态会通过轮询更新。
   - SSE 中可见 `connector_completed`、`warning`、`cost_updated`。
   - 如果 OpenAlex / arXiv 返回论文，Results 展示真实论文卡片。
   - 如果外部源失败，Results 展示“检索源失败/无候选”和 `missing_evidence` 诊断。
   - 如果返回 `synthesis`，Results 上方展示 Citation-backed Synthesis Panel。
   - 如果后端返回 citation graph，Results 展示 Citation Graph Panel。
   - 有 result 时可以点击 `Export JSON` / `Export Markdown`。

Real Search 支持：

- asynchronous real run lifecycle：`/api/v1/real/search/runs`
- SSE event replay：`/api/v1/real/search/runs/{run_id}/events`
- cancelling queued/running runs：`POST /api/v1/real/search/runs/{run_id}/cancel`
- retrieval cache diagnostics：`cost_report.cache_hit_count`
- connector observability：`connector_completed` event 会展示 source、returned_count、latency、cache_hit、error_message
- cost updates：`cost_updated` event 会回放后端 cost_report

注意：产品路径不再提供示例检索兜底。OpenAlex / arXiv 网络错误会被记录并展示，不代表系统逻辑崩溃。

## 查看 Synthesis Panel

Real Search 返回 `synthesis` 时，论文列表上方会展示 Citation-backed Synthesis Panel。

当前 MVP 边界：

- Synthesis 是规则版 metadata/evidence-row synthesis。
- 不代表系统读取了全文 PDF。
- 不调用 LLM。
- source error 和 metadata-only 限制会显示在 limitations 中。

## 查看 Citation Graph 与导出

有 result 时，如果 `citation_graph.nodes` 或 `citation_graph.edges` 非空，Results 会展示 Citation Graph Panel。该 panel 只展示后端返回的结构化关系，不在前端推断缺失边。

有 result 时还会展示：

- `Export JSON`
- `Export Markdown`

导出完全在浏览器本地完成，不上传后端，不重新检索。

## 批量运行 SearchService

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

批量结果可汇总成 Markdown：

```bash
PYTHONPATH=src python scripts/summarize_search_batch.py \
  --input outputs/batch_runs/result.jsonl \
  --output outputs/batch_runs/summary.md
```

如需用 gold/qrels JSONL 评测：

```bash
PYTHONPATH=src python scripts/evaluate_search_batch.py \
  --batch-results outputs/batch_runs/result.jsonl \
  --gold path/to/qrels.jsonl \
  --output outputs/batch_runs/eval.json \
  --k 5 \
  --k 10 \
  --include-partial
```

## 关键边界提醒

- 当前系统仅 Query Understanding 和 Judgement 可选使用 LLM；Reranking、Synthesis 仍为规则版。
- 当前没有读取全文 PDF。
- 当前没有完整接入 LitSearch / AstaBench benchmark。
- 前端不读取、不保存、不展示任何 API Key。
- 产品路径不再提供示例检索兜底。
- OpenAlex / arXiv 网络错误会被记录并展示，不代表系统逻辑崩溃。
- 后续可继续把 LLM 扩展到 Reranking / Synthesis；provider 不可用时也应返回明确错误或诊断。

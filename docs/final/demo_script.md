# ScholarNavigator 演示脚本

## 演示前准备

确认依赖已安装：

```bash
python3 -m pip install -r requirements.txt
cd frontend && npm install
```

可选环境变量：

```bash
export OPENALEX_MAILTO=your_email@example.com
```

说明：

- `OPENALEX_MAILTO` 仅用于 OpenAlex polite pool，不是 API Key。
- 前端不会读取、保存或展示任何 API Key。
- 当前演示通过后端 Real Search lifecycle 访问真实 OpenAlex / arXiv。
- 当前 LLM 只可选用于 Query Understanding；默认不需要 LLM key，不读取全文 PDF。
- 产品路径不再提供示例数据兜底；外部源失败时展示 diagnostics。

如需演示 LLM Query Understanding，可只在后端设置：

```bash
SCHOLAR_AGENT_LLM_PROVIDER=openai_compatible
SCHOLAR_AGENT_LLM_BASE_URL=https://api.openai.com/v1
SCHOLAR_AGENT_LLM_API_KEY=...
SCHOLAR_AGENT_LLM_MODEL=gpt-4.1-mini
SCHOLAR_AGENT_ENABLE_LLM_QUERY_UNDERSTANDING=1
```

前端不输入、不读取、不展示 LLM key。

建议演示前执行：

```bash
PYTHONPATH=src pytest -q
cd frontend && npm run lint
cd frontend && npm run build
```

## 后端启动命令

推荐演示时启用 retrieval cache，并降低真实检索并发：

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

Runtime config 检查：

```bash
curl http://127.0.0.1:8000/api/v1/runtime/config
```

现场应确认：

- `mode=real_search`。
- 默认 `llm.available=false`；配置后端 LLM provider 后可为 `true`。
- `features.llm_query_understanding=true` 仅表示 Query Understanding 可选走 LLM JSON 增强。
- OpenAlex / arXiv connector 可用于 Real Search。
- `features.real_search`、`real_search_cancel`、`real_search_sse`、`retrieval_cache`、`batch_cli` 可见。
- connectors 中不再有产品级示例 connector。

## 前端启动命令

```bash
cd frontend
npm run dev
```

打开：

```text
http://localhost:3000
```

如果 `3000` 被占用，Next.js 可能切换到 `3001`。后端默认 CORS 支持 `3000`、`3001`、`5173`。

## Real Search 演示步骤

1. 打开前端页面。
2. 确认 Header 显示 `Real Search Runtime`、`Real Search` 和 `backend ready`；未配置 LLM 时会显示规则解析 / no LLM 语义。
3. 输入推荐 query：

   ```text
   latest LLM reranking methods for scientific literature retrieval
   ```

4. 设置参数：
   - `top_k=10`
   - `run_profile=balanced`
   - `current_year=2026`
   - `enable_query_evolution=true`
   - `enable_refchain=false`
5. 点击启动 Real Search。
6. 讲解运行过程：
   - run_id 应以 `run_real_` 开头。
   - Run Progress 展示 `Real Search Events`。
   - status 会经历 queued / running / succeeded；失败时显示 failed。
   - SSE 中可看到 `connector_completed`、`warning`、`cost_updated`。
   - 检索运行中可点击“取消 Real Search”；取消后 status 为 cancelled。
7. 如果返回论文：
   - 展示前几篇论文卡片。
   - 讲解 relevance_score、category、ranking_reason 和 evidence。
   - 指出 sources 中可能出现 `arxiv`、`openalex`。
   - 讲解 cost_report 中的 API calls、latency、cache_hit_count。
8. 如果没有论文：
   - 展示“检索源失败/无候选”。
   - 展示 `missing_evidence` 中的 OpenAlex / arXiv 错误诊断。
   - 说明系统没有静默回退到示例数据。

## Synthesis Panel 演示步骤

Real Search 返回 `synthesis` 时，在论文列表上方展示 Citation-backed Synthesis Panel。

建议讲解：

1. status。
2. answer_summary。
3. key_findings 与 citation keys。
4. citation_coverage。
5. limitations / warnings。
6. evidence_table。

必须明确说明：

- 当前 synthesis 是规则版 metadata/evidence-row synthesis。
- citation-backed 表示结论绑定到当前候选论文证据行。
- 不代表系统已读取全文 PDF。
- 当前 synthesis 没有调用 LLM。

## Citation Graph Panel 演示步骤

有 result 且后端返回 `citation_graph` 时，Results 区域会展示 Citation Graph Panel。

建议讲解：

1. nodes 数量和 edges 数量。
2. node 列表中的 label、id、rank。
3. edge 列表中的 source、target、relation。
4. 如果只有 nodes 没有 edges，说明“当前无引用边/关系边”是合法状态。

必须明确说明：

- graph 只展示后端返回的结构化关系。
- 前端不推断未返回的引用关系。
- `enable_refchain=false` 时 edges 可能为空。

## Export JSON / Markdown 演示步骤

有 result 时，Results 区域会显示：

- `Export JSON`
- `Export Markdown`

建议讲解：

1. 导出内容来自当前页面已有 `SearchRunResultResponse`。
2. JSON 导出包含完整 result 对象。
3. Markdown 导出包含 query analysis、search plan、cost report、synthesis、论文列表、citation graph 和 missing evidence。
4. 导出完全在浏览器本地通过 Blob 完成，不上传后端，不重新检索。

## OpenAlex 503 或 arXiv 超时的解释

如果演示中出现 OpenAlex 503：

- 这是 OpenAlex 上游服务临时不可用或压力较高。
- 系统已经做轻量 retry/backoff。
- 如果重试后仍失败，错误会进入 `source_stats`、`missing_evidence`、Real Search Events 和 synthesis limitations。
- arXiv 如果可用，系统仍可继续输出 arXiv 候选。

如果演示中出现 arXiv 429 或 timeout：

- 这是公开检索 API 的限流或网络超时。
- 系统不会把单个 source 失败扩散成整体崩溃。
- 前端会展示“检索源失败/无候选”或保留可用 source 的结果。

如果 OpenAlex 和 arXiv 同时失败：

- 直接展示 `missing_evidence` 诊断。
- 说明当前产品路径不会返回示例数据。
- 可切换展示 Batch CLI 或历史验证记录中的结构化 diagnostics，但不要声称实时检索成功。

## 推荐演示 Query

主推英文 query：

```text
latest LLM reranking methods for scientific literature retrieval
```

中文 query：

```text
请帮我找近三年关于 LLM reranking 在科研论文检索中的代表性方法和评测数据集
```

偏 benchmark query：

```text
recent benchmark datasets for evaluating retrieval augmented generation and LLM reranking
```

偏综述 query：

```text
survey of neural information retrieval methods for scientific literature search
```

## 讲解词草稿

开场：

> ScholarNavigator 面向华为企业赛题三，目标是把复杂自然语言学术查询转成一个可解释、可评测、可控成本的论文搜索流程。系统采用前后端分离架构，前端只负责交互和可视化，后端负责所有检索源调用、agent pipeline、评测和成本统计。

Real Search：

> 当前产品路径只保留 Real Search lifecycle。创建 run 后，前端通过状态轮询和 SSE 展示真实运行过程。外部 OpenAlex 或 arXiv 失败时，系统返回明确 diagnostics，不会静默展示示例数据。

Synthesis：

> Synthesis Panel 是当前的规则版 citation-backed synthesis。它只使用候选论文 metadata 和 evidence rows，不读取全文 PDF，也不调用 LLM。每条 finding 都绑定到 R1、R2 这样的 citation key，限制和 source error 也会明确展示。

边界：

> 当前版本已经接入真实 LLM provider 基础设施，但只用于 Query Understanding。即使 LLM provider 不可用，系统也不会返回示例数据，而是记录诊断并使用规则解析继续真实检索。

## 预期展示亮点

- 前后端分离，前端不接触 API Key。
- Real Search lifecycle 支持 create/status/result/events/cancel。
- Real Search Events 暴露 connector_completed、warning、cost_updated。
- OpenAlex / arXiv 真实检索源接入，且错误可观测。
- retrieval cache 可降低重复检索压力，并在 cost_report 中显示 cache_hit_count。
- Query Understanding 支持规则版和可选 LLM JSON 增强；Judgement、Reranker、Query Evolution、RefChain、Synthesis 仍为规则版。
- Citation-backed Synthesis Panel 明确显示引用 key、证据表和 limitations。
- Citation Graph Panel 和本地 JSON / Markdown 导出增强结果可解释与复盘能力。
- 默认关闭 LLM 时 token 成本为 0；启用 LLM Query Understanding 后，当前阶段还未完整统计 token 成本。
- Batch CLI 与离线评测链路具备批量搜索、汇总、gold/qrels 评测能力。

## 演示失败处理

1. 后端未启动：
   - 前端会提示“后端服务不可用，请先启动 FastAPI Real Search API”。
   - 按后端启动命令重新启动。
2. 前端端口冲突：
   - 使用 Next.js 输出的新端口。
   - 后端默认 CORS 支持常见本地端口。
3. Real Search 网络失败：
   - 展示 `missing_evidence`。
   - 解释 OpenAlex / arXiv 是真实外部依赖。
   - 不展示示例数据兜底。
4. 没有 synthesis：
   - 若无候选，Synthesis 可能进入 insufficient-evidence 或只展示 limitations。
5. 真实检索运行时间过长：
   - 点击“取消 Real Search”演示 cancel 语义。
   - 说明当前取消不能强杀已经发出的外部请求，但会忽略后续结果并停止前端等待。

## Batch CLI / Evaluation CLI 备选展示

批量搜索：

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

汇总 Markdown：

```bash
PYTHONPATH=src python scripts/summarize_search_batch.py \
  --input outputs/batch_runs/result.jsonl \
  --output outputs/batch_runs/summary.md
```

gold/qrels 评测：

```bash
PYTHONPATH=src python scripts/evaluate_search_batch.py \
  --batch-results outputs/batch_runs/result.jsonl \
  --gold path/to/qrels.jsonl \
  --output outputs/batch_runs/eval.json \
  --k 5 \
  --k 10 \
  --include-partial
```

说明：

- 批量搜索默认会真实访问 OpenAlex / arXiv。
- 汇总和评测脚本只读取本地文件，不访问外网。
- 当前项目没有完整接入 LitSearch / AstaBench benchmark，CLI 只是评测链路基础设施。

## 演示结束

停止后端和前端：

```text
Ctrl-C
```

可补充展示的文档：

- `docs/final/project_delivery_summary.md`
- `docs/final/reviewer_quickstart.md`
- `docs/final/submission_manifest.md`

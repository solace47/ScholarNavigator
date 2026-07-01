# ScholarNavigator

ScholarNavigator 是面向“中国研究生人工智能创新大赛”华为企业赛题三“科研场景下复杂学术查询的智能论文搜索与推荐”的前后端分离系统。

一句话说明：当前版本是 **Real Search only runtime 的规则版 MVP，并已接入可选真实 LLM Query Understanding 基础设施**。系统已形成可演示、可测试、可观测的真实论文检索闭环；默认不要求 LLM key，不读取全文 PDF，也不声称已完成完整 LitSearch / AstaBench benchmark。

本仓库参考 SPAR、PaSa、PaperQA2、ai2-scholarqa-lib、LitSearch 和 AstaBench 的相关思想实现参赛 MVP。原 SPAR 论文入口：[SPAR paper](https://arxiv.org/abs/2507.15245)。

## 核心能力

- Real Search lifecycle：`create/status/result/events/cancel` 独立真实检索路径。
- Real Search SSE：展示 `connector_completed`、`warning`、`cost_updated` 等事件。
- OpenAlex / arXiv connectors：支持真实检索、timeout、轻量 retry/backoff 和错误诊断。
- Connector observability：source errors 会进入 source stats、warnings、missing evidence 和前端诊断。
- Retrieval cache：轻量 in-memory cache，`cache_hit_count` 进入 cost report。
- Query Understanding：默认规则版解析，可选通过后端环境变量启用 OpenAI-compatible LLM JSON 增强。
- Judgement / Reranking / Synthesis：仍为规则版，不调用 LLM。
- Query Evolution / RefChain：可选规则版扩展阶段。
- Citation-backed Synthesis Panel：基于 metadata/evidence rows 的规则版 synthesis 展示。
- Citation Graph Panel：展示后端返回的 citation graph nodes / edges，不做前端推断。
- JSON / Markdown export：浏览器本地导出当前 `SearchRunResultResponse`。
- Batch CLI / summary CLI / evaluation CLI：支持批量搜索、Markdown 汇总和 gold/qrels 指标评测。

## 快速启动

安装后端依赖：

```bash
python3 -m pip install -r requirements.txt
```

安装前端依赖：

```bash
cd frontend
npm install
```

启动后端：

```bash
SCHOLAR_AGENT_RETRIEVAL_CACHE=1 \
REAL_SEARCH_MAX_WORKERS=1 \
REAL_SEARCH_BACKGROUND_WORKERS=2 \
PYTHONPATH=src uvicorn scholar_agent.app.main:app --host 127.0.0.1 --port 8000
```

检查 runtime config：

```bash
curl http://127.0.0.1:8000/api/v1/runtime/config
```

预期要点：

- `mode=real_search`
- 默认 `llm.available=false`；配置真实 provider 后可为 `true`
- OpenAlex / arXiv connector 可用于 Real Search
- `real_search`、`real_search_cancel`、`real_search_sse`、`retrieval_cache`、`batch_cli` feature 可见
- `llm_query_understanding=true` 仅在 provider 可用且启用对应开关时出现

## 可选 LLM Query Understanding

本轮只把真实 LLM 用于 Query Understanding，不把 Judgement、Reranking 或 Synthesis 改成 LLM。

默认关闭：

```bash
SCHOLAR_AGENT_LLM_PROVIDER=disabled
```

启用 OpenAI-compatible Chat Completions：

```bash
SCHOLAR_AGENT_LLM_PROVIDER=openai_compatible
SCHOLAR_AGENT_LLM_BASE_URL=https://api.openai.com/v1
SCHOLAR_AGENT_LLM_API_KEY=...
SCHOLAR_AGENT_LLM_MODEL=gpt-4.1-mini
SCHOLAR_AGENT_ENABLE_LLM_QUERY_UNDERSTANDING=1
```

可选超时：

```bash
SCHOLAR_AGENT_LLM_TIMEOUT_SECONDS=30
```

安全边界：

- API key 只从后端环境变量读取。
- runtime config 只返回 provider、model、base_url_host、available 和 reason，不返回 API key。
- LLM 禁用、配置缺失或调用失败时，系统使用确定性的规则版 Query Understanding，并在 `SearchPlan.warnings`、SSE warning 和 `missing_evidence` 中记录 `llm_query_understanding_disabled` 或 `llm_query_understanding_failed:<reason>`。
- 这不是示例数据 fallback；检索仍走真实 OpenAlex / arXiv。
- 当前尚未完整统计 LLM token 成本，`cost_report.llm_call_count` 仍可能为 0。

启动前端：

```bash
cd frontend
npm run dev
```

默认访问：

```text
http://localhost:3000
```

如果 `3000` 被占用，Next.js 可能切到 `3001`。后端默认 CORS allowlist 支持 `3000`、`3001`、`5173` 的 localhost / 127.0.0.1；自定义地址可通过 `SCHOLAR_AGENT_CORS_ORIGINS` 增加。

## 常用验证命令

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

运行结果以本地命令输出为准；最新提交前应重新执行上述三条命令。

## 推荐阅读顺序

1. `docs/final/project_delivery_summary.md`
2. `docs/final/contest_technical_report_draft.md`
3. `docs/final/demo_script.md`
4. `docs/final/reviewer_quickstart.md`
5. `docs/design/final_engineering_acceptance.md`
6. `docs/final/submission_manifest.md`

## 主要目录

```text
src/scholar_agent/        后端核心包
src/scholar_agent/app/    FastAPI 应用和 API 路由
src/scholar_agent/agents/ QueryUnderstanding/Judgement/Reranker 等规则版 agents
src/scholar_agent/connectors/ OpenAlex 和 arXiv connectors
src/scholar_agent/services/ SearchService、API mapper
src/scholar_agent/evaluation/ 离线评测 metrics/evaluator/fixtures
frontend/                Next.js + TypeScript + Tailwind 前端
scripts/                 批量搜索、汇总和评测 CLI
docs/design/             架构、runbook、验证记录
docs/final/              参赛交付材料
tests/                   后端测试
```

## Batch CLI

批量运行 SearchService：

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

生成 Markdown 汇总：

```bash
PYTHONPATH=src python scripts/summarize_search_batch.py \
  --input outputs/batch_runs/result.jsonl \
  --output outputs/batch_runs/summary.md
```

使用 gold/qrels JSONL 评测：

```bash
PYTHONPATH=src python scripts/evaluate_search_batch.py \
  --batch-results outputs/batch_runs/result.jsonl \
  --gold path/to/qrels.jsonl \
  --output outputs/batch_runs/eval.json \
  --k 5 \
  --k 10 \
  --include-partial
```

说明：批量搜索默认可能真实访问 OpenAlex / arXiv；汇总和评测脚本只读取本地文件。

## 边界与非目标

- 产品路径不再提供示例检索模式或静默 fallback；外部源失败时返回明确 diagnostics。
- 当前 LLM 仅可选用于 Query Understanding；Judgement、Reranking、Synthesis 仍为规则版。
- 当前不读取全文 PDF，Synthesis 只基于 metadata 和 evidence rows。
- 当前未接入完整 LitSearch / AstaBench benchmark，只有本地 fake fixture 和 CLI 评测链路。
- OpenAlex / arXiv 是真实外部依赖，可能出现 503、429 或 timeout；系统会降级并展示诊断。
- Real Search 当前使用 in-memory run store，不是生产级持久化任务队列。
- Semantic Scholar 和 PubMed connector 尚未实现。
- 前端不读取、不保存、不展示任何 API Key。

## 提交前检查

```bash
PYTHONPATH=src pytest -q
cd frontend && npm run lint
cd frontend && npm run build
git status --short
```

不要提交：

- `node_modules`
- `.next`
- `__pycache__`
- `.pytest_cache`
- `.DS_Store`
- secrets / API keys

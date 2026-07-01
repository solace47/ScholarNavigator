# ScholarNavigator

ScholarNavigator 是面向“中国研究生人工智能创新大赛”华为企业赛题三“科研场景下复杂学术查询的智能论文搜索与推荐”的前后端分离系统。

一句话说明：当前版本是 **Mock Demo + Real Search hybrid runtime 的 no-LLM 规则版 MVP**。系统已形成可演示、可测试、可观测的论文检索闭环，但不调用 LLM，不读取全文 PDF，也不声称已完成完整 LitSearch / AstaBench benchmark。

本仓库参考 SPAR、PaSa、PaperQA2、ai2-scholarqa-lib、LitSearch 和 AstaBench 的相关思想实现参赛 MVP。原 SPAR 论文入口：[SPAR: Scholar Paper Retrieval with LLM-based Agents for Enhanced Academic Search](https://arxiv.org/abs/2507.15245)。

## 核心能力

- Mock Demo：稳定 mock run lifecycle，用于现场演示兜底。
- Real Search lifecycle：`create/status/result/events/cancel` 独立真实检索路径。
- Real Search SSE：展示 `connector_completed`、`warning`、`cost_updated` 等事件。
- OpenAlex / arXiv connectors：支持真实检索、timeout、轻量 retry/backoff 和错误诊断。
- Connector observability：source errors 会进入 source stats、warnings、missing evidence 和前端诊断。
- Retrieval cache：轻量 in-memory cache，`cache_hit_count` 进入 cost report。
- Query Understanding / Judgement / Reranking：规则版 no-LLM pipeline。
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

- `mode=hybrid`
- `llm.available=false`
- OpenAlex / arXiv connector 可用于 Real Search
- `real_search`、`real_search_cancel`、`real_search_sse`、`retrieval_cache`、`batch_cli` feature 可见

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

最终工程验收记录中的结果：

- `PYTHONPATH=src pytest -q`：`190 passed, 1 warning`
- `cd frontend && npm run lint`：通过
- `cd frontend && npm run build`：通过

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

- 当前是 no-LLM 规则版 MVP，没有调用 LLM。
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

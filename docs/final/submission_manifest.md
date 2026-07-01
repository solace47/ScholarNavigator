# 最终提交清单

本文档用于评审和提交前快速确认 ScholarNavigator 的代码、文档、验证结果和边界。

## 项目定位

- 项目名称：ScholarNavigator
- 对应赛题：华为企业赛题三，科研场景下复杂学术查询的智能论文搜索与推荐
- 当前形态：Mock Demo + Real Search hybrid runtime 的 no-LLM 规则版 MVP
- 核心目标：提供可演示、可测试、可观测、可评测的学术论文搜索与推荐闭环

## 最终提交文件地图

### Backend 关键目录

- `src/scholar_agent/app/`  
  FastAPI 应用入口、CORS、Mock API、Real Search lifecycle、runtime config。
- `src/scholar_agent/core/`  
  Paper、Search、API、Synthesis、Evaluation 等 Pydantic schema，以及 dedup 逻辑。
- `src/scholar_agent/connectors/`  
  OpenAlex / arXiv connector，含 detailed result、retry/backoff、错误诊断和 OpenAlex references。
- `src/scholar_agent/agents/`  
  QueryUnderstanding、Judgement、Reranker、QueryEvolution、RefChain、Synthesis 等规则版 agents。
- `src/scholar_agent/services/`  
  SearchService、SearchServiceOutput、API mapper。
- `src/scholar_agent/evaluation/`  
  metrics、offline evaluator、fixture loader。
- `tests/`  
  Mock API、Real Search API、connectors、retriever、SearchService、agents、mapper、evaluation 和 CLI 测试。

### Frontend 关键目录

- `frontend/src/app/`  
  Next.js 应用入口。
- `frontend/src/components/`  
  ScholarNavigator 工作台、Run Progress、Results、Synthesis Panel、Citation Graph Panel、导出按钮。
- `frontend/src/lib/`  
  后端 API client、导出工具。
- `frontend/src/types/`  
  与后端 API response 对齐的 TypeScript 类型。
- `frontend/README.md`  
  前端启动、Mock Demo / Real Preview、Synthesis、Citation Graph、导出和 CORS 说明。

### Final 文档

- `docs/final/project_delivery_summary.md`  
  项目目标、架构、核心功能、参考系统关系、真实检索能力、测试结果和边界。
- `docs/final/contest_technical_report_draft.md`  
  参赛技术报告初稿。
- `docs/final/demo_script.md`  
  现场演示流程、讲解词、失败兜底和 CLI 备选展示。
- `docs/final/reviewer_quickstart.md`  
  评审快速启动和阅读指南。
- `docs/final/submission_manifest.md`  
  本提交清单。

### 关键验证记录

- `docs/design/final_engineering_acceptance.md`  
  最终工程验收记录，覆盖测试、构建、runtime config、Mock Demo API、Real Search API、cancel、前端 smoke 和 Batch CLI。

### Batch / Evaluation CLI

- `scripts/run_search_batch.py`  
  从 JSONL query 文件批量运行 SearchService，输出 JSONL。
- `scripts/summarize_search_batch.py`  
  读取 batch result JSONL，生成 Markdown 汇总。
- `scripts/evaluate_search_batch.py`  
  读取 batch result JSONL 和 gold/qrels JSONL，计算 Recall@K、Precision@K、MRR、nDCG。

## 最终验收结果

最终工程验收记录中通过：

- `PYTHONPATH=src pytest -q`：`190 passed, 1 warning`
- `cd frontend && npm run lint`：passed
- `cd frontend && npm run build`：passed

唯一 warning 是既有 Starlette / TestClient deprecation warning，不影响当前功能验证。

## 演示建议

1. 先检查 runtime config：

   ```bash
   curl http://127.0.0.1:8000/api/v1/runtime/config
   ```

   重点说明 `mode=hybrid`、`llm.available=false`、OpenAlex / arXiv 可用于 Real Search。

2. 优先演示 Mock Demo：
   - 稳定可控。
   - 展示 run 创建、Mock SSE、论文卡片。
   - 可作为网络失败时的兜底。

3. 再演示 Real Preview：
   - 走 `/api/v1/real/search/runs` lifecycle。
   - 展示 queued / running / succeeded / failed 状态。
   - 展示 Real Search Events，包括 `connector_completed`、`warning`、`cost_updated`。
   - 展示论文卡片、Synthesis Panel、Citation Graph Panel 和 Export JSON / Markdown。

4. OpenAlex 503 或 arXiv 429 / timeout 时：
   - 不解释为系统崩溃。
   - 展示 `missing_evidence`、source stats 和 events 诊断。
   - 说明 arXiv 或其他可用 source 仍可返回候选。
   - 必要时切回 Mock Demo，或展示 `docs/design/final_engineering_acceptance.md` 中的真实验收记录。

5. 网络不稳定时可展示 Batch CLI / Evaluation CLI：
   - 批量搜索 JSONL。
   - Markdown 汇总。
   - gold/qrels 指标评测。

## 提交前检查命令

```bash
PYTHONPATH=src pytest -q
cd frontend && npm run lint
cd frontend && npm run build
git status --short
```

## 不应提交的内容

- `node_modules`
- `.next`
- `__pycache__`
- `.pytest_cache`
- `.DS_Store`
- secrets / API keys
- 临时 curl 输出
- 临时 batch 输出
- 本地虚拟环境目录

## 当前边界和非目标

- 当前是 no-LLM 规则版 MVP，没有调用 LLM。
- 当前没有读取全文 PDF。
- Citation-backed synthesis 只基于 metadata / evidence rows，不代表全文级证据归纳。
- 当前未完整接入 LitSearch / AstaBench benchmark。
- OpenAlex / arXiv 是真实外部依赖，可能出现 503、429 或 timeout。
- Real Search 当前使用 in-memory run store，不是生产级持久化队列。
- Retrieval cache 是轻量 in-memory cache，不是生产级分布式缓存。
- Semantic Scholar 和 PubMed connector 尚未实现。
- 当前没有用户鉴权、配额管理、生产部署脚本或长期日志系统。

## 交付摘要

本次最终提交材料强调 ScholarNavigator 的真实边界：系统不是纯 mock，也不是完整生产系统；它是一个 no-LLM、metadata/evidence-row、可解释、可观测、可评测的参赛 MVP。Mock Demo 提供稳定演示，Real Search 验证真实 OpenAlex / arXiv 检索链路，前端展示运行过程、结果、synthesis、citation graph 和本地导出，CLI 提供批量运行与评测基础设施。

# 最终提交清单

本文档用于评审和提交前快速确认 ScholarNavigator 的代码、文档、验证结果和边界。

## 项目定位

- 项目名称：ScholarNavigator
- 对应赛题：华为企业赛题三，科研场景下复杂学术查询的智能论文搜索与推荐
- 当前形态：Real Search only runtime；可选真实 LLM 仅用于 Query Understanding，默认可无 LLM key 运行规则版路径
- 核心目标：提供可演示、可测试、可观测、可评测的真实学术论文搜索与推荐闭环

## 最终提交文件地图

### Backend 关键目录

- `src/scholar_agent/app/`  
  FastAPI 应用入口、CORS、Real Search lifecycle、runtime config。
- `src/scholar_agent/core/`  
  Paper、Search、API、Synthesis、Evaluation 等 Pydantic schema，以及 dedup 逻辑。
- `src/scholar_agent/connectors/`  
  OpenAlex / arXiv connector，含 detailed result、retry/backoff、错误诊断和 OpenAlex references。
- `src/scholar_agent/agents/`  
  QueryUnderstanding、Judgement、Reranker、QueryEvolution、RefChain、Synthesis 等规则版 agents。
- `src/scholar_agent/services/`  
  SearchService、SearchServiceOutput、API mapper。
- `src/scholar_agent/llm/`  
  OpenAI-compatible LLM provider 基础设施，仅用于可选 Query Understanding 增强。
- `src/scholar_agent/evaluation/`  
  metrics、offline evaluator、fixture loader。
- `tests/`  
  Real Search API、connectors、retriever、SearchService、agents、mapper、evaluation、CLI 和旧产品 path 删除测试。

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
  前端启动、Real Search、Synthesis、Citation Graph、导出和 CORS 说明。

### Final 文档

- `docs/final/project_delivery_summary.md`
- `docs/final/contest_technical_report_draft.md`
- `docs/final/demo_script.md`
- `docs/final/reviewer_quickstart.md`
- `docs/final/submission_manifest.md`
- `docs/final/final_submission_readiness.md`

### 关键验证记录

- `docs/design/final_engineering_acceptance.md`  
  hybrid runtime 阶段的历史验收记录。Real Search only 重构后不能作为当前最终验收结论。

### Batch / Evaluation CLI

- `scripts/run_search_batch.py`
- `scripts/summarize_search_batch.py`
- `scripts/evaluate_search_batch.py`

## 当前验收状态

Real Search only 重构后需要重新验收。提交前应运行：

```bash
PYTHONPATH=src pytest -q
cd frontend && npm run lint
cd frontend && npm run build
git status --short
```

验收重点：

- runtime config 为 `real_search`。
- connectors 中没有产品级示例 connector。
- OpenAlex / arXiv `available=true`。
- Semantic Scholar / PubMed 仍 `not_implemented`。
- 默认 `llm.available=false`；配置 provider 后可为 `true`。
- `features.llm_query_understanding=true` 仅表示 Query Understanding 可选走 LLM。
- legacy product-facing example search path 返回 404/405。
- OpenAPI 不再包含 legacy product-facing example search paths。
- 前端只展示 Real Search 入口。

## 演示建议

1. 先检查 runtime config：

   ```bash
   curl http://127.0.0.1:8000/api/v1/runtime/config
   ```

2. 演示 Real Search：
   - 走 `/api/v1/real/search/runs` lifecycle。
   - 展示 queued / running / succeeded / failed 状态。
   - 展示 Real Search Events，包括 `connector_completed`、`warning`、`cost_updated`。
   - 展示论文卡片、Synthesis Panel、Citation Graph Panel 和 Export JSON / Markdown。

3. OpenAlex 503 或 arXiv 429 / timeout 时：
   - 不解释为系统崩溃。
   - 展示 `missing_evidence`、source stats 和 events 诊断。
   - 明确说明产品路径不会返回示例数据。

4. 网络不稳定时可展示 Batch CLI / Evaluation CLI：
   - 批量搜索 JSONL。
   - Markdown 汇总。
   - gold/qrels 指标评测。

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

- 当前 LLM 只可选用于 Query Understanding；Judgement、Reranking、Synthesis 仍为规则版。
- 当前没有读取全文 PDF。
- Citation-backed synthesis 只基于 metadata / evidence rows，不代表全文级证据归纳。
- 当前未完整接入 LitSearch / AstaBench benchmark。
- OpenAlex / arXiv 是真实外部依赖，可能出现 503、429 或 timeout。
- Real Search 当前使用 in-memory run store，不是生产级持久化队列。
- Retrieval cache 是轻量 in-memory cache，不是生产级分布式缓存。
- Semantic Scholar 和 PubMed connector 尚未实现。
- 当前没有用户鉴权、配额管理、生产部署脚本或长期日志系统。
- 后续可将 LLM 扩展到 Judgement、Reranking、Synthesis；provider 不可用时也应返回明确错误或诊断。

## 交付摘要

本次提交将 ScholarNavigator 从双路径演示形态切换为 Real Search only runtime。产品路径不再提供示例检索数据兜底，而是通过真实 OpenAlex / arXiv 检索链路、结构化 diagnostics、Real Search Events、Synthesis Panel、Citation Graph Panel 和导出功能支撑参赛演示与评审。

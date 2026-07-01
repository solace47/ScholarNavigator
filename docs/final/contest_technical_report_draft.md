# ScholarNavigator 参赛技术报告初稿

## 摘要

ScholarNavigator 是面向“中国研究生人工智能创新大赛”华为企业赛题三“科研场景下复杂学术查询的智能论文搜索与推荐”的前后端分离系统。当前版本已经从双路径演示形态切换为 **Real Search only runtime**：产品路径只保留真实检索生命周期接口，不再提供示例数据兜底或产品级模拟检索结果。

当前版本已接入真实 LLM Provider 基础设施，但只用于可选的 Query Understanding JSON 增强。系统已经实现 FastAPI 后端、Next.js 前端、OpenAlex / arXiv 真实检索 connector、多源聚合去重、Query Understanding、Judgement、Reranking、Query Evolution、RefChain、Synthesis、API mapper、Real Search lifecycle、SSE 可观测事件、retrieval cache、批量搜索 CLI 和离线评测基础设施。当前版本没有把 Judgement、Reranking 或 Synthesis 改成 LLM，没有读取全文 PDF，也没有接入完整 LitSearch / AstaBench benchmark。

外部检索源失败时，系统会返回明确 diagnostics，例如 source stats、warnings、missing_evidence 和 SSE warning/error events，不会静默返回示例数据。

## 赛题理解

赛题要求构建端到端学术论文智能搜索系统，针对自然语言描述的复杂学术查询，完成查询理解、多策略检索、论文排序和结构化归纳。系统设计重点包括：

1. F1 Score：平衡 Precision 和 Recall。
2. 运行效率：控制 API 调用次数、Token 消耗和端到端延迟。
3. 结果结构化：输出列表、证据、关系图、诊断和可复现的结构化结果。

ScholarNavigator 当前采用 staged pipeline：默认可无 LLM key 运行规则版路径，也可在后端启用 OpenAI-compatible LLM 对 Query Understanding 做结构化增强。无论 LLM 是否可用，检索、去重、判断、重排和诊断链路都保持可复现。

## 系统目标与创新点

- Real Search only：产品路径只走 `/api/v1/real/search/runs` 生命周期接口。
- 无静默 fallback：OpenAlex / arXiv 或未来 LLM 不可用时，返回明确错误或诊断。
- 前后端分离：前端不读取、不保存、不展示 API Key。
- 可解释 pipeline：每个阶段输出结构化对象，便于测试、追踪和前端展示。
- 低 token 成本：LLM 只可选用于 Query Understanding，默认关闭；当前还未完整统计 LLM token。
- 可观测真实检索：SSE 暴露 `connector_completed`、`warning`、`cost_updated`。
- 保守 synthesis：规则版 citation-backed synthesis 只基于 metadata/evidence rows，不读取全文 PDF。
- 可评测基础：提供离线 metrics、fake fixture evaluator、批量搜索和 gold/qrels 评测 CLI。

## 总体架构

后端基于 FastAPI 和 `src/scholar_agent` 包实现：

- API 层：health、runtime config、Real Search lifecycle、internal preview。
- Service 层：SearchService、API mapper、离线评测服务。
- Agent 层：QueryUnderstanding、Judgement、Reranker、QueryEvolution、RefChain、Synthesis。
- Connector 层：OpenAlex、arXiv。
- Core 层：Paper schema、Search schema、Synthesis schema、Evaluation schema、dedup。
- Evaluation 层：metrics、offline evaluator、fixture loader、CLI 脚本。

前端基于 Next.js + TypeScript + Tailwind CSS 实现：

- ScholarNavigator Real Search 工作台。
- 参数配置：`top_k`、`run_profile`、`enable_query_evolution`、`enable_refchain`、`current_year`。
- Real Search Events 展示。
- 论文卡片、source badges、identifier、links、missing evidence。
- Citation-backed Synthesis Panel。
- Citation Graph Panel。
- Export JSON / Markdown。

## API 边界与 Runtime

保留的产品 API：

- `GET /api/v1/health`
- `GET /api/v1/runtime/config`
- `POST /api/v1/real/search/runs`
- `GET /api/v1/real/search/runs/{run_id}`
- `GET /api/v1/real/search/runs/{run_id}/result`
- `GET /api/v1/real/search/runs/{run_id}/events`
- `POST /api/v1/real/search/runs/{run_id}/cancel`

删除的产品 API：

- legacy product-facing example search create/status/result/events endpoints。

`GET /api/v1/runtime/config` 当前应返回：

- `mode=real_search`
- 默认 `llm.available=false`；配置 OpenAI-compatible provider 后可为 `true`
- `features.llm_query_understanding=true` 仅在 provider 可用且启用对应开关时出现
- OpenAlex / arXiv connector `available=true`
- Semantic Scholar / PubMed `not_implemented`
- `features.real_search=true`
- `features.real_search_cancel=true`
- `features.real_search_sse=true`
- `features.retrieval_cache=true`
- `features.batch_cli=true`

internal preview endpoint 可保留为后端调试入口，但必须调用真实 SearchService，不返回示例数据。

## 核心 Pipeline

```text
query
  -> analyze_query
  -> retrieve_papers for each subquery
  -> aggregate papers
  -> deduplicate_papers
  -> judge_papers
  -> rerank_papers
  -> optional evolve_queries
  -> optional retrieve evolved queries
  -> merge and deduplicate
  -> judge and rerank
  -> optional expand_refchain
  -> merge references and deduplicate
  -> judge and rerank
  -> optional synthesize_answer
  -> SearchServiceOutput
```

## 关键模块

### Query Understanding

QueryUnderstandingAgent 默认输出规则版 SearchPlan，支持 language、intent、domain、time range、venue、source selection 和 subquery 生成。启用 OpenAI-compatible LLM provider 时，系统要求 LLM 返回 JSON object，再经过 schema 校验和 source 过滤。当前只允许已实现的 `openalex` / `arxiv` 进入 selected_sources，不会把未实现的 Semantic Scholar / PubMed 假装为可用源。LLM 禁用或失败时会记录 `llm_query_understanding_disabled` 或 `llm_query_understanding_failed:<reason>`，并继续使用规则版解析。

### 多源检索与去重

OpenAlex 和 arXiv connector 都有 timeout、轻量 retry/backoff、字段缺失容错和 detailed diagnostics。Retriever 汇总 source_stats、warnings、latency，并支持轻量 retrieval cache。Dedup 支持 DOI、arXiv ID 去版本号、OpenAlex ID、Semantic Scholar ID、PubMed ID 和 title+year fallback。

### Judgement 与 Reranking

JudgementAgent 只基于 QueryAnalysis 和 Paper metadata，不访问外网、不读取 PDF、不调用 LLM。RerankerAgent 以 judgement score 为主，同时考虑 citation_count、sources 数量、identifier 完整度、venue、year、metadata 完整性和 intent 权重。

### Query Evolution 与 RefChain

Query Evolution 只从高相关 seed 生成少量 evolved queries。RefChain 只做单层引用扩展，默认使用 OpenAlex references，不做递归多层引用扩展，也不训练 PaSa 风格 selector。

### Citation-backed Synthesis

SynthesisAgent 只使用 final ranked papers、evidence rows、warnings、source_stats 和 refchain_output。每个 finding 必须绑定 citation key。没有 evidence rows 时返回 insufficient evidence，不编造结论。当前不读取全文 PDF，不调用 LLM，不引入外部事实。

## 前端交互设计

当前前端只有 Real Search 产品路径：

1. Search Workbench：输入查询、设置 `top_k`、`run_profile`、Query Evolution、RefChain、current year。
2. Run Progress：展示 Real Search Events、状态、阶段、cost report、cache hits、取消按钮。
3. Results：展示论文卡片、diagnostics、Synthesis Panel、Citation Graph Panel、Export JSON / Markdown。

如果外部检索源失败，页面展示“检索源失败/无候选”和 missing_evidence，而不是显示示例数据。

## Evaluation 设计

当前已实现：

- `canonical_paper_id`
- Recall@K
- Precision@K
- MRR
- nDCG@K
- candidate_count_metrics
- error_rate_metrics
- offline evaluator
- fixture loader
- `scripts/run_search_batch.py`
- `scripts/summarize_search_batch.py`
- `scripts/evaluate_search_batch.py`

当前 sample fixture 是小型手写 fake 数据，只用于 smoke test 和报告样例，不代表完整 LitSearch / AstaBench benchmark。

## 成本、延迟与鲁棒性

- 默认关闭 LLM；启用后当前仅用于 Query Understanding，本报告阶段还未完整统计 LLM token。
- Search API 调用数、search rounds、latency、judged_paper_count 和 `cache_hit_count` 进入 cost_report。
- `REAL_SEARCH_MAX_WORKERS` 控制 SearchService 并发。
- `REAL_SEARCH_BACKGROUND_WORKERS` 控制后台 executor。
- `REAL_SEARCH_RUN_TTL_SECONDS` 和 `REAL_SEARCH_MAX_STORED_RUNS` 控制 in-memory run store 清理。
- `SCHOLAR_AGENT_CORS_ORIGINS` 可扩展本地开发 CORS allowlist。

## 当前测试与验证状态

Real-only 重构后需要重新执行最终验收。旧 `docs/design/final_engineering_acceptance.md` 属于 hybrid runtime 历史记录，不能代表当前最终状态。

本轮应验证：

- `PYTHONPATH=src pytest -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`
- runtime config 为 real_search。
- legacy product-facing example search path 不可用，且不出现在 OpenAPI paths 中。
- 前端不再展示模式切换或任何产品级示例检索入口。

## 已知问题与边界

1. 当前只有 Query Understanding 和 Judgement 支持可选 LLM JSON 增强；Reranking、Synthesis 仍为规则版。
2. 当前没有读取全文 PDF，所有证据来自 title、abstract、venue、metadata。
3. 当前没有接入完整 LitSearch / AstaBench benchmark，只有本地 fake fixture 评测链路。
4. Real Search 依赖 OpenAlex / arXiv，可能受到 503、429、timeout 等外部服务影响。
5. Semantic Scholar 和 PubMed connector 尚未实现，biomedical 查询会提示 PubMed 未实现。
6. Real Search 使用 in-memory run store，不是生产级持久化任务队列。
7. 当前已接入 LLM provider 基础设施，但只覆盖 Query Understanding；LLM 失败时只记录诊断并回到规则解析，不允许返回示例数据。

## 后续工作

1. 将 LLM provider 从 Query Understanding / Judgement 扩展到 Reranking / Synthesis，并实现更完整的 token 成本统计。
2. 将 Real Search lifecycle 升级为持久化任务队列。
3. 增加 Semantic Scholar、PubMed 等检索源。
4. 增加全文或分段证据检索能力，并严格绑定证据来源。
5. 接入完整 LitSearch / AstaBench 或 SPARBench 数据，形成正式实验表格。
6. 完善缓存、日志、限流、失败重试和成本 dashboard。

## 总结

ScholarNavigator 当前已经形成 Real Search only 的参赛系统雏形。它通过前后端分离、可解释规则 pipeline、真实 OpenAlex / arXiv 检索、错误可观测、结构化 API mapper、Synthesis Panel、Citation Graph Panel 和离线评测基础，覆盖了赛题中查询理解、多源检索、相关性判断、结果结构化和效率控制的核心方向。

当前版本不夸大能力：它只可选在 Query Understanding 调用真实 LLM，不读取全文 PDF，不声称完成完整 benchmark，也不再用产品级示例数据兜底。它的价值在于先搭建稳定、可测试、可演示、可扩展的真实检索工程闭环，为后续更深入的 LLM agent、更多检索源和正式评测接入提供可靠基础。

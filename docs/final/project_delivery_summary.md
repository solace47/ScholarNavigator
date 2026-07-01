# ScholarNavigator 项目交付总览

## 基本信息

- 项目名称：ScholarNavigator
- 对应赛题：华为企业赛题三，科研场景下复杂学术查询的智能论文搜索与推荐
- 当前阶段：前后端分离的 Real Search only MVP，已接入可选真实 LLM Query Understanding 基础设施；默认可在无 LLM key 下运行规则版路径。

## 项目目标

ScholarNavigator 面向复杂学术查询场景，目标是把自然语言研究需求转化为可解释、可复现、可评测的真实论文搜索流程。系统重点服务赛题评分关注点：

1. 提高 Precision 与 Recall 的平衡。
2. 控制外部 API 调用、Token 成本和端到端延迟。
3. 输出结构化论文列表、相关性解释、证据与诊断信息。

当前版本只允许 LLM 可选增强 Query Understanding，且 API key 仅从后端环境变量读取。Judgement、Reranking、Query Evolution、RefChain 和 Synthesis 仍是规则版实现，主要基于论文 metadata、标题、摘要、venue、identifier、来源和检索过程诊断。系统不读取全文 PDF。

## 系统架构

- 后端：FastAPI + Python 3.11+，负责真实检索 pipeline、OpenAlex / arXiv connector、聚合去重、规则判断、重排、可选 Query Evolution、可选 RefChain、规则版 Synthesis、API mapper、离线评测和成本统计。
- 前端：Next.js + TypeScript + Tailwind CSS，负责 ScholarNavigator 工作台、参数交互、Real Search Events、结果卡片、missing evidence 诊断、Citation-backed Synthesis Panel、Citation Graph Panel 和本地导出。
- 安全边界：前端不读取、不保存、不展示任何 API Key；外部 API 和未来 LLM 调用都保留在后端。
- API 形态：产品路径只保留 Real Search lifecycle；legacy product-facing example search 接口已删除，不再返回任何示例数据或静默 fallback。

## 核心 Pipeline

当前 SearchService 的主要流程如下：

1. QueryUnderstandingAgent：默认规则解析 query；启用真实 OpenAI-compatible LLM provider 时，可先请求结构化 JSON，再归一化为 QueryAnalysis 和 SearchPlan。
2. Retriever：按 subquery 调用 OpenAlex / arXiv connector，记录 source_stats、warnings 和 latency。
3. Dedup：跨来源、跨 subquery 做 DOI、arXiv ID、OpenAlex ID、Semantic Scholar ID、PubMed ID、title+year 去重。
4. JudgementAgent：基于 QueryAnalysis 与 Paper metadata 生成相关性 score、category、reasoning 和 evidence。
5. RerankerAgent：综合 judgement score、引用数、来源数、identifier 完整度、venue、year、metadata 完整度生成 final_score。
6. QueryEvolutionAgent：在开关开启时，基于高相关 seed 生成有限数量 evolved queries，并进行第二轮检索。
7. RefChainAgent：在开关开启时，基于高相关 seed 做单层 OpenAlex references 扩展。
8. SynthesisAgent：在 final rerank 后生成规则版 citation-backed synthesis。
9. API Mapper：把 SearchServiceOutput 映射为前端可消费的 SearchRunResultResponse。

## 已实现功能清单

- 基础 API：
  - `GET /api/v1/health`
  - `GET /api/v1/runtime/config`
- Real Search API：
  - `POST /api/v1/real/search/runs`
  - `GET /api/v1/real/search/runs/{run_id}`
  - `GET /api/v1/real/search/runs/{run_id}/result`
  - `GET /api/v1/real/search/runs/{run_id}/events`
  - `POST /api/v1/real/search/runs/{run_id}/cancel`
  - 异步后台执行，支持 queued / running / succeeded / failed / cancelled 状态。
  - SSE 回放真实事件，包括 `connector_completed`、`warning`、`cost_updated`。
- Internal Preview API：
  - `POST /api/v1/internal/search/preview`
  - `POST /api/v1/internal/search/preview/api-result`
  - 仅用于后端调试，调用真实 SearchService，不返回示例数据。
- OpenAlex connector：
  - Works 检索。
  - references 获取能力，用于单层 RefChain。
  - timeout、轻量 retry/backoff、错误诊断。
- arXiv connector：
  - 公共 API 检索。
  - XML 解析容错、timeout、轻量 retry/backoff、错误诊断。
- 多源聚合：
  - `retrieve_papers` 支持 OpenAlex / arXiv。
  - 单个 source 失败不会中断整体 pipeline。
  - `source_stats` 和 `warnings` 可观测。
  - 轻量 in-memory retrieval cache，`cache_hit_count` 会进入 `cost_report`。
- 规则版 agents：
  - QueryUnderstandingAgent
  - JudgementAgent
  - RerankerAgent
  - QueryEvolutionAgent
  - RefChainAgent
  - SynthesisAgent
- 前端：
  - Real Search 工作台。
  - Real Search Events。
  - Results 论文卡片、source badges、identifier、links、missing evidence。
  - Citation-backed Synthesis Panel。
  - Citation Graph Panel。
  - Export JSON / Export Markdown，本地浏览器导出，不上传后端。
- Runtime 与工程能力：
  - `/api/v1/runtime/config` 返回 `mode=real_search`，明确 LLM provider 状态、OpenAlex/arXiv 可用于 Real Search。
  - LLM provider 支持 OpenAI-compatible Chat Completions；当前只用于 Query Understanding，禁用或失败时会记录明确 warning，并走规则版解析。
  - CORS allowlist 可配置，默认支持 `3000`、`3001`、`5173` 的 localhost / 127.0.0.1。
  - Real Search in-memory run store 支持 TTL 和最大数量清理，只清理 terminal runs。
- 批量 CLI：
  - `scripts/run_search_batch.py`
  - `scripts/summarize_search_batch.py`
  - `scripts/evaluate_search_batch.py`
- 离线评测：
  - evaluation schemas、metrics、offline evaluator、fixture loader。
  - sample fake fixture 和报告输出脚本。
  - 可比较 baseline / query_evolution / refchain 三组。

## 参考思想与借鉴关系

- SPAR：借鉴 Query Understanding、Retrieval、Judgement、Query Evolution、Reranker 和 RefChain 等 agent 化 pipeline 思路。
- PaSa：借鉴 Crawler / Selector、paper queue、Search / Expand / Stop 的高召回检索和选择式扩展思想；当前不实现 SFT、PPO、RL 训练或递归多层扩展。
- PaperQA2：借鉴 evidence gathering、citation traversal、citation-backed answer 和 insufficient evidence 表达方式；当前不读取全文 PDF。
- ai2-scholarqa-lib：借鉴 scholar QA 中证据组织、引用约束和回答结构化的思想。
- LitSearch：借鉴 Recall@K、Precision@K、MRR、nDCG 等检索指标；当前未接入完整 benchmark。
- AstaBench：借鉴科研 Agent 评测中的任务级指标、候选规模、错误率、延迟和可复现评测；当前未接入完整 benchmark。

## 真实检索能力

当前真实检索源包括：

- OpenAlex：支持 Works 检索、`OPENALEX_MAILTO` polite pool 配置、references 获取、timeout、轻量 retry/backoff 和错误诊断。
- arXiv：支持公共 API 检索、XML 容错、timeout、轻量 retry/backoff 和错误诊断。

已知情况：OpenAlex 可能返回 503，arXiv 可能出现 429 或 timeout。系统会把这些错误写入 `source_stats`、`warnings`、`missing_evidence`、SSE events 和 synthesis limitations，不会静默返回示例数据。

## Query Evolution / RefChain / Synthesis / Evaluation 状态

- Query Evolution：已实现规则版，只从 highly relevant 和高分 partially relevant seed 生成少量 evolved queries。
- RefChain：已实现规则版单层引用扩展，生产默认使用 OpenAlex references，不做递归多层引用扩展。
- Synthesis：已实现规则版 citation-backed synthesis，输出 answer_summary、key_findings、evidence_table、citation_coverage、limitations、warnings；当前不调用 LLM，不读取全文 PDF。
- Evaluation：已实现基础 schema、metrics、offline evaluator、fixture loader 和 sample run；当前 sample 是小型手写 fake fixture，不代表完整 LitSearch / AstaBench benchmark。

## 前端 Real Search

- 调用 `POST /api/v1/real/search/runs` 创建异步真实检索 run。
- 轮询 `GET /api/v1/real/search/runs/{run_id}`，成功后读取 result。
- 连接 `GET /api/v1/real/search/runs/{run_id}/events` 展示 Real Search Events。
- 支持 `POST /api/v1/real/search/runs/{run_id}/cancel` 取消 queued / running run。
- 可能真实访问 OpenAlex / arXiv。
- 返回论文时复用结果卡片；无候选或检索源失败时展示“检索源失败/无候选”和 missing_evidence。
- 返回 `synthesis` 时展示 Citation-backed Synthesis Panel。
- 有 citation graph 时展示 Citation Graph Panel。
- 有 result 时支持 Export JSON / Export Markdown。

## 当前测试与验证状态

Real-only 重构后需要重新执行最终验收。上一版最终验收记录 `docs/design/final_engineering_acceptance.md` 属于 hybrid runtime 历史记录，不能作为当前 Real Search only runtime 的最终验收结论。

本轮目标验证项：

- `PYTHONPATH=src pytest -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`
- legacy product-facing example search path 返回 404/405。
- OpenAPI 不再包含 legacy product-facing example search paths。
- runtime config 不再包含 mock connector。

## 当前已知问题

- OpenAlex 503、arXiv 429 / timeout 是真实外部依赖风险，retry/backoff 只能提升可观测性和部分恢复能力，不能保证外部服务可用。
- 当前只有 Query Understanding 支持可选 LLM JSON 增强；其他 agent 仍为规则版，复杂语义判断、跨语言概念扩展和证据归纳能力有限。
- 当前 Synthesis 只基于 metadata 和 evidence rows，不读取全文 PDF，不做段落级证据检索。
- Real Search 使用 in-memory run store，不是生产级持久化队列。
- 评测当前只完成 fake fixture 离线链路，尚未接入完整 LitSearch / AstaBench 数据。
- 尚未实现生产级持久化缓存、用户级日志、成本看板和部署配置。

## 后续可扩展方向

1. 将 LLM 增强从 Query Understanding 扩展到 Judgement / Reranking / Synthesis，但必须保留证据边界和 no-key diagnostics。
2. 将 Real Search lifecycle 从 in-memory store 升级为持久化任务队列和可部署服务。
3. 增加 Semantic Scholar、PubMed 等检索源，并完善 biomedical query 的 source selection。
4. 增加全文 PDF / abstract chunk 证据检索，但必须保留引用来源和证据边界。
5. 完成 LitSearch / AstaBench 数据适配，形成可复现实验表格。
6. 增加生产级缓存、限流、日志、成本统计和失败重试策略。

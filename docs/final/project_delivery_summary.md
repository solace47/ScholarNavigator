# ScholarNavigator 项目交付总览

## 基本信息

- 项目名称：ScholarNavigator
- 对应赛题：华为企业赛题三，科研场景下复杂学术查询的智能论文搜索与推荐
- 当前阶段：前后端分离的 no-LLM 规则版 MVP，已具备 Mock Demo + Real Search hybrid runtime、真实 OpenAlex / arXiv 检索、异步 run lifecycle、结构化结果展示、规则版 citation-backed synthesis、批量搜索和离线评测雏形。

## 项目目标

ScholarNavigator 面向复杂学术查询场景，目标是把自然语言研究需求转化为可解释、可复现、可评测的论文搜索流程。系统重点服务赛题评分关注点：

1. 提高 Precision 与 Recall 的平衡。
2. 控制外部 API 调用、Token 成本和端到端延迟。
3. 输出结构化论文列表、相关性解释、证据与诊断信息。

当前版本没有调用 LLM，也没有读取全文 PDF。所有理解、判断、重排、查询演化和 synthesis 都是规则版实现，主要基于论文 metadata、标题、摘要、venue、identifier、来源和检索过程诊断。

## 系统架构

项目采用前后端分离架构：

- 后端：FastAPI + Python 3.11+，负责学术检索 pipeline、外部检索源调用、聚合去重、规则判断、重排、可选 Query Evolution、可选 RefChain、规则版 Synthesis、API mapper、离线评测和成本统计。
- 前端：Next.js + TypeScript + Tailwind CSS，负责 ScholarNavigator 工作台、参数交互、运行过程展示、结果卡片、missing evidence 诊断和 Citation-backed Synthesis Panel。
- 安全边界：前端不读取、不保存、不展示任何 API Key；外部 API 和未来 LLM 调用都应保留在后端。
- API 形态：现有 Mock API 保持稳定；真实检索通过独立 `/api/v1/real/search/runs` lifecycle 暴露，支持 create/status/result/events/cancel；internal preview endpoint 仅保留为后端调试入口。

## 核心 Pipeline

当前 SearchService 的主要流程如下：

1. QueryUnderstandingAgent：规则解析 query，生成 QueryAnalysis 和 SearchPlan。
2. Retriever：按 subquery 调用 OpenAlex / arXiv connector，记录 source_stats、warnings 和 latency。
3. Dedup：跨来源、跨 subquery 做 DOI、arXiv ID、OpenAlex ID、Semantic Scholar ID、PubMed ID、title+year 去重。
4. JudgementAgent：基于 QueryAnalysis 与 Paper metadata 生成相关性 score、category、reasoning 和 evidence。
5. RerankerAgent：综合 judgement score、引用数、来源数、identifier 完整度、venue、year、metadata 完整度生成 final_score。
6. QueryEvolutionAgent：在开关开启时，基于高相关 seed 生成有限数量 evolved queries，并进行第二轮检索。
7. RefChainAgent：在开关开启时，基于高相关 seed 做单层 OpenAlex references 扩展。
8. SynthesisAgent：在 final rerank 后生成规则版 citation-backed synthesis。
9. API Mapper：把 SearchServiceOutput 映射为现有 SearchRunResultResponse，兼容前端结果结构。

## 已实现功能清单

- FastAPI Mock API：
  - `GET /api/v1/health`
  - `GET /api/v1/runtime/config`
  - `POST /api/v1/search/runs`
  - `GET /api/v1/search/runs/{run_id}`
  - `GET /api/v1/search/runs/{run_id}/result`
  - `GET /api/v1/search/runs/{run_id}/events`
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
- OpenAlex connector：
  - Works 检索。
  - references 获取能力，为单层 RefChain 做准备。
  - timeout、轻量 retry/backoff、错误诊断。
- arXiv connector：
  - 公共 API 检索。
  - XML 解析容错、timeout、轻量 retry/backoff、错误诊断。
- 多源聚合：
  - `retrieve_papers` 支持 OpenAlex / arXiv。
  - 单个 source 失败不会中断整体 pipeline。
  - `source_stats` 和 `warnings` 可观测。
  - 轻量 in-memory retrieval cache，支持 TTL、最大条目数、环境变量关闭，`cache_hit_count` 会进入 `cost_report`。
- 论文去重：
  - 支持 identifier 优先和 title+year fallback。
  - 合并 sources、identifiers、urls、citation_count、abstract、authors 等字段。
- 规则版 agents：
  - QueryUnderstandingAgent
  - JudgementAgent
  - RerankerAgent
  - QueryEvolutionAgent
  - RefChainAgent
  - SynthesisAgent
- SearchService：
  - 支持 subquery 并发。
  - 支持可选 Query Evolution、RefChain、Synthesis。
  - 支持注入 fake retriever / fake reference_fetcher，便于离线测试。
- API Mapper：
  - 将真实 SearchServiceOutput 映射成现有前端可消费的 SearchRunResultResponse。
  - 可选暴露 `synthesis` 字段，不破坏 Mock Demo。
- 前端：
  - Mock Demo 模式：保留原 Mock API 和 SSE 流程。
  - Real Preview 模式：调用独立 Real Search lifecycle，并展示真实 SSE events。
  - Results 展示论文卡片、source badges、identifier、links、missing evidence。
  - Citation-backed Synthesis Panel：展示 summary、findings、coverage、limitations、evidence rows。
  - Citation Graph Panel：展示后端返回的 citation graph nodes / edges，不做前端推断。
  - Export JSON / Export Markdown：在浏览器本地导出当前 `SearchRunResultResponse`，不上传后端。
- Runtime 与工程能力：
  - `/api/v1/runtime/config` 返回 `mode=hybrid`，明确 no-LLM、OpenAlex/arXiv 可用于 Real Search。
  - CORS allowlist 可配置，默认支持 `3000`、`3001`、`5173` 的 localhost / 127.0.0.1。
  - Real Search in-memory run store 支持 TTL 和最大数量清理，只清理 terminal runs。
- 批量 CLI：
  - `scripts/run_search_batch.py`：从 JSONL 批量运行 SearchService。
  - `scripts/summarize_search_batch.py`：汇总批量结果为 Markdown。
  - `scripts/evaluate_search_batch.py`：基于 gold/qrels JSONL 计算 Recall@K、Precision@K、MRR、nDCG。
- 离线评测：
  - evaluation schemas、metrics、offline evaluator、fixture loader。
  - sample fake fixture 和报告输出脚本。
  - 可比较 baseline / query_evolution / refchain 三组。

## 参考思想与借鉴关系

- SPAR：
  - 借鉴 Query Understanding、Retrieval、Judgement、Query Evolution、Reranker、RefChain 等 agent 化 pipeline 思路。
  - 当前项目将其落成可测试的规则版 MVP，暂不接 LLM agent 推理。
- PaSa：
  - 借鉴 Crawler / Selector、paper queue、Search / Expand / Stop 的高召回检索和选择式扩展思想。
  - 当前 MVP 只实现轻量 Query Evolution 与单层 RefChain，不实现 SFT、PPO、RL 训练或递归多层扩展。
- PaperQA2：
  - 借鉴 evidence gathering、citation traversal、citation-backed answer 和 insufficient evidence 的表达方式。
  - 当前 Synthesis 只使用 metadata/evidence rows，不读取全文 PDF。
- ai2-scholarqa-lib：
  - 借鉴 scholar QA 中证据组织、引用约束和回答结构化的思想。
  - 当前实现重点是 evidence table、citation keys、limitations 和 coverage。
- LitSearch：
  - 借鉴学术检索离线评测任务的 Recall@K、Precision@K、MRR、nDCG 等指标设计。
  - 当前未接入完整 LitSearch benchmark，只提供 fake fixture 评测链路。
- AstaBench：
  - 借鉴科研 Agent 评测中任务级结果、效率、错误率、候选规模等维度。
  - 当前未接入完整 AstaBench，只完成评测方案和本地 evaluator 雏形。

## 真实检索能力

当前真实检索源包括：

- OpenAlex：
  - 支持 Works 检索。
  - 支持 OpenAlex polite pool 相关 `OPENALEX_MAILTO` 环境变量。
  - 支持 references 获取，用于 RefChain。
  - 已加入轻量 retry/backoff 和错误诊断。
- arXiv：
  - 支持公共 API 检索。
  - 支持 timeout、XML 容错和错误诊断。

已知情况：OpenAlex 可能返回 503，arXiv 可能出现 429 或 timeout。系统会把这些错误写入 `source_stats`、`warnings`、`missing_evidence` 和 synthesis limitations，避免前端白屏或静默失败。

## Query Evolution / RefChain / Synthesis / Evaluation 状态

- Query Evolution：
  - 已实现规则版。
  - 仅从 highly relevant 和高分 partially relevant seed 生成少量 evolved queries。
  - 默认稳定、去重、限制 source_hints，只使用 openalex / arxiv。
- RefChain：
  - 已实现规则版单层引用扩展。
  - 默认最多选择有限 seed 和有限 references。
  - 支持注入 reference_fetcher；生产默认使用 OpenAlex references。
  - 不做递归多层引用扩展。
- Synthesis：
  - 已实现规则版 citation-backed synthesis。
  - 输出 answer_summary、key_findings、evidence_table、citation_coverage、limitations、warnings。
  - 不调用 LLM，不读取全文 PDF，不引入外部事实。
- Evaluation：
  - 已实现基础 schema、metrics、offline evaluator、fixture loader 和 sample run。
  - 可离线比较 baseline / query_evolution / refchain。
  - 当前 sample 是小型手写 fake fixture，不代表完整 LitSearch / AstaBench benchmark。

## 前端双模式

- Mock Demo：
  - 继续走 `/api/v1/search/runs` mock flow。
  - 有 run 创建、状态查询、Mock SSE、Mock 结果。
  - 默认 `synthesis=null` 或缺省，Synthesis Panel 不展示。
- Real Preview：
  - 调用 `POST /api/v1/real/search/runs` 创建异步真实检索 run。
  - 轮询 `GET /api/v1/real/search/runs/{run_id}`，成功后读取 result。
  - 连接 `GET /api/v1/real/search/runs/{run_id}/events` 展示 Real Search Events。
  - 支持 `POST /api/v1/real/search/runs/{run_id}/cancel` 取消 queued / running run。
  - 可能真实访问 OpenAlex / arXiv。
  - 返回论文时复用结果卡片；无候选或检索源失败时展示“检索源失败/无候选”和 missing_evidence。
  - 返回 `synthesis` 时展示 Citation-backed Synthesis Panel。
  - 有 citation graph 时展示 Citation Graph Panel。
  - 有 result 时支持 Export JSON / Export Markdown。

## 当前测试与验证结果

以下结果来自最终工程验收记录 `docs/design/final_engineering_acceptance.md`：

- 后端测试：`PYTHONPATH=src pytest -q` 通过，`190 passed, 1 warning`。
- 前端 lint：`cd frontend && npm run lint` 通过。
- 前端 build：`cd frontend && npm run build` 通过。
- Runtime config 验证：`mode=hybrid`，`llm.available=false`，OpenAlex/arXiv connector 对 Real Search 可用，real_search/cancel/sse/retrieval_cache/batch_cli feature 可见。
- Mock Demo 验证：Mock run、Mock SSE、论文卡片展示成功，`synthesis=null` 按预期隐藏。
- Real Search API 验证：
  - 异步 run 创建、状态轮询、result、events 均通过。
  - events 包含 `connector_completed`、`warning`、`cost_updated`。
  - cancel endpoint 返回 `cancelled`，cancelled run 的 result 返回 `409 run cancelled`。
  - OpenAlex 503 进入 `missing_evidence` / source stats / SSE events，arXiv 仍可返回候选，系统不崩溃。
- 前端 smoke 验证：
  - Header 显示 `Mock + Real Search`、`Hybrid Runtime`、`no-LLM`、`backend ready`。
  - Mock Demo 与 Real Preview 均可演示。
  - Synthesis Panel、Citation Graph Panel、Export JSON / Markdown 在有 result 时可见。
- Synthesis Panel 验证：
  - Real Preview 返回论文时，panel 可展示 status、summary、findings、coverage、limitations 和 evidence rows。
  - 明确显示当前 MVP 不代表系统已读取全文 PDF。
- Batch CLI 验证：
  - 批量运行、Markdown 汇总、gold/qrels 评测均通过 smoke 验证。
  - 该评测使用临时小型 gold，只验证链路，不代表完整 benchmark。

## 当前已知问题

- OpenAlex 503、arXiv 429 / timeout 是真实外部依赖风险，retry/backoff 只能提升可观测性和部分恢复能力，不能保证外部服务可用。
- 当前所有 agent 均为 no-LLM 规则版，复杂语义理解、跨语言概念扩展和证据归纳能力有限。
- 当前 Synthesis 只基于 metadata 和 evidence rows，不读取全文 PDF，不做段落级证据检索。
- 公共 Mock API 仍是演示 mock flow；真实搜索已通过独立 Real Search lifecycle 暴露，但仍是 in-memory run store，不是生产级持久化队列。
- 评测当前只完成 fake fixture 离线链路，尚未接入完整 LitSearch / AstaBench 数据。
- 尚未实现生产级持久化缓存、任务队列、用户级日志、成本看板和部署配置。

## 后续可扩展方向

1. 将 Real Search lifecycle 从 in-memory store 升级为持久化任务队列和可部署服务。
2. 增加 Semantic Scholar、PubMed 等检索源，并完善 biomedical query 的 source selection。
3. 接入可选 LLM 增强 Query Understanding、Judgement、Reranking、Synthesis，同时保留无 Key fallback。
4. 增加全文 PDF / abstract chunk 证据检索，但必须保留引用来源和证据边界。
5. 完成 LitSearch / AstaBench 数据适配，形成可复现实验表格。
6. 增加缓存、限流、日志、成本统计和失败重试策略。
7. 将现有文档整理成参赛报告、答辩 PPT 和演示视频脚本。

# ScholarNavigator 参赛技术报告初稿

## 摘要

ScholarNavigator 是面向“中国研究生人工智能创新大赛”华为企业赛题三“科研场景下复杂学术查询的智能论文搜索与推荐”的前后端分离系统。系统目标是把研究者的自然语言复杂学术查询转化为可解释、可观测、可评测的论文检索 pipeline，并输出结构化论文结果、相关性理由、证据诊断和规则版 citation-backed synthesis。

当前版本是 no-LLM 规则版 MVP。系统已经实现 FastAPI 后端、Next.js 前端、OpenAlex / arXiv 真实检索 connector、多源聚合去重、Query Understanding、Judgement、Reranking、Query Evolution、RefChain、Synthesis、API mapper 和离线评测基础设施。当前版本没有调用 LLM，没有读取全文 PDF，也没有接入完整 LitSearch / AstaBench benchmark。真实检索依赖 OpenAlex / arXiv，可能受到 OpenAlex 503、arXiv 429 或 timeout 等外部服务状态影响。

项目重点不是把所有高级能力一次性做成黑盒，而是先形成一个边界清晰、可测试、可解释、低 Token 成本的闭环系统：后端负责检索与判断，前端负责工作台、运行过程、结果卡片和 synthesis 展示；Mock Demo 保证稳定演示，Real Preview 验证真实检索路径。

## 赛题理解

赛题要求构建端到端学术论文智能搜索系统，针对自然语言描述的复杂学术查询，完成查询理解、多策略检索、论文排序和结构化归纳。根据评分规则，自动评分重点包括：

1. F1 Score，占自动评分 70%，要求同时关注 Precision 和 Recall。
2. 运行效率，占自动评分 20%，重点是 API 调用次数、Token 消耗和端到端延迟。
3. 结果结构化，占自动评分 10%，要求输出列表、关系图、证据和清晰的结构化结果。

因此系统设计需要避免两类极端：

- 只做关键词检索，召回和语义约束不足。
- 无限制调用外部 API 或 LLM，成本和延迟不可控。

ScholarNavigator 当前采用规则版 staged pipeline，在不调用 LLM 的前提下先建立可复现的查询理解、检索、去重、判断、重排和诊断链路，为后续可选 LLM 增强保留接口。

## 系统目标与创新点

### 系统目标

- 将复杂学术查询解析为结构化 `SearchPlan`。
- 同时支持多源检索和跨来源去重。
- 对候选论文给出可解释的相关性判断和重排序。
- 对检索失败、证据不足、source error 给出结构化诊断。
- 在前端展示可演示的搜索工作台、运行过程、结果卡片和 synthesis panel。
- 建立离线评测基础，用于后续比较 baseline / query_evolution / refchain。

### 当前阶段创新点

1. 可解释的 no-LLM fallback pipeline  
   当前 MVP 即使没有 LLM Key，也能完成查询解析、候选检索、判断、重排和 synthesis，避免系统完全依赖 LLM。

2. 前后端分离的可信边界  
   前端不读取、不保存、不展示 API Key。真实检索、未来 LLM 调用、成本统计和评测都保留在后端。

3. 双模式演示机制  
   Mock Demo 用稳定 mock 数据保证比赛现场演示；Real Preview 真实调用 OpenAlex / arXiv，验证工程闭环和错误可观测性。

4. 检索错误可观测  
   OpenAlex 503、arXiv 429 / timeout 等失败会进入 `source_stats`、`warnings`、`missing_evidence` 和 synthesis limitations，前端不会白屏。

5. Citation-backed synthesis 的保守实现  
   规则版 synthesis 只基于 ranked papers 的 evidence rows 生成 summary 和 findings，每条 finding 绑定 citation key，并明确显示 metadata-only 和 full-text unavailable 限制。

6. 离线评测准备  
   已实现 canonical paper matching、Recall@K、Precision@K、MRR、nDCG、candidate count 和 error rate 相关基础模块，支持 fake fixture 离线对比三组策略。

## 总体架构

系统采用前后端分离架构。

### 后端

后端基于 FastAPI 和 `src/scholar_agent` 包实现，主要职责包括：

- API 层：Mock API、internal preview API、SSE mock flow。
- Service 层：SearchService、API mapper、离线评测服务。
- Agent 层：QueryUnderstanding、Judgement、Reranker、QueryEvolution、RefChain、Synthesis。
- Connector 层：OpenAlex、arXiv。
- Core 层：Paper schema、Search schema、Synthesis schema、Evaluation schema、dedup。
- Evaluation 层：metrics、offline evaluator、fixture loader、报告脚本。

### 前端

前端基于 Next.js + TypeScript + Tailwind CSS，主要职责包括：

- ScholarNavigator 搜索工作台。
- Mock Demo / Real Preview 模式切换。
- 参数配置：`top_k`、`run_profile`、`enable_query_evolution`、`enable_refchain`、`current_year`。
- Run Progress 展示。
- 论文卡片、source badges、identifier、links、missing evidence 展示。
- Citation-backed Synthesis Panel 展示。

### API 边界

现有 Mock API 保持不变：

- `POST /api/v1/search/runs`
- `GET /api/v1/search/runs/{run_id}`
- `GET /api/v1/search/runs/{run_id}/result`
- `GET /api/v1/search/runs/{run_id}/events`

真实检索当前通过 internal preview endpoint 验证：

- `POST /api/v1/internal/search/preview`
- `POST /api/v1/internal/search/preview/api-result`

其中 api-result preview 会调用 SearchService 并通过 API mapper 输出兼容前端的 `SearchRunResultResponse`。

## 核心算法与 Pipeline

SearchService 当前 pipeline 为：

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

核心设计原则：

- 每个阶段输出结构化对象，方便测试、追踪和前端展示。
- 单个 source 或 subquery 失败不阻断整个 pipeline。
- Query Evolution 和 RefChain 由开关控制，默认可单独验证。
- Synthesis 在 final rerank 之后执行，避免基于未排序或未过滤候选生成结论。
- 真实 API 调用集中在 connector 层和 SearchService，前端不直接访问外部学术 API。

## Query Understanding

QueryUnderstandingAgent 当前为规则版实现，输出内部 `SearchPlan`。核心能力包括：

- 基础校验：空 query 抛错。
- language 检测：`zh`、`en`、`mixed`、`unknown`。
- intent 识别：`survey`、`recent_progress`、`method_comparison`、`benchmark_or_dataset`、`application`、`paper_finding`、`general`。
- domain 识别：`computer_science`、`machine_learning`、`biomedical`、`general_science`。
- 时间约束解析：`since 2020`、`2021-2024`、`近三年`、`latest` 等。
- venue 抽取：ACL、EMNLP、SIGIR、KDD、NeurIPS、ICLR、ICML、CVPR 等。
- source selection：当前只返回已实现 connector `openalex` 和 `arxiv`。biomedical query 会提示 `pubmed_not_implemented`，但不会假装 PubMed 已接入。
- subquery 生成：根据 run profile 生成 1 到 5 个稳定、去重的子查询。

当前限制：

- 不做 LLM 语义改写。
- 中文 query 的英文关键词版本较保守，不做复杂机器翻译式长句。
- source selection 只覆盖 OpenAlex / arXiv。

## 多源检索与去重

### OpenAlex Connector

OpenAlex connector 支持：

- Works API 检索。
- `OPENALEX_MAILTO` polite pool 配置。
- OpenAlex references 获取，用于 RefChain。
- timeout、轻量 retry/backoff。
- 网络错误、非 2xx、字段缺失容错。
- detailed connector 输出 `papers`、`error_message`、`warnings`。

### arXiv Connector

arXiv connector 支持：

- arXiv 公共 API 检索。
- XML 解析容错。
- timeout、轻量 retry/backoff。
- HTTPError、URLError、timeout 诊断。
- detailed connector 输出 `papers`、`error_message`、`warnings`。

### 聚合与去重

Retriever 默认同时调用 OpenAlex 和 arXiv，并返回：

- query
- requested_sources
- raw_count
- deduplicated_count
- papers
- source_stats
- warnings
- latency_seconds

去重规则优先级：

1. DOI，大小写归一。
2. arXiv ID，去掉版本号。
3. OpenAlex ID。
4. Semantic Scholar ID。
5. PubMed ID。
6. 标题归一化后高度相似，并且年份相同或相差不超过 1 年。

合并时保留更完整的 identifiers、urls、sources、abstract、authors、venue、title，并取最大 citation_count。

## Judgement 与 Reranking

### JudgementAgent

JudgementAgent 当前只基于 QueryAnalysis 和 Paper metadata，不访问外网、不读取 PDF、不调用 LLM。评分信号包括：

- 原始 query 关键词命中。
- must include terms、methods、datasets 命中。
- title 命中权重高于 abstract。
- venue constraint 命中加分。
- time range 满足加分，不满足降分。
- domain 相关词命中。
- 缺失 title、abstract、year 时进入 warnings。

分类规则：

- `highly_relevant`
- `partially_relevant`
- `weakly_relevant`
- `irrelevant`
- `insufficient_evidence`

Evidence 只允许来自：

- title
- abstract
- venue
- metadata

### RerankerAgent

RerankerAgent 以 judgement score 为主，同时考虑：

- citation_count
- sources 数量
- identifier 完整度
- venue
- year
- metadata 完整性
- intent 对权重的影响

例如：

- `recent_progress` 查询提高 timeliness 权重。
- `survey` 查询提高 authority 权重。
- `irrelevant` 和 `insufficient_evidence` 排在后面。

输出 `RankedPaper`，包含 rank、final_score、score_breakdown、ranking_reason、evidence 等字段。

## Query Evolution 与 RefChain

### Query Evolution

QueryEvolutionAgent 当前为规则版可选阶段，仅在 `enable_query_evolution=True` 时执行。

规则：

- 只从 highly relevant 和高分 partially relevant 论文中选 seed。
- irrelevant 和 insufficient_evidence 不作为 seed。
- 默认最多生成 3 个 evolved queries。
- evolved query 与 used queries 去重。
- source_hints 只允许 `openalex` / `arxiv`。
- 同样输入输出稳定。

在人工验证中，Query Evolution 可生成额外 query，并在 `missing_evidence` 中记录 round、seed_count、generated_count。

### RefChain

RefChainAgent 当前为规则版单层引用扩展，仅在 `enable_refchain=True` 时执行。

规则：

- 只选择 highly relevant / partially relevant 作为 seed。
- 默认最多 3 个 seed。
- 每个 seed 最多 15 篇 references。
- 总 references 默认最多 50。
- 缺少 OpenAlex ID 或 DOI 的 seed 会跳过并进入 warnings。
- fetcher 由外部注入，测试中使用 fake fetcher。

当前不做递归多层引用扩展，不做引用图全量遍历，也不训练 PaSa 风格 selector。

## Citation-backed Synthesis

SynthesisAgent 当前为规则版 citation-backed synthesis，默认在 SearchService final rerank 后执行。

输入：

- final ranked papers。
- ranked_papers 中的 evidence。
- SearchService warnings。
- source_stats。
- refchain_output。

输出：

- answer_summary
- key_findings
- evidence_table
- citation_coverage
- limitations
- warnings
- status

约束：

- 只允许 evidence source 为 `title`、`abstract`、`venue`、`metadata`。
- citation_key 按 rank 生成，例如 `R1`、`R2`。
- 每个 finding 必须至少包含一个 citation_key。
- 没有 evidence rows 时返回 insufficient evidence，不编造结论。
- limitations 会包含 source errors、warnings、无全文证据、metadata-only 等限制。
- 不调用 LLM，不读取全文 PDF，不引入外部事实。

前端已支持展示 Synthesis Panel。人工验证中，Real Preview 返回论文时，panel 展示了 status、answer_summary、5 条 key findings、22 条 evidence table、coverage counters 和 OpenAlex 503 相关 limitations。

## 前端交互设计

ScholarNavigator 前端工作台包含三个核心区域：

1. Search Workbench  
   提供品牌区、复杂查询输入、示例 query、`top_k`、`run_profile`、Query Evolution、RefChain、模式切换和启动按钮。

2. Run Progress  
   Mock Demo 下展示 mock run 状态和 SSE 事件；Real Preview 下展示一次性 REST loading 和 Preview Transport 说明。

3. Results  
   展示高度相关论文、部分相关论文、method_clusters、timeline、missing_evidence 和可选 Synthesis Panel。

双模式设计：

- Mock Demo：走稳定 mock run API，适合无网络或比赛现场兜底。
- Real Preview：调用 internal api-result preview，真实访问 OpenAlex / arXiv，用于展示真实检索和错误诊断。

前端对 backend unavailable、loading、error、empty candidate 都有友好提示。当前 UI 明确说明 Synthesis 是规则版 metadata/evidence-row synthesis，不代表读取全文 PDF。

## Evaluation 设计与当前实验结果

### 指标设计

当前已实现的离线评测指标包括：

- Recall@K
- Precision@K
- MRR
- nDCG@K
- candidate_count_metrics
- error_rate_metrics

canonical paper matching 支持：

- DOI
- arXiv ID
- OpenAlex ID
- Semantic Scholar ID
- PubMed ID
- title + year fallback

### 离线评测方式

Evaluator 支持注入 fake retriever 和 fake reference_fetcher，分别运行：

- baseline
- query_evolution
- refchain

这样可以在不访问真实外网、不依赖实时 OpenAlex / arXiv 状态的前提下比较不同 feature flag 的效果。

### 当前实验边界

当前 sample fixture 是小型手写 fake 数据，只用于 smoke test 和报告样例，不代表完整 LitSearch / AstaBench benchmark。完整公开数据集适配、隐藏集策略和大规模统计实验尚未完成。

### 当前验证结果

已有记录显示：

- 后端测试：`PYTHONPATH=src pytest -q`，`140 passed, 1 warning`。
- 前端 lint：通过。
- 前端 build：通过。
- Mock Demo：run、SSE、论文卡片展示成功。
- Real Preview：
  - 网络失败场景下能展示 `missing_evidence`。
  - 降并发后两次真实 preview 均返回 HTTP 200。
  - arXiv 返回可用论文，OpenAlex 503 诊断可见。
- Synthesis Panel：
  - Real Preview 返回论文时，panel 成功展示 summary、findings、coverage、limitations 和 evidence rows。

## 成本、延迟与鲁棒性设计

### 成本设计

当前 MVP 不调用 LLM，因此：

- `llm_call_count=0`
- `estimated_input_tokens=0`
- `estimated_output_tokens=0`
- `estimated_total_tokens=0`

Search API 调用数、search rounds、latency、judged_paper_count 会进入 cost_report。后续接入 LLM 时，系统可以沿用同一成本统计结构。

### 延迟设计

SearchService 支持 subquery 并发，默认 `max_workers=4`。internal preview endpoint 通过 `REAL_PREVIEW_MAX_WORKERS` 降低真实检索并发压力，演示建议设置为 `1`。

验证记录中，在 `REAL_PREVIEW_MAX_WORKERS=1` 下，两次 api-result preview 延迟约为 21.1 秒和 16.0 秒。这一设置提高了对外部源的友好性，但会增加延迟。

### 鲁棒性设计

- Connector 层设置 timeout。
- OpenAlex / arXiv 对 429、5xx、timeout、URLError 做轻量 retry/backoff。
- 单个 source 失败不会导致整个 retrieve_papers 失败。
- 单个 subquery 检索失败不会中断 SearchService。
- Query Evolution 无 seed 时返回 warning。
- RefChain 单个 seed 失败不会中断整体。
- 前端展示 missing_evidence，避免空白失败。

## 与参考系统和数据集的关系

### SPAR

项目借鉴 SPAR 的 agent pipeline 思路，包括 Query Understanding、Retrieval、Judgement、Query Evolution、Reranker 和 RefChain。当前实现不是直接复刻 SPAR，而是将其核心模块转化为本项目可测试、可替换的后端服务与 schema。

### PaSa

项目借鉴 PaSa 的 Crawler / Selector、paper queue、Search / Expand / Stop 思想，用于指导 Query Evolution 和 RefChain 的设计。当前 MVP 不实现 SFT、PPO、RL 训练，也不做递归多层引用扩展。

### PaperQA2

项目借鉴 PaperQA2 的 evidence gathering、citation traversal、citation-backed answer 和 insufficient evidence 处理思想。当前 synthesis 只使用 metadata/evidence rows，不读取全文 PDF。

### ai2-scholarqa-lib

项目借鉴 scholar QA 的证据组织、引用约束、answer structure 和 limitations 展示思路。当前落地为 evidence_table、citation_keys、coverage 和 limitations。

### LitSearch

项目借鉴 LitSearch 的学术检索评测方向，使用 Recall@K、Precision@K、MRR、nDCG 等指标。当前尚未接入完整 LitSearch benchmark。

### AstaBench

项目借鉴 AstaBench 对科研 Agent 的评测关注点，例如任务级指标、候选规模、错误率、延迟和可复现评测。当前尚未接入完整 AstaBench benchmark。

## 当前测试与验证结果

截至本报告初稿撰写时，已有记录包括：

- 单元和集成测试：`140 passed, 1 warning`。
- 前端 lint：通过。
- 前端 build：通过。
- 仓库状态审计：已清理 `third_party/paper-qa` 中 `.DS_Store` 删除状态，root tracked 工作区干净。
- Mock Demo 人工验证：通过。
- Real Preview 人工验证：成功展示真实检索失败诊断和真实论文结果两类情况。
- Synthesis Panel 人工验证：通过。

唯一测试 warning 是既有 FastAPI/TestClient Starlette deprecation warning。

## 已知问题与边界

1. 当前是 no-LLM 规则版 MVP，不具备 LLM 级复杂语义理解和自然语言归纳能力。
2. 当前没有读取全文 PDF，所有证据来自 title、abstract、venue、metadata。
3. 当前没有接入完整 LitSearch / AstaBench benchmark，只有本地 fake fixture 评测链路。
4. Real Preview 依赖 OpenAlex / arXiv，可能受到 503、429、timeout 等外部服务影响。
5. Semantic Scholar 和 PubMed connector 尚未实现，biomedical 查询会提示 PubMed 未实现。
6. 公共 `/api/v1/search/runs` 仍是 Mock API，真实搜索暂通过 internal preview endpoint 暴露。
7. 当前 method_clusters 和 timeline 是确定性简化结构，不是深层语义聚类。
8. 当前缺少生产级持久化缓存、任务队列、用户日志、鉴权和部署脚本。

## 后续工作

1. 将 SearchService 以 feature flag 方式接入正式 search run API。
2. 增加 Semantic Scholar、PubMed 等检索源。
3. 接入可选 LLM 增强 Query Understanding、Judgement、Reranking 和 Synthesis，同时保留 no-Key fallback。
4. 增加全文或分段证据检索能力，并严格绑定证据来源。
5. 接入完整 LitSearch / AstaBench 或 SPARBench 数据，形成正式实验表格。
6. 完善缓存、日志、限流、失败重试和成本 dashboard。
7. 优化前端 citation graph、method clusters、timeline 和导出能力。
8. 将本报告扩展为正式参赛报告和答辩 PPT。

## 总结

ScholarNavigator 当前已经形成一个完整但边界清晰的参赛系统雏形。它通过前后端分离、可解释规则 pipeline、真实 OpenAlex / arXiv 检索、错误可观测、结构化 API mapper、Synthesis Panel 和离线评测基础，覆盖了赛题中查询理解、多源检索、相关性判断、结果结构化和效率控制的核心方向。

当前版本不夸大能力：它不调用 LLM，不读取全文 PDF，不声称完成完整 benchmark。它的价值在于先搭建稳定、可测试、可演示、可扩展的工程闭环，为后续 LLM 增强、更多检索源和正式评测接入提供可靠基础。

# Query Evolution and RefChain Design

## Scope

本文档设计当前项目中 Query Evolution 与 RefChain 的落地方案。

本轮只做设计，不实现 Python/TypeScript 业务代码，不修改现有 API，不修改
`frontend/`，不修改 `third_party/`，不调用 LLM，不访问外网。

当前基线已经具备：

- `QueryUnderstandingAgent` 规则版，输出 `SearchPlan`。
- `retrieve_papers`，支持 OpenAlex 和 arXiv 聚合检索。
- `deduplicate_papers`，支持多源与跨 subquery 去重。
- `JudgementAgent` 规则版，输出相关性判断和证据。
- `RerankerAgent` 规则版，输出稳定排序。
- `SearchService`，串联 query understanding、retrieval、dedup、judgement、rerank，并支持 subquery 并发。

`SearchPlan` 已有 `enable_refchain` 和 `enable_query_evolution` 字段，但当前
`SearchService` 尚未执行这两个阶段。本文档只规划后续如何接入。

## 1. PaSa 可借鉴思想

### 1.1 Crawler / Selector 分工

PaSa 将系统拆成两个角色：

- Crawler：决定下一步动作，包括生成检索查询、扩展引用、停止处理当前论文。
- Selector：判断 paper queue 中每篇论文是否满足用户查询。

适合当前项目借鉴的部分：

- 将“发现候选论文”和“判断候选论文”解耦。当前 `retrieve_papers` 和未来
  `QueryEvolutionAgent` / `RefChainAgent` 可以承担 Crawler 的轻量职责，
  `JudgementAgent` 继续承担 Selector 的轻量职责。
- 每次新增候选都必须经过 judgement，避免引用扩展把噪声直接推入最终结果。
- Crawler 不直接决定最终排序，最终排序仍交给 `RerankerAgent`。

不适合 MVP 直接照搬的部分：

- 不训练 Crawler / Selector。
- 不做 SFT、PPO 或 RL。
- 不读取论文全文来选择章节。
- 不使用 Google/Serper 或 ar5iv 作为 MVP 依赖。

### 1.2 paper queue

PaSa 的 paper queue 是一个候选池。Crawler 通过 `[Search]` 和 `[Expand]` 不断向
queue 添加论文，Selector 再逐篇过滤。

当前项目可借鉴为：

- 使用 `ranked_papers` 或 `judgements` 中的高相关论文作为 frontier。
- frontier 只保留有限数量的 seed papers。
- 每个 seed paper 记录来源：初始检索、query evolution、refchain。
- 所有新增论文进入统一候选池后立刻调用 `deduplicate_papers`。

建议 MVP 不引入复杂图结构，先使用简单列表和边记录：

- 候选论文列表：`list[Paper]`
- 引用边：`ReferenceEdge(parent_paper_id, child_paper_id, relation)`
- 检索轨迹：`QueryEvolutionRecord` / `RefChainRecord`

### 1.3 Search / Expand / Stop 动作

PaSa 的 Crawler 有三个动作：

- `[Search]`：生成查询并调用搜索工具。
- `[Expand]`：从当前论文的引用中加入相关论文。
- `[Stop]`：停止当前论文或当前轨迹。

当前项目的轻量映射：

- Search：已有 `SearchSubquery` 和 `retrieve_papers`。
- Expand：未来 `RefChainAgent` 只做单层引用扩展。
- Stop：使用预算和阈值控制，例如 seed 数量、引用数量、候选总数、延迟上限。

MVP 不需要显式训练一个会输出动作 token 的 agent。规则版本只需要根据
`SearchPlan.run_profile`、相关性分数和预算做确定性决策。

### 1.4 成本意识

PaSa 论文中强调 Search 和 Expand action 会显著增加候选数量，因此需要 action
cost 和深度限制。

当前项目必须借鉴这一点：

- 每轮 Query Evolution 生成查询数需要上限。
- RefChain 只能单层扩展。
- 每个 seed paper 的 reference 数量需要上限。
- 每个阶段都要记录 source_stats、warnings、latency_seconds。
- 超预算时输出 warning，而不是静默丢弃。

## 2. SPAR 可借鉴思想

### 2.1 query_fusion

SPAR 原项目的 `pipeline_spar.py` 中，`query_fusion` 会调用 search engine 的
query expansion，保留原始 query，并为每个 query 创建 `SearchNode`。

当前项目已有 `QueryUnderstandingAgent` 生成第一批 `SearchSubquery`，可直接承担
query_fusion 的 MVP 版本。后续 Query Evolution 不应该替代这一层，而应作为首轮
检索后的可选补充。

### 2.2 reference_level_search

SPAR 的 `reference_level_search` 会选择若干 top documents，获取 references，
过滤缺少 title/abstract 的引用，并计算这些 reference 与原始 query 的相似度。

当前项目可借鉴：

- 只从高分候选中选择 seed papers。
- 只保留 metadata 足够的引用论文。
- 引用论文必须重新经过 judgement。
- 引用扩展必须有数量上限。

当前项目不应照搬：

- 不把 irrelevant docs 作为默认扩展 seed。MVP 只扩展 highly relevant 和部分
  high-scoring partially relevant 论文，以降低噪声。
- 不做多层递归引用扩展。

### 2.3 query_expand_from_context

SPAR 的 `query_expand_from_context` 会从当前 level 的 relevant docs 中选择论文，
生成新查询，并去重已经搜索过的 queries。

当前项目可借鉴：

- 基于已判断为相关的论文生成 evolved queries。
- 记录 used_queries，防止重复检索。
- 将新查询作为下一轮 `retrieve_papers` 输入。
- 每轮新查询数量受 `QUERY_NUM_PRUNED` 类似的预算约束。

MVP 规则版不调用 LLM，可从 query analysis、matched_terms、论文标题、venue、
年份和关键词中生成少量稳定查询。

### 2.4 SearchNode 思路

SPAR 的 `SearchNode` 保存 query、children、docs、irrelevant_docs、references、
searched_queries、searched_docs 等状态。

当前项目不需要引入完整 tree，但应保留可追踪结构：

- `SearchPlan.subqueries`：初始查询节点。
- `QueryEvolutionRecord`：记录从哪些 seed papers 生成了哪些 evolved queries。
- `RefChainRecord`：记录从哪些 seed papers 扩展了哪些 references。
- `SearchServiceOutput`：汇总 retrieval_outputs、source_stats、warnings 和 latency。

这样可以满足前端展示运行过程，也避免过早引入复杂树结构。

## 3. 轻量版 Query Evolution

### 3.1 目标

Query Evolution 的目标是提高 recall，尤其处理以下情况：

- 首轮结果过少。
- 首轮结果主题正确但覆盖面不足。
- 用户查询是 survey、recent_progress、method_comparison 或 benchmark_or_dataset。
- 高相关论文暴露出新的方法名、任务名、数据集名或领域术语。

Query Evolution 不负责最终排序，也不绕过 judgement。

### 3.2 MVP 规则

输入：

- `SearchPlan`
- 初始 `RetrievalOutput` 列表
- 初始 `JudgementResult` 列表
- 初始 `RankedPaper` 列表
- `used_queries`
- `QueryEvolutionOptions`

规则：

- 默认最多执行 1 轮。
- 从 highly relevant 论文中选 seed，不足时补充高分 partially relevant 论文。
- `irrelevant` 和 `insufficient_evidence` 不作为 seed。
- 每轮最多生成 1 到 3 个 evolved subqueries。
- evolved query 必须与原始 query 和已搜索 query 去重。
- source_hints 只能使用当前已实现的 `openalex` 和 `arxiv`。
- 如果没有足够 seed 或预算不足，输出 warning 并跳过。

### 3.3 无 LLM query 生成策略

规则版只生成保守、短查询，不编造复杂语义：

- 从 `query_analysis.constraints.methods`、`datasets`、`must_include_terms` 取核心词。
- 从 seed paper 的 title 和 `matched_terms` 中抽取 2 到 5 个关键词。
- 根据 intent 拼接少量模板：
  - survey：`{topic terms} survey review`
  - recent_progress：`{topic terms} recent advances`
  - method_comparison：`{method terms} comparison benchmark`
  - benchmark_or_dataset：`{topic terms} dataset benchmark evaluation`
  - general：`{topic terms} representative papers`
- 如果原始查询为中文或 mixed，可保留原始中文 query，同时生成简短英文关键词版本。
- 不做机器翻译式长句生成，避免错误扩展。

### 3.4 建议新增 schema

建议放入 `src/scholar_agent/core/search_schemas.py`：

```python
class QueryEvolutionOptions(BaseModel):
    enabled: bool = False
    max_rounds: int = 1
    max_evolved_queries: int = 3
    max_seed_papers: int = 5
    min_seed_score: float = 0.45
    max_total_candidates: int = 200


class EvolvedSubquery(BaseModel):
    query: str
    source_hints: list[SourceName]
    priority: int
    purpose: str
    seed_paper_titles: list[str]
    generated_by: Literal["rules", "llm"]
    warnings: list[str]


class QueryEvolutionRecord(BaseModel):
    round_index: int
    seed_count: int
    generated_queries: list[EvolvedSubquery]
    skipped_reasons: list[str]
    latency_seconds: float
```

后续如需更清晰的 service 输出，可增加：

```python
class QueryEvolutionOutput(BaseModel):
    records: list[QueryEvolutionRecord]
    retrieval_outputs: list[RetrievalOutput]
    warnings: list[str]
    latency_seconds: float
```

### 3.5 建议新增 agent 函数

建议文件：`src/scholar_agent/agents/query_evolution.py`

建议函数：

```python
def evolve_queries(
    query_analysis: QueryAnalysis,
    search_plan: SearchPlan,
    judgements: list[JudgementResult],
    ranked_papers: list[RankedPaper],
    used_queries: set[str],
    *,
    options: QueryEvolutionOptions | None = None,
) -> QueryEvolutionRecord:
    ...
```

职责：

- 选择 seed papers。
- 生成 evolved subqueries。
- 过滤重复 query。
- 输出 record 和 warnings。

该 agent 不直接调用外部检索源。真正调用 `retrieve_papers` 的动作由
`SearchService` 统一调度。

## 4. 单层 RefChain

### 4.1 目标

RefChain 的目标是补充“检索源关键词搜索不容易直接召回”的关键参考论文。
它适合：

- survey 查询。
- method lineage 查询。
- 用户需要代表性、奠基性或权威论文。
- 首轮高相关论文已经有可靠 identifiers，可用于查 references。

RefChain 不适合：

- 用户只关心最新进展时无限扩展早期引用。
- 首轮结果质量低时盲目扩展。
- 缺少 OpenAlex ID、DOI 或其他可解析 identifier 的论文。

### 4.2 MVP 规则

输入：

- `QueryAnalysis`
- `RankedPaper` 列表
- reference fetcher 函数
- `RefChainOptions`

规则：

- 只做单层引用扩展。
- 只从 top ranked 且 category 为 highly_relevant 或 partially_relevant 的论文中选 seed。
- 默认最多 3 个 seed papers。
- 每个 seed 最多拉取 10 到 20 篇 references。
- 总 reference candidate 数默认不超过 50。
- references 与初始候选合并后调用 `deduplicate_papers`。
- references 必须再经过 `judge_papers` 和 `rerank_papers`。
- 如果 reference fetcher 失败，记录 warning，不中断主 pipeline。

### 4.3 reference 获取策略

MVP 优先使用 OpenAlex：

- 如果 paper 有 `openalex_id`，调用 OpenAlex Works reference endpoint 或 inverted
  abstract/reference metadata 能力。
- 如果只有 DOI，可先通过 OpenAlex 查询 DOI 对应 work，再读取 referenced works。
- 如果只有 arXiv ID，MVP 不读取 PDF 和全文引用。可先跳过并记录
  `refchain_no_supported_identifier`。

当前 arXiv connector 不应在 MVP 中解析全文参考文献。这样能避免 PDF 解析成本、
引用格式噪声和网络不稳定。

### 4.4 建议新增 schema

建议放入 `src/scholar_agent/core/search_schemas.py`：

```python
class RefChainOptions(BaseModel):
    enabled: bool = False
    max_seed_papers: int = 3
    max_references_per_seed: int = 15
    max_total_references: int = 50
    min_seed_score: float = 0.45
    allowed_sources: list[SourceName] = ["openalex"]


class RefChainSeed(BaseModel):
    paper: Paper
    rank: int
    score: float
    reason: str


class ReferenceEdge(BaseModel):
    seed_paper_id: str
    reference_paper_id: str
    source: SourceName
    relation: Literal["references"]


class RefChainRecord(BaseModel):
    seeds: list[RefChainSeed]
    reference_edges: list[ReferenceEdge]
    raw_reference_count: int
    deduplicated_reference_count: int
    warnings: list[str]
    latency_seconds: float
```

后续也可增加：

```python
class RefChainOutput(BaseModel):
    record: RefChainRecord
    papers: list[Paper]
    source_stats: list[SourceStats]
    warnings: list[str]
    latency_seconds: float
```

### 4.5 建议新增 agent 和 connector 函数

建议文件：`src/scholar_agent/agents/refchain.py`

```python
def expand_refchain(
    query_analysis: QueryAnalysis,
    ranked_papers: list[RankedPaper],
    *,
    fetch_references: ReferenceFetcher,
    options: RefChainOptions | None = None,
) -> RefChainOutput:
    ...
```

建议 connector 扩展：

```python
def fetch_openalex_references(paper: Paper, limit: int = 20) -> list[Paper]:
    ...
```

`RefChainAgent` 只负责选 seed、调用注入的 reference fetcher、汇总 warnings 和
edges。真实 HTTP 调用仍留在 connector 层，测试中通过 fake fetcher 保持离线。

## 5. SearchService 接入方式

### 5.1 推荐执行顺序

建议后续 `SearchService.run_search` 保持现有主干，并在 flags 打开时插入两个可选阶段：

```text
analyze_query
  -> initial retrieve_papers for SearchPlan.subqueries
  -> deduplicate_papers
  -> judge_papers
  -> rerank_papers
  -> optional Query Evolution
       -> evolve_queries
       -> retrieve_papers for evolved subqueries
       -> merge + deduplicate_papers
       -> judge_papers
       -> rerank_papers
  -> optional RefChain
       -> select seed papers from current ranked list
       -> fetch one-layer references
       -> merge + deduplicate_papers
       -> judge_papers
       -> rerank_papers
  -> SearchServiceOutput
```

理由：

- Query Evolution 先补充关键词检索召回。
- RefChain 再基于更好的 ranked list 选择 seed papers。
- 每次引入新候选后都重新 dedup、judge、rerank，保证质量闸门一致。

### 5.2 输出扩展

建议未来扩展 `SearchServiceOutput`：

```python
class SearchServiceOutput(BaseModel):
    search_plan: SearchPlan
    retrieval_outputs: list[RetrievalOutput]
    query_evolution_records: list[QueryEvolutionRecord]
    refchain_record: RefChainRecord | None
    raw_count: int
    deduplicated_count: int
    judgements: list[JudgementResult]
    ranked_papers: list[RankedPaper]
    warnings: list[str]
    source_stats: list[SourceStats]
    latency_seconds: float
```

API 层无需立即改变。现有 preview endpoint 可先忽略这些新增字段，或只在内部调试响应中
展示。Mock API 保持不变。

### 5.3 与现有 flags 的关系

- `enable_query_evolution=False`：保持当前 SearchService 行为。
- `enable_query_evolution=True`：最多执行 1 轮 evolved subquery 检索。
- `enable_refchain=False`：不拉取 references。
- `enable_refchain=True`：最多执行单层 reference expansion。

`fast` profile 建议默认关闭两个阶段，或使用极小预算。`high_recall` 和
`evaluation` profile 可打开更高预算，但必须保持 deterministic。

## 6. 成本、候选数量、延迟和噪声控制

### 6.1 默认预算建议

建议默认值：

- `max_evolution_rounds = 1`
- `max_evolved_queries = 3`
- `max_evolution_seed_papers = 5`
- `max_refchain_seed_papers = 3`
- `max_references_per_seed = 15`
- `max_total_reference_candidates = 50`
- `max_total_candidates_before_judgement = 200`
- `min_seed_score = 0.45`
- `min_seed_category in {"highly_relevant", "partially_relevant"}`

### 6.2 降噪规则

- 所有 query 先做规范化去重。
- 所有 papers 进入 judgement 前都调用 `deduplicate_papers`。
- RefChain 不扩展 insufficient_evidence 或 irrelevant 论文。
- RefChain 引用必须至少有 title，最好有 abstract。
- 缺少 year、venue、abstract 的引用可保留，但要降低 judgement 和 metadata 得分。
- source_stats 和 warnings 必须记录每个阶段的失败和截断原因。

### 6.3 延迟控制

- Query Evolution 的 evolved subqueries 可复用 SearchService 现有 subquery 并发。
- RefChain reference fetching 可使用小规模并发，但必须按 seed 顺序稳定汇总。
- 单个 seed 失败不影响其他 seed。
- preview endpoint 文档必须说明开启这两个阶段后可能访问外网并增加延迟。

### 6.4 成本统计

建议在 SearchService 层汇总：

- retrieval query count
- connector call count
- raw candidates
- deduplicated candidates
- judgement candidate count
- rerank candidate count
- reference fetch count
- warnings count
- per-stage latency

如果后续接 LLM，还需要记录 prompt tokens、completion tokens、LLM call count 和
estimated cost。MVP 规则版不产生 token 成本。

## 7. MVP 不做内容

明确不进入 MVP：

- 不做 RL、PPO、SFT。
- 不训练 Crawler 或 Selector。
- 不实现 PaSa 的 session trajectory sampling。
- 不读取论文全文。
- 不解析 PDF 引用。
- 不接 ar5iv。
- 不做递归多层引用扩展。
- 不做 Google Scholar / Serper 检索。
- 不把 Query Evolution 或 RefChain 接入现有 Mock API。
- 不改变前端 API contract。
- 不让 LLM 决定唯一排序或唯一相关性判断。
- 不在没有 connector 的情况下假装支持 Semantic Scholar 或 PubMed。

## 8. 测试计划

### 8.1 Query Evolution 测试

建议新增 `tests/test_query_evolution.py`：

- 有 highly relevant seeds 时生成 evolved queries。
- 没有相关 seeds 时不生成 queries，并返回 warning。
- generated query 与 used_queries 去重。
- max_evolved_queries 生效。
- source_hints 只包含 `openalex` 和 `arxiv`。
- 中文查询保留原始意图，不生成过度复杂英文句子。
- 同样输入输出稳定，不使用随机数。
- 不调用 LLM，不访问外网。

### 8.2 RefChain 测试

建议新增 `tests/test_refchain.py`：

- 只选择 highly_relevant / partially_relevant seeds。
- max_seed_papers、max_references_per_seed、max_total_references 生效。
- fake fetcher 返回 references 后可生成 edges。
- fake fetcher 抛异常时记录 warning，并继续处理其他 seed。
- reference papers 通过 `deduplicate_papers` 合并。
- 单层扩展，不对 reference 的 references 再扩展。
- 缺少 supported identifier 时跳过并返回 warning。
- 不访问外网。

### 8.3 SearchService 集成测试

建议新增 `tests/test_search_service_evolution_refchain.py`：

- flags 关闭时保持现有输出行为。
- `enable_query_evolution=True` 时会调用 fake retriever 处理 evolved queries。
- `enable_refchain=True` 时会调用 fake reference fetcher。
- 两个阶段新增 candidates 后仍会 dedup、judge、rerank。
- 一个 evolved query 或 seed 失败不影响整个 pipeline。
- warnings、source_stats、latency_seconds 稳定汇总。
- top_k 仍生效。

### 8.4 Connector 测试

如果新增 `fetch_openalex_references`，建议新增或扩展
`tests/test_openalex_connector.py`：

- mock OpenAlex work response。
- 解析 referenced works 为 `Paper`。
- timeout / 非 2xx / 字段缺失返回空列表或 warnings。
- sources 包含 `openalex`。
- 不真实访问外网。

## 9. 实际参考文件路径

本设计实际参考了以下文件：

- `docs/design/spar_module_design.md`
- `docs/design/search_service_runbook.md`
- `docs/reference_papers/pasa.pdf`
- `third_party/pasa/README.md`
- `third_party/pasa/paper_agent.py`
- `third_party/pasa/paper_node.py`
- `third_party/pasa/utils.py`
- `third_party/pasa/agent_prompt.json`
- `third_party/pasa/metrics.py`
- `pipeline_spar.py`
- `search_engine.py`
- `search_node.py`
- `src/scholar_agent/services/search_service.py`
- `src/scholar_agent/core/search_schemas.py`
- `src/scholar_agent/agents/retriever.py`

读取 `docs/reference_papers/pasa.pdf` 时使用本地 PDF 文本提取，未访问外网。

## 10. 无法读取文件

本轮要求中的参考文件均可读取。

没有无法读取的必需文件。`docs/reference_papers/pasa.pdf` 可通过本地 PDF 提取读取。


# SPAR Module Design: Query Understanding, Judgement, Reranker

## Scope

本文档设计 `QueryUnderstandingAgent`、`JudgementAgent`、`RerankerAgent` 在当前 ScholarNavigator 参赛系统中的落地方案。

当前主线是 Real Search only。本文保留模块设计背景，不描述产品级 mock
兜底路径。

## 1. SPAR 可借鉴的核心思想

### 1.1 Query Understanding

SPAR 的 Query Understanding Agent 不只是把用户问题改写成一个关键词查询，而是先理解用户的学术检索意图，再决定检索策略。可借鉴点包括：

- 识别查询意图：如 survey、recent advances、method comparison、benchmark/dataset、application、specific paper family 等。
- 识别研究领域和关键约束：主题、方法、数据集、任务、时间范围、venue 或发表类型。
- 判断是否需要查询扩展：短查询、歧义查询、跨领域查询、综述型查询通常需要多个子查询。
- 输出结构化 SearchPlan，而不是直接调用搜索源。
- 对中文查询先做语义归一和英文检索短语生成，但保留原始中文意图。

当前项目应避免在 MVP 阶段过度依赖 LLM。无 Key fallback 可以使用规则和轻量文本处理先生成可用 SearchPlan。

### 1.2 Source Selection

SPAR 根据查询意图和领域选择不同检索源。原始 SPAR 设计中涉及 Google、ArXiv、OpenAlex、Semantic Scholar、PubMed 等来源。当前项目已经实现 OpenAlex 和 arXiv connector，因此 MVP source selection 应先限制为：

- `openalex`：覆盖面广，适合跨学科、综述、引用量、venue、年份等元数据需求。
- `arxiv`：适合计算机、机器学习、数学、物理等预印本密集领域，尤其适合最新论文。

后续再接入 Semantic Scholar 和 PubMed。Source selection 需要给出选择理由，并保留 requested_sources 到结果中，便于前端展示和成本统计。

### 1.3 Query Refinement

SPAR 的查询扩展包括：

- survey-focused 查询：生成 methods、applications、history、future directions 等不同角度的子查询。
- domain-aware 查询：补充领域术语、任务名、方法名和同义表述。
- temporal-aware 查询：将 `recent`、`latest`、`since 2022` 等时间约束带入子查询。
- retrieval-context 查询演化：基于已找到的高相关论文再生成后续查询。

当前 MVP 只建议实现第一层 query refinement：

- 从用户原始查询生成 1 到 5 个子查询。
- 子查询带 `source_hints`、`intent`、`constraints`、`priority`。
- 暂不实现 Query Evolution 和 RefChain，但 SearchPlan 需要为后续扩展预留字段。

### 1.4 Relevance Judgement

SPAR 的 Judgement Agent 是检索结果进入 related pool 的关键过滤器。可借鉴点包括：

- 对每篇论文进行 query-paper 相关性判断，而不是只依赖搜索源排序。
- 判断依据包括标题、摘要、关键词、venue、年份、来源和引用信息。
- 输出可解释的 score、category、reasoning、evidence。
- 使用阈值将候选论文分成 highly relevant、partially relevant、irrelevant。
- 对引用扩展得到的论文同样走 judgement，以控制噪声。

MVP 需要先提供无 LLM 的可测试规则版 judgement，再在 LLM 可用时增强自然语言判断和证据归纳。

### 1.5 Reranking

SPAR 的 Reranker Agent 在 judgement 之后重新排序最终列表。核心思想是综合：

- relevance：与查询主题、方法、任务、数据集和约束的匹配程度。
- authority：引用量、venue、来源覆盖、多源命中、作者或期刊会议权威度。
- timeliness：是否满足查询中的时间约束；如果用户要求 recent/latest，则提高近期论文权重。

当前项目的 Reranker 应先做 deterministic reranking，保证可复现和可测试。LLM reranking 后续只作为解释增强或 tie-breaker，不能成为唯一排序来源。

### 1.6 Timeliness / Authority / Relevance Signals

建议保留以下信号，后续逐步接入：

- Relevance signals：标题匹配、摘要匹配、方法/任务/数据集词匹配、语义相似度、Judgement score。
- Authority signals：citation_count、venue、source 数量、DOI/OpenAlex/Semantic Scholar 等 identifier 完整度。
- Timeliness signals：year、显式时间范围匹配、recent/latest 查询偏好。
- Evidence signals：摘要句子、标题短语、来源命中、identifier 和链接完整性。

## 2. 与当前项目的对应关系

### 2.1 推荐模块位置

建议新增以下 agent 模块：

- `src/scholar_agent/agents/query_understanding.py`
- `src/scholar_agent/agents/judgement.py`
- `src/scholar_agent/agents/reranker.py`

建议新增或扩展结构化 schema：

- 优先新增 `src/scholar_agent/core/search_schemas.py`，集中放置 agent 间流转模型。
- 如模型和论文对象强耦合，也可扩展 `src/scholar_agent/core/paper_schemas.py`。
- 不建议把 agent 内部数据结构放入 FastAPI 的 `core/api_schemas.py`，避免 API contract 和内部 pipeline contract 互相污染。

### 2.2 可复用的现有模块

当前已有模块可以直接作为第一层 pipeline 的基础：

- `src/scholar_agent/core/paper_schemas.py`：复用 `Paper`、`PaperIdentifiers`、`PaperUrls`。
- `src/scholar_agent/core/dedup.py`：复用 `deduplicate_papers` 合并多源论文。
- `src/scholar_agent/agents/retriever.py`：复用 `retrieve_papers` 和 `RetrievalOutput` 作为候选论文入口。
- `src/scholar_agent/connectors/openalex.py`：由 retriever 间接调用。
- `src/scholar_agent/connectors/arxiv.py`：由 retriever 间接调用。
- `src/scholar_agent/core/api_schemas.py`：Real Search API schema 可作为前端
  输出格式参考，但不建议直接承担内部 agent schema。

### 2.3 需要扩展的数据结构

建议内部 pipeline 增加以下 Pydantic 模型：

- `QueryConstraint`
  - `time_range`
  - `venues`
  - `methods`
  - `datasets`
  - `domains`
  - `must_include_terms`
  - `exclude_terms`
- `QueryAnalysis`
  - `original_query`
  - `language`
  - `intent`
  - `domain`
  - `constraints`
  - `needs_expansion`
  - `reasoning`
- `SearchSubquery`
  - `query`
  - `source_hints`
  - `priority`
  - `purpose`
- `SearchPlan`
  - `query_analysis`
  - `subqueries`
  - `selected_sources`
  - `limit_per_source`
  - `enable_refchain`
  - `enable_query_evolution`
  - `run_profile`
- `JudgementResult`
  - `paper`
  - `score`
  - `category`
  - `reasoning`
  - `evidence`
  - `matched_terms`
  - `warnings`
- `RankedPaper`
  - `paper`
  - `rank`
  - `final_score`
  - `relevance_score`
  - `authority_score`
  - `timeliness_score`
  - `ranking_reason`
  - `evidence`

这些模型后续可以映射到现有 FastAPI `SearchRunResultResponse`，但不应要求前端立即改变 API contract。

## 3. 建议模块和函数签名

### 3.1 QueryUnderstandingAgent

建议文件：`src/scholar_agent/agents/query_understanding.py`

建议签名：

```python
def analyze_query(
    query: str,
    *,
    top_k: int = 20,
    run_profile: str = "balanced",
    enable_refchain: bool = False,
    enable_query_evolution: bool = False,
) -> SearchPlan:
    ...
```

可选类封装：

```python
class QueryUnderstandingAgent:
    def analyze(self, query: str, options: QueryUnderstandingOptions) -> SearchPlan:
        ...
```

职责：

- 解析原始查询。
- 判断查询意图和领域。
- 抽取时间、方法、数据集、venue 等约束。
- 选择当前支持的 sources。
- 生成 subqueries。
- 返回 SearchPlan。

### 3.2 JudgementAgent

建议文件：`src/scholar_agent/agents/judgement.py`

建议签名：

```python
def judge_papers(
    query_analysis: QueryAnalysis,
    papers: list[Paper],
    *,
    threshold_high: float = 0.72,
    threshold_partial: float = 0.45,
) -> list[JudgementResult]:
    ...
```

可选类封装：

```python
class JudgementAgent:
    def judge(self, query_analysis: QueryAnalysis, papers: list[Paper]) -> list[JudgementResult]:
        ...
```

职责：

- 对候选论文做相关性评分。
- 输出 highly relevant、partially relevant、irrelevant 分类。
- 保留可解释 reason 和 evidence。
- 不直接排序最终列表。

### 3.3 RerankerAgent

建议文件：`src/scholar_agent/agents/reranker.py`

建议签名：

```python
def rerank_papers(
    query_analysis: QueryAnalysis,
    judged_papers: list[JudgementResult],
    *,
    top_k: int = 20,
) -> list[RankedPaper]:
    ...
```

可选类封装：

```python
class RerankerAgent:
    def rerank(self, query_analysis: QueryAnalysis, judged_papers: list[JudgementResult], top_k: int) -> list[RankedPaper]:
        ...
```

职责：

- 综合 relevance、authority、timeliness 生成最终分数。
- 输出稳定 rank。
- 保留 ranking_reason。
- 为前端 method clusters、timeline、missing evidence 提供结构化依据。

## 4. 每个模块的 MVP 范围

### 4.1 QueryUnderstandingAgent MVP

无 LLM fallback：

- 中英文查询语言检测。
- 基于关键词和正则抽取时间约束，例如 `recent`、`latest`、`since 2020`、`2021-2024`、`近三年`。
- 基于词表识别意图，例如 survey、comparison、benchmark、application、method。
- 基于领域词表选择 `arxiv` 或 `openalex`，默认两者都选。
- 生成少量可解释 subqueries。

有 LLM 后增强：

- 复杂查询分解。
- 领域术语标准化。
- 中译英学术检索短语生成。
- 约束抽取置信度和歧义解释。

暂不实现：

- Retrieval-context Query Evolution。
- 多轮澄清问题。
- 训练式 query planner。

### 4.2 JudgementAgent MVP

无 LLM fallback：

- 标题、摘要、venue 的关键词覆盖率评分。
- 方法、数据集、领域词命中加权。
- 时间约束不满足时降权。
- 缺少摘要或标题时给 warning。
- 输出 deterministic score 和 evidence。

有 LLM 后增强：

- 复杂语义相关性判断。
- 摘要证据句提取。
- 判断论文是否只是背景相关而非核心相关。
- 对用户查询中的细粒度条件做逐项满足性判断。

暂不实现：

- 大规模 pairwise LLM 比较。
- 对全文 PDF 的证据读取。
- RefChain 论文的单独 judgement 策略。

### 4.3 RerankerAgent MVP

无 LLM fallback：

- `final_score = relevance_weight * relevance + authority_weight * authority + timeliness_weight * timeliness`。
- citation_count 做 log 归一。
- 多源命中和 identifier 完整度作为 authority bonus。
- 有 explicit recent/latest 查询时提高 timeliness 权重。
- 输出稳定 tie-breaker：final_score、year、citation_count、title。

有 LLM 后增强：

- 为 top N 生成更自然的排序解释。
- 在分数接近时做 tie-breaker。
- 根据用户意图调整 authority/timeliness/relevance 的解释权重。

暂不实现：

- 使用 LLM 对所有候选做 pairwise tournament。
- 基于作者声誉数据库排序。
- venue rank 外部数据源接入。

## 5. Prompt 文件规划

后续建议新增以下 prompt 文件，但本轮不创建 prompt 内容文件：

- `prompts/query_understanding.md`
  - 输入：原始查询、run_profile、top_k、支持的 sources。
  - 输出：JSON 格式 QueryAnalysis 和 SearchPlan。
  - 要求：保留原始查询，不编造 sources，不输出 API key，不承诺真实结果。

- `prompts/relevance_judgement.md`
  - 输入：QueryAnalysis、Paper metadata。
  - 输出：score、category、reasoning、evidence、matched_terms。
  - 要求：先判断核心主题是否一致，再判断方法/任务/数据集/时间约束；证据必须来自传入 metadata。

- `prompts/reranking.md`
  - 输入：QueryAnalysis、JudgementResult 列表、候选论文特征。
  - 输出：rank、final_score、ranking_reason。
  - 要求：不得引入外部事实；authority、timeliness、relevance 的解释需要可追踪。

Prompt 调用层应通过统一 LLM client 注入，并支持无 Key fallback。Prompt 文件不应包含任何 API Key 或私有配置。

## 6. 测试计划

### 6.1 tests/test_query_understanding.py

覆盖：

- 中文长查询可生成 SearchPlan。
- recent/latest/since 年份约束可解析。
- 机器学习类查询默认包含 `arxiv` 和 `openalex`。
- biomedical/pubmed 类查询在 PubMed 未接入时返回 warning 或降级到 `openalex`。
- `run_profile` 影响 subquery 数量或 limit。
- 空查询或过短查询返回可控错误。

### 6.2 tests/test_judgement.py

覆盖：

- 标题和摘要强命中时 classified 为 highly relevant。
- 只有背景词命中时 classified 为 partially relevant 或 irrelevant。
- 时间约束不满足时分数下降。
- 缺少 abstract 时不崩溃，并输出 warning。
- evidence 只来自论文 title/abstract/metadata。
- 分数和分类阈值稳定。

### 6.3 tests/test_reranker.py

覆盖：

- relevance score 主导默认排序。
- recent 查询中较新论文获得 timeliness bonus。
- citation_count 和多源命中提高 authority score。
- final_score tie 时排序稳定。
- top_k 生效。
- ranking_reason 非空且不引用不存在证据。

所有测试必须 mock LLM client；无 LLM fallback 测试应完全离线运行。

## 7. 接入 retrieve_papers 的方式

推荐第一阶段 pipeline：

1. `QueryUnderstandingAgent.analyze(...)` 生成 `SearchPlan`。
2. 对 `SearchPlan.subqueries` 逐个调用 `retrieve_papers(...)`。
3. `retrieve_papers` 使用 `selected_sources` 或每个 subquery 的 `source_hints`。
4. 汇总所有 `RetrievalOutput.papers`。
5. 再调用 `deduplicate_papers(...)` 做跨 subquery 去重。
6. `JudgementAgent.judge(...)` 对去重后的候选论文打分分类。
7. `RerankerAgent.rerank(...)` 生成最终 `RankedPaper` 列表。
8. API 层把内部结果映射到稳定的前端 response schema。

建议伪流程：

```text
user query
  -> QueryUnderstandingAgent
  -> SearchPlan
  -> retrieve_papers per subquery/source
  -> deduplicate_papers
  -> JudgementAgent
  -> RerankerAgent
  -> API response mapper
```

`retrieve_papers` 当前签名为：

```python
retrieve_papers(query: str, limit_per_source: int = 20, sources: list[str] | None = None) -> RetrievalOutput
```

因此 SearchPlan 到 retriever 的最小接入方式是：

- `query = subquery.query`
- `limit_per_source = search_plan.limit_per_source`
- `sources = subquery.source_hints or search_plan.selected_sources`

当前 SearchService 已作为 Real Search 主线服务层，负责连接 query
understanding、retrieval、judgement、reranking 和 API mapper。

## 8. 风险和约束

- 不要过度依赖 LLM：QueryUnderstanding、Judgement、Reranker 都必须有 deterministic fallback。
- 控制 API 调用成本：SearchPlan 需要限制 subquery 数量、source 数量和 limit_per_source。
- 控制 Token 成本：LLM judgement 只对 top candidates 或规则不确定样本启用。
- 保留无 Key fallback：没有 LLM key 时仍能完成搜索、去重、规则评分和排序。
- 保证前端 API contract 稳定：内部 schema 可以演进，但 FastAPI response 字段应通过 mapper 稳定输出。
- 避免 source selection 过早复杂化：当前只支持 `openalex` 和 `arxiv`，不要在实现中假装 PubMed/Semantic Scholar 已可用。
- 结果解释必须可追踪：ranking_reason 和 evidence 不能编造传入 metadata 之外的信息。
- Reranker 不应掩盖 Judgement：低相关论文即使 citation_count 高，也不能排到高度相关论文前面。

## 9. 实际参考的文件路径

本设计实际参考了以下路径：

- `AGENTS.md`
- `docs/reference_papers/spar.pdf`
- `legacy/spar_original/pipeline_spar.py`
- `legacy/spar_original/search_engine.py`
- `legacy/spar_original/rerank.py`
- `legacy/spar_original/search_node.py`
- `legacy/spar_original/instruction.py`
- `docs/design/architecture.md`
- `docs/design/development_plan.md`
- `docs/design/retriever_runbook.md`
- `src/scholar_agent/core/paper_schemas.py`
- `src/scholar_agent/core/dedup.py`
- `src/scholar_agent/agents/retriever.py`
- `src/scholar_agent/connectors/openalex.py`
- `src/scholar_agent/connectors/arxiv.py`
- `src/scholar_agent/connectors/__init__.py`

## 10. 无法读取或缺失的参考文件

- `docs/notes/spar_notes.md`：当前仓库中未找到该文件，因此未能参考。
- `docs/reference_papers/spar.pdf`：系统未安装 `pdftotext`，但已通过 Python PDF 工具读取文本内容，因此该 PDF 可参考。

## 11. 后续落地建议

建议按以下顺序实现：

1. 新增内部 search schema，不改变 API response。
2. 实现 `QueryUnderstandingAgent` 的无 LLM fallback。
3. 将 SearchPlan 接到 `retrieve_papers`，通过 SearchService 和测试验证真实
   检索链路。
4. 实现 `JudgementAgent` 规则版并补测试。
5. 实现 `RerankerAgent` 规则版并补测试。
6. 再规划 LLM client、prompt 文件和可选 LLM 增强路径。

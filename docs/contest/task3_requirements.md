# 华为企业赛题三需求文档

---

## 1. 赛题基本信息

- **赛题编号**：华为企业赛题三
- **题目名称**：科研场景下复杂学术查询的智能论文搜索与推荐
- **任务类型**：学术搜索 / 文献推荐 / LLM Agent / RAG / 信息检索
- **核心目标**：构建一个端到端的学术论文智能搜索系统，针对自然语言描述的复杂学术查询，自动完成查询理解、多维度多策略检索、论文综合排序、搜索结果归纳整理。

---

## 2. 题目背景

科研工作流中，文献检索是基础且耗时的环节。研究者通常需要根据复杂、细粒度的研究问题，在海量学术文献中找到全面且精准的相关论文集合。

该需求超出传统关键词搜索引擎的能力边界，因为系统不仅要理解查询的语义意图，还需要具备：

1. 多轮检索能力；
2. 引文网络探索能力；
3. 跨文献关联推理能力；
4. 面向不同查询意图的结构化归纳能力。

---

## 3. 当前方案面临的挑战

赛题文档明确指出当前方案仍面临以下问题：

### 3.1 查询理解不充分

用户的学术查询往往包含多维度约束，例如：

- 研究主题；
- 方法；
- 数据集；
- 应用领域；
- 时间范围；
- 发表 venue；
- 作者或机构；
- 论文类型，例如 survey、benchmark、method paper、application paper。

系统需要从自然语言查询中解析这些约束，而不是只做关键词匹配。

### 3.2 检索覆盖率与精确度的平衡

论文搜索的核心矛盾是：

- 要保证高召回率，尽可能找全相关论文；
- 又要控制噪声，避免返回大量不相关或低质量论文。

因此系统需要在 Recall 和 Precision 之间取得平衡，并最终优化 F1。

### 3.3 权威性、时效性、相关性、多样性的权衡

排序不能只看相关性，还需要综合考虑：

- 论文是否权威；
- 论文是否足够新；
- 论文是否与查询细粒度匹配；
- 论文集合是否覆盖不同研究方向；
- 是否避免重复或同质化结果。

### 3.4 搜索结果归纳和结构化展示

系统不应只返回论文列表，还需要根据用户意图进行归纳整理，例如：

- 高度相关论文；
- 部分相关论文；
- 方法路线；
- 数据集或 benchmark；
- 时间线；
- 代表性工作；
- 引文关系；
- 研究趋势；
- 证据不足或不确定信息。

---

## 4. 总体任务目标

参赛者需要构建一个端到端的学术论文智能搜索系统，能够针对自然语言复杂查询，完成以下流程：

```text
用户复杂学术查询
  ↓
查询理解与分解
  ↓
多维度、多策略检索
  ↓
搜索结果过滤与去噪
  ↓
论文相关性判断
  ↓
论文综合排序
  ↓
搜索结果归纳整理
  ↓
结构化展示最终结果
```

---

## 5. 核心功能要求

### 5.1 查询理解与分解

系统需要：

1. 解析用户自然语言查询中的核心研究意图；
2. 识别关键实体；
3. 识别方法论约束；
4. 识别数据、领域限定等多维检索条件；
5. 对复杂查询进行子查询分解；
6. 将宽泛或组合式的学术问题拆解为可独立检索的子问题；
7. 支持查询改写与扩展；
8. 生成更适合检索引擎的查询。

#### 工程实现建议

建议实现 `QueryUnderstandingAgent`，输出结构化 `SearchPlan`：

```json
{
  "intent_type": "survey | recent_progress | method_comparison | dataset_search | application_search | paper_finding",
  "domain": "computer science / biomedicine / material science / ...",
  "time_range": {"start": 2020, "end": 2026},
  "must_have_terms": [],
  "excluded_terms": [],
  "source_preferences": ["Semantic Scholar", "OpenAlex", "arXiv", "PubMed"],
  "expanded_queries": []
}
```

---

### 5.2 基于大模型的自主搜索策略迭代优化

系统需要：

1. 自主规划搜索词进行搜索；
2. 过滤不相干、低质量论文；
3. 支持迭代式检索策略；
4. 根据已找到的相关论文信息，动态调整检索策略和关键词。

#### 工程实现建议

建议实现以下模块：

- `RetrieverAgent`
- `JudgementAgent`
- `QueryEvolverAgent`
- `RefChainExpansion`

推荐迭代流程：

```text
初始查询
  ↓
生成多个子查询
  ↓
多源检索
  ↓
相关性判断
  ↓
保留高相关论文
  ↓
基于高相关论文生成 follow-up query
  ↓
执行下一轮检索
  ↓
达到轮数、成本或结果数量阈值后停止
```

停止条件建议包括：

- 达到最大检索轮数；
- 高相关论文数量已足够；
- API 调用次数达到预算；
- Token 消耗达到预算；
- 新增高相关论文数量低于阈值；
- 端到端延迟超过阈值。

---

### 5.3 论文综合排序

系统需要：

1. 基于论文标题、摘要及可用全文信息；
2. 对候选论文与原始查询进行细粒度相关性评估；
3. 输出经过排序的最终论文列表；
4. 区分高度相关和部分相关的结果。

#### 工程实现建议

建议实现 `RerankerAgent`，综合考虑：

- `relevance_score`：相关性得分；
- `recency_score`：时效性；
- `authority_score`：权威性，例如引用量、venue、作者影响力；
- `diversity_score`：多样性；
- `source_confidence`：多个检索源是否共同返回；
- `query_constraint_match`：是否满足时间、方法、数据集、领域等约束。

建议输出：

```json
{
  "rank": 1,
  "paper": {},
  "relevance_score": 0.92,
  "category": "highly_relevant",
  "ranking_reason": "该论文直接研究用户查询中的核心方法，并满足时间范围约束。"
}
```

---

### 5.4 搜索结果归纳整理

系统需要：

1. 根据用户查询意图，自主整理归纳搜索结果；
2. 返回结构化展示；
3. 展示形式可以包括列表、关系图等。

#### 工程实现建议

建议实现 `SynthesizerAgent`，至少输出：

- 查询理解结果；
- 检索策略；
- 高度相关论文列表；
- 部分相关论文列表；
- 方法分类；
- 时间线；
- 代表性工作；
- 论文之间的关系；
- 证据不足或不确定信息；
- API 调用次数；
- Token 消耗；
- 端到端耗时。

---

## 6. 技术要求

### 6.1 LLM 使用要求

参赛者可使用开源或商业 LLM，但需要注明具体模型及版本。

文档鼓励使用开源模型，例如：

- Qwen；
- DeepSeek；
- 其他可复现开源模型。

#### 工程注意事项

系统应记录：

- 使用的模型名称；
- 模型版本；
- 调用方式；
- Token 消耗；
- 调用次数；
- 是否使用缓存；
- 是否支持无 LLM fallback。

---

### 6.2 学术搜索 API 要求

检索后端需要至少对接一种学术搜索 API，例如：

- Semantic Scholar；
- OpenAlex；
- PubMed。

#### MVP 建议

MVP 阶段至少实现：

1. OpenAlex；
2. arXiv；
3. Semantic Scholar；
4. PubMed 骨架或可选实现。

其中，OpenAlex 和 arXiv 通常更容易快速跑通；Semantic Scholar 和 PubMed 用于增强覆盖率和专业领域泛化。

---

### 6.3 成本控制要求

方案需要具备合理的成本控制意识。评分中会考虑：

- API 调用次数；
- Token 消耗量；
- 端到端延时。

#### 工程实现建议

需要在 `PipelineTrace` 中记录：

```json
{
  "api_call_count": 0,
  "llm_call_count": 0,
  "estimated_input_tokens": 0,
  "estimated_output_tokens": 0,
  "latency_seconds": 0.0,
  "cache_hit_count": 0,
  "search_rounds": 0
}
```

---

## 7. 参考数据集

赛题文档给出的参考数据集：

1. PaSa  
   `https://github.com/bytedance/pasa`

2. AstaBench  
   `https://github.com/allenai/asta-bench`

#### 开发用途

- PaSa：参考 AutoScholarQuery、RealScholarQuery、Crawler/Selector 架构；
- AstaBench：参考科研 Agent 评测体系、PaperFindingBench、ScholarQA 等任务；
- LitSearch：可作为额外公开检索评测集；
- SPARBench：如代码和数据可用，可作为高级评测参考。

---

## 8. 参考文献

赛题文档列出的参考文献包括：

1. He Y, Huang G, Feng P, et al. **PaSa: An LLM Agent for Comprehensive Academic Paper Search.** ACL 2025. arXiv:2501.10120.
2. Ajith A, Xia M, Chevalier A, et al. **LitSearch: A Retrieval Benchmark for Scientific Literature Search.** EMNLP 2024. arXiv:2407.18940.
3. Feldman S, et al. **AstaBench: Rigorous Benchmarking of AI Agents with a Scientific Research Suite.** arXiv:2510.21652, 2025.
4. Shi X, Li Y, Kou Q, et al. **SPAR: Scholar Paper Retrieval with LLM-based Agents for Enhanced Academic Search.** arXiv:2507.15245, 2025.
5. Skarlinski M, et al. **Language Agents for Answering Questions from Scientific Literature.** NeurIPS 2024.
6. Khattab O, Santhanam K, Li X, et al. **Demonstrate-Search-Predict: Composing Retrieval and Language Models for Knowledge-Intensive NLP.** arXiv:2212.14024, 2022.
7. Feng P, He Y, Huang G, et al. **AGILE: A Novel Framework of LLM Agents.** NeurIPS 2024.
8. Muennighoff N, et al. **GritLM: Generative Representational Instruction Tuning.** arXiv:2402.09906, 2024.
9. Press O, Zhang M, Min S, et al. **Measuring and Narrowing the Compositionality Gap in Language Models.** arXiv:2210.03350, 2022.
10. Lee D, Sohn S S, Lee B, et al. **Domain-aligned LLM Framework for Trustworthy Scientific Q/A via Query Reformulation RAG.** ChemRxiv, 2025.

---

## 9. 参考系统

赛题文档给出的参考系统：

| 系统 | 核心方法 | 性能参考 |
|---|---|---|
| PaSa-7B | Crawler + Selector 双 Agent + RL 训练 | RealScholarQuery 上 Recall@20 超 Google+GPT-4o 37.78% |
| SPAR | RefChain 查询分解 + 查询演化 + 多 Agent | AutoScholar 上 F1=0.38，超 PaSa 56% |
| Ai2 Paper Finder | 多索引语义检索 + LLM 改写 + 引文追踪 | PaperFindingBench 上得分超 ReAct Agent 一倍以上 |
| PaperQA2 | 全文检索 + LLM QA | LitQA2 上表现优异 |

---

## 10. 推荐 MVP 范围

MVP 阶段建议实现：

1. 查询理解；
2. 多源检索；
3. 论文去重；
4. 相关性判断；
5. 重排序；
6. 单层 RefChain；
7. Query Evolution；
8. 结构化输出；
9. LitSearch 小规模评测；
10. 成本和延迟统计。

MVP 阶段不建议实现：

1. PaSa 风格 SFT/PPO/RL 训练；
2. 全量 AstaBench 评测；
3. 大规模本地论文向量库；
4. 复杂 Web 前端；
5. 需要大量 GPU 的模型训练。

---

## 11. 验收清单

开发过程中，每个版本至少检查以下内容：

- [ ] 能输入复杂学术查询；
- [ ] 能解析查询意图；
- [ ] 能生成多个检索子查询；
- [ ] 至少能调用一种学术搜索 API；
- [ ] 能过滤不相关论文；
- [ ] 能区分高度相关和部分相关；
- [ ] 能输出结构化 Markdown；
- [ ] 能输出 JSON；
- [ ] 能记录 API 调用次数；
- [ ] 能记录 Token 消耗；
- [ ] 能记录端到端延迟；
- [ ] 能在公开数据集上做初步评测；
- [ ] 能生成比赛报告所需实验结果。

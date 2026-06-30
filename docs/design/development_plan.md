# 前后端分离开发计划

## 1. 总体原则

开发计划围绕四条线并行推进：

1. 后端线：论文搜索 Agent、检索 API、缓存、日志、成本统计。
2. 前端线：高级 UI、交互、过程展示、结果可视化和演示体验。
3. 评测线：公开数据集评测、指标计算、消融实验和效率实验。
4. 文档线：架构、API、运行说明、实验报告和可解释性材料。

优先级依据赛题评分：F1 Score 最高，运行效率其次，结构化输出第三。Web UI 是演示与专家评分的重要支撑，但不能替代后端检索质量。

## 2. 阶段划分

| 阶段 | 目标 | 验收标准 |
|---|---|---|
| P0 Baseline | 跑通后端最小闭环 | 一个查询可返回结构化论文列表、成本统计和 JSON |
| P1 MVP | 支持多源检索、去重、Judgement、Reranker | LitSearch 小样本可评测，前端可展示运行过程 |
| P2 Alpha | 加入 Query Evolution、单层 RefChain、缓存 | 召回与 F1 有可度量提升，效率指标可导出 |
| P3 Beta | 完成评测 API、消融实验、前端演示页 | 可演示检索全过程与评测结果 |
| P4 Final | 完成报告、稳定性、部署与 Demo | README、报告、接口文档、实验结果齐备 |

## 3. 后端开发线

### P0：后端骨架

- 建立 `src/scholar_agent` Python 包结构。
- 定义 Pydantic 核心模型：
  - `SearchRequest`
  - `SearchPlan`
  - `Paper`
  - `PaperIdentifier`
  - `CandidatePaper`
  - `RankedPaper`
  - `SearchResult`
  - `PipelineTrace`
  - `CostReport`
- 建立配置模块，从环境变量读取 API Key、模型名、timeout、预算上限。
- 建立统一日志、request id、run id。
- 建立外部调用基类，强制 timeout、异常处理、日志和成本记录。

### P1：检索闭环

- 实现 `QueryUnderstandingAgent` 的结构化输出。
- 实现 OpenAlex 与 arXiv 检索连接器。
- 增加 Semantic Scholar 连接器，若 key 不可用则支持降级。
- 预留 PubMed 连接器接口。
- 实现论文去重与 ID 归一。
- 实现规则初筛，减少进入 LLM Judgement 的候选数量。
- 实现 `JudgementAgent` 与 `RerankerAgent`。
- 实现 `SynthesizerAgent`，输出 JSON 与 Markdown。

### P2：召回增强与效率控制

- 实现 `QueryEvolverAgent`，默认最多 1 轮演化。
- 实现单层 `RefChainExpansion`。
- 实现 early stop：
  - 最大轮数
  - 最大候选数
  - 最大 LLM 调用次数
  - 最大 Token
  - 最大延迟
  - 新增高相关论文低于阈值
- 实现缓存：
  - 搜索 API 返回
  - 论文详情
  - Query Understanding
  - Judgement
  - Reranking
  - 最终结果

### P3：后端 API

- 实现 REST API：
  - 创建检索任务
  - 查询任务状态
  - 获取检索结果
  - 取消任务
  - 导出 JSON/Markdown
  - 查询成本统计
  - 查询连接器健康状态
- 实现 SSE 事件流：
  - 阶段开始/结束
  - 中间候选数量
  - 子查询列表
  - 外部 API 调用摘要
  - 成本变化
  - 错误与降级事件
- 增加 API contract 测试。

### P4：稳定性与复现

- 完善无 Key fallback。
- 完善错误码。
- 增加外部 API mock 测试。
- 增加端到端后端测试。
- 输出稳定实验配置与运行脚本。

## 4. 前端开发线

前端后续应放入独立 `frontend/` 目录。本阶段只规划，不创建代码。

### P0：信息架构

- 确定页面：
  - 检索工作台
  - 运行详情
  - 论文结果
  - 评测面板
  - 成本与日志
  - 安全配置状态
- 确定 REST + SSE 数据流。
- 确定前端只使用后端安全配置，不读取 API Key。

### P1：检索演示体验

- 实现复杂查询输入区。
- 实现约束配置：
  - 时间范围
  - venue
  - 数据集
  - 方法关键词
  - 检索源选择
  - top-k
  - 预算上限
- 实现运行进度视图：
  - 查询理解
  - 子查询生成
  - 多源检索
  - 去重
  - Judgement
  - Query Evolution
  - RefChain
  - Reranking
  - Synthesis

### P2：结果可视化

- 论文结果表：
  - rank
  - title
  - year
  - venue
  - authors
  - relevance score
  - category
  - identifiers
  - source links
- 论文详情抽屉：
  - 摘要
  - 相关性解释
  - 命中约束
  - 证据来源
  - 引文关系
- 方法分类视图。
- 时间线视图。
- 引用网络图，必须提供表格 fallback。
- 成本统计图。

### P3：评测与演示

- 评测任务提交页。
- 评测进度页。
- 指标总览：
  - Precision@K
  - Recall@K
  - F1@K
  - API call count
  - Token usage
  - latency
  - cache hit count
- 消融实验对比视图。
- Demo 模式：预置示例查询和可复现运行记录。

### P4：可用性与质量

- 响应式适配 375px、768px、1024px、1440px。
- 键盘可达与可见焦点。
- 长表格虚拟滚动或分页。
- loading skeleton、错误重试、空结果建议。
- 导出 JSON、Markdown、CSV。

## 5. 评测开发线

### P0：指标与数据加载

- 梳理 `benchmark/`、`datasets/LitSearch/` 可用数据。
- 定义统一 `EvaluationExample` 和 `GroundTruthPaper`。
- 实现 Precision、Recall、F1、Recall@K。
- 定义论文匹配策略：
  - DOI 精确匹配
  - arXiv ID 精确匹配
  - Semantic Scholar ID 精确匹配
  - OpenAlex ID 精确匹配
  - 标题归一化模糊匹配作为 fallback

### P1：公开集小样本评测

- 支持 LitSearch 小样本 batch run。
- 输出每条 query 的结果与指标。
- 汇总平均 F1、Recall@5、Recall@10、Recall@20、延迟、调用次数。

### P2：消融实验

- Baseline：关键词检索。
- 多源检索。
- 多源检索 + 去重。
- 多源检索 + Judgement。
- 多源检索 + Judgement + Reranker。
- 完整系统。
- 去除 Query Understanding。
- 去除 Query Evolution。
- 去除 RefChain。
- 去除缓存。

### P3：效率实验

- 记录平均 API 调用次数。
- 记录平均 LLM 调用次数。
- 记录平均 Token 消耗。
- 记录平均端到端耗时。
- 记录平均候选论文数。
- 记录平均最终返回论文数。
- 分析缓存命中对延迟和成本的影响。

### P4：报告材料

- 生成机器可读 JSON 报告。
- 生成 Markdown 实验摘要。
- 输出图表数据供前端展示。
- 保留实验配置、随机种子、模型版本和检索源版本。

## 6. 文档开发线

### P0：设计文档

- 维护 `docs/design/architecture.md`。
- 维护 `docs/design/api_contract_draft.md`。
- 维护 `docs/design/frontend_ux_brief.md`。
- 维护 `docs/design/development_plan.md`。

### P1：运行文档

- 更新 README：
  - 安装依赖
  - 环境变量
  - 后端启动
  - 前端启动
  - CLI demo
  - API demo
  - 评测 demo

### P2：工程文档

- API Key 配置说明。
- 外部 API timeout 与错误处理说明。
- 缓存策略说明。
- 日志与成本统计字段说明。
- 无 Key fallback 说明。

### P3：比赛材料

- 系统方案文档。
- 实验报告。
- 消融实验结果。
- 可解释性样例。
- Demo 脚本。
- 最终提交材料对照表。

## 7. 跨线依赖

| 依赖 | 说明 |
|---|---|
| 前端依赖 API contract | 前端不得绕过后端访问外部服务 |
| 评测依赖 SearchService | 评测必须复用正式 pipeline |
| 成本统计依赖后端 trace | 前端只展示后端计算结果 |
| 文档依赖真实实现 | 文档不得声明未实现能力 |
| UI 依赖后端事件流 | 过程展示需要稳定 SSE event schema |

## 8. 每个开发任务完成标准

- 代码可以运行。
- 有最小测试。
- `pytest -q` 通过。
- `ruff check src tests` 通过或说明暂未启用。
- 没有硬编码密钥。
- 外部调用有 timeout、异常处理和日志。
- 最终结果保留来源信息。
- 架构变化同步更新 `docs/design/architecture.md`。
- API 变化同步更新 `docs/design/api_contract_draft.md`。

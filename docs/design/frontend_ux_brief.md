# 前端 UX Brief

## 1. 产品定位

前端定位为“学术论文搜索 Agent 工作台”，面向比赛演示、调试评测和研究人员试用。它不是营销落地页，也不是普通搜索框页面，而是一个能解释检索过程、展示证据和证明成本可控的专业工具界面。

核心体验目标：

1. 让用户清楚看到系统如何理解复杂学术查询。
2. 让用户实时看到检索、扩展、判断、重排序的过程。
3. 让用户能检查每篇论文为什么相关。
4. 让评委能快速看到 F1、效率、结构化输出三类评分能力。
5. 全流程不暴露 API Key，所有外部调用都由后端完成。

## 2. 设计原则

依据 UI/UX Pro Max 的工作台与数据产品建议，采用克制、清晰、信息密度合理的研究工具风格：

- 内容优先：首屏直接进入检索工作台，不做大幅营销 hero。
- 高可读：论文标题、摘要、解释和指标要适合长时间阅读。
- 高可解释：所有阶段、成本、来源、判断理由可追踪。
- 低干扰：避免装饰性动画和复杂背景。
- 可复现：每次 run 的参数、模型、检索源、成本和结果都可回放。
- 可访问：键盘可达、焦点可见、颜色不是唯一信息表达方式。

## 3. 推荐视觉风格

- 风格：专业研究仪表盘，flat design，轻量边框，低阴影或无阴影。
- 主色：中性灰白为主，使用深色文字保证阅读对比。
- 强调色：蓝色用于主要操作与链接，绿色用于成功和缓存命中，琥珀色用于进行中或警告，红色用于错误。
- 字体：优先使用高可读 sans-serif；后续可评估 Atkinson Hyperlegible、Inter 或系统字体。
- 图标：使用 Lucide 或同类一致线性图标，不使用 emoji 作为结构性图标。
- 圆角：控制在 8px 或以下，符合工具型界面的紧凑感。
- 暗色模式：可作为增强项，但 MVP 优先保证浅色模式对比度和展示稳定性。

避免：

- 营销式大 hero。
- 单一紫蓝渐变或大面积暗蓝背景。
- 装饰性光斑、玻璃拟态堆叠。
- 卡片嵌套卡片。
- 只有图没有表的结果展示。

## 4. 信息架构

建议顶级导航：

1. Search Workbench：检索工作台。
2. Runs：历史运行与详情。
3. Evaluation：评测与消融实验。
4. Cost & Logs：成本、延迟、缓存和日志。
5. Settings：安全配置状态，只显示后端返回的可用状态，不显示密钥值。

桌面端使用左侧窄导航或顶部导航。移动端使用简化顶部导航加抽屉，不要同时使用同层级的 sidebar、tabs 和 bottom nav。

## 5. 页面规划

### 5.1 Search Workbench

目标：输入复杂查询并启动检索。

关键组件：

- 查询输入框：支持中英文长查询。
- 示例查询按钮：用于 Demo 快速填充。
- 高级约束面板：
  - 时间范围
  - venue
  - 数据集
  - 方法关键词
  - 排除词
  - source preferences
  - top-k
  - 最大检索轮数
  - 最大候选数
  - Token/API/延迟预算
- 运行模式：
  - fast
  - balanced
  - high_recall
  - evaluation
- 提交按钮：创建后端 search run。

交互要求：

- 高级选项默认折叠，保留常用配置。
- 提交后按钮进入 loading/disabled 状态。
- 校验错误显示在字段附近。
- 若后端不可用，提供明确 retry。

### 5.2 Run Detail

目标：展示 Agent 检索全过程。

关键组件：

- Run header：
  - run id
  - status
  - elapsed time
  - current stage
  - stop/cancel
- 阶段时间线：
  - query_understanding
  - retrieval
  - deduplication
  - judgement
  - query_evolution
  - refchain
  - reranking
  - synthesis
- 实时事件流：
  - 子查询生成
  - 各检索源返回数量
  - 去重前后数量
  - LLM 调用摘要
  - 缓存命中
  - 成本变化
  - 错误和降级
- 中间产物面板：
  - SearchPlan JSON
  - expanded queries
  - candidate summary
  - judgement distribution

交互要求：

- 通过 SSE 实时更新。
- 事件列表支持暂停自动滚动。
- 失败阶段显示错误码、原因和是否可重试。
- 用户可从运行中跳转到最终结果，但不丢失过程状态。

### 5.3 Results

目标：展示最终结构化结果。

关键组件：

- 结果摘要：
  - highly relevant count
  - partially relevant count
  - insufficient evidence count
  - cost summary
- 论文结果表：
  - rank
  - title
  - authors
  - year
  - venue
  - relevance score
  - category
  - matched constraints
  - source badges
  - identifiers
- 论文详情抽屉：
  - abstract
  - relevance reason
  - ranking reason
  - evidence snippets
  - DOI/arXiv/S2/OpenAlex/PubMed IDs
  - source links
- 视图切换：
  - table
  - method clusters
  - timeline
  - citation/refchain graph
  - JSON
  - Markdown

交互要求：

- 表格支持排序、筛选、搜索和列显隐。
- 长标题和摘要默认换行，不应破坏布局。
- 引用图必须提供邻接表 fallback。
- 导出按钮调用后端导出 API，不在前端重新计算结果。

### 5.4 Evaluation

目标：展示公开集评测和消融实验结果。

关键组件：

- 评测任务表：
  - dataset
  - sample size
  - status
  - started_at
  - finished_at
  - metrics
- 新建评测任务表单：
  - dataset
  - split
  - sample limit
  - top-k
  - run profile
  - ablation flags
- 指标总览：
  - Precision@K
  - Recall@K
  - F1@K
  - API call count
  - LLM call count
  - token usage
  - latency
  - cache hit count
- 消融对比：
  - bar chart 比较 F1/Recall/Precision
  - line chart 展示 latency/token 趋势
  - table 展示每个 query 的失败原因

交互要求：

- 评测运行状态通过 SSE 更新。
- 图表必须有表格数据替代。
- 指标要清楚标注 K 值、样本数和运行配置。

### 5.5 Cost & Logs

目标：证明系统有成本意识和可观测性。

关键组件：

- 成本总览 KPI：
  - search_api_call_count
  - llm_call_count
  - estimated_total_tokens
  - latency_seconds
  - cache_hit_count
- 阶段耗时图。
- API 调用分布图。
- Token 趋势图。
- 日志列表：
  - timestamp
  - run id
  - stage
  - level
  - message
  - error_code

交互要求：

- 支持按 run id、stage、level 过滤。
- 错误日志可跳转到对应 run detail。
- 不展示敏感 header、密钥或完整 prompt 中的秘密片段。

### 5.6 Settings

目标：展示安全配置状态。

关键组件：

- 后端健康状态。
- 检索源可用状态。
- LLM provider 显示名和模型名。
- 当前默认预算。
- Feature flags。

限制：

- 不显示 API Key。
- 不允许前端输入或保存 API Key。
- 若需要配置密钥，应提示通过后端环境变量配置。

## 6. 核心用户流程

### 6.1 普通检索流程

```text
输入复杂查询
  ↓
配置约束和预算
  ↓
创建 SearchRun
  ↓
进入 Run Detail
  ↓
SSE 展示阶段事件
  ↓
查看结果表和解释
  ↓
导出 JSON/Markdown
```

### 6.2 评测流程

```text
选择数据集和配置
  ↓
创建 EvaluationJob
  ↓
展示 batch 进度
  ↓
查看指标与消融对比
  ↓
导出评测报告
```

### 6.3 Demo 流程

```text
选择预置示例查询
  ↓
balanced 模式运行
  ↓
展示 Query Understanding
  ↓
展示多源检索与 RefChain
  ↓
展示排序解释和成本统计
  ↓
切换到 Evaluation 页面展示公开集指标
```

## 7. 数据可视化建议

| 数据 | 推荐图表 | 说明 |
|---|---|---|
| 阶段耗时 | 横向条形图 | 适合比较各阶段耗时 |
| 成本趋势 | 折线图 | 展示 Token、调用次数随时间变化 |
| 指标对比 | 条形图 | 比较不同 ablation 的 F1/Recall/Precision |
| 引用关系 | 网络图 + 邻接表 | 图只作为探索视图，表格是可访问 fallback |
| 方法分类 | 分组表或树 | 避免复杂饼图 |
| 时间线 | 年份分组列表或时间线 | 展示研究脉络 |
| 检索源贡献 | 堆叠条形图 | 展示 OpenAlex/arXiv/S2/PubMed 的候选贡献 |

## 8. 状态设计

每个主要页面至少覆盖：

- empty：没有运行记录或结果。
- loading：等待后端响应或 SSE 事件。
- partial：部分检索源失败但系统降级可用。
- success：运行完成。
- failed：运行失败，展示错误码与恢复建议。
- cancelled：用户取消。
- stale：结果来自缓存或历史运行。

## 9. 可访问性要求

- 所有按钮和图标按钮必须有可读 label 或 tooltip。
- 交互控件最小点击区域不小于 44px。
- 键盘 Tab 顺序与视觉顺序一致。
- 表格支持键盘浏览。
- 图表提供数据表 fallback。
- 颜色不能作为唯一状态表达，需要文字或图标辅助。
- loading 超过 300ms 时显示反馈。
- 支持 reduced-motion，实时图表允许暂停。

## 10. 前端与后端边界

前端可以：

- 发送用户查询与约束。
- 展示后端返回的过程事件、结果、日志和指标。
- 做本地 UI 层排序、过滤和折叠。
- 导航到 run、evaluation、paper 详情。

前端不可以：

- 读取 API Key。
- 调用外部论文 API。
- 调用 LLM。
- 自行计算官方评测指标作为权威结果。
- 绕过后端缓存和成本统计。

所有权威数据以后端返回为准。

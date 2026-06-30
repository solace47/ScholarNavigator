# AGENTS.md

## 项目目标

本项目用于参加“中国研究生人工智能创新大赛”华为企业赛题三：
“科研场景下复杂学术查询的智能论文搜索与推荐”。

系统需要完成以下任务：

1. 理解用户自然语言学术查询。
2. 解析研究主题、方法、数据集、时间范围、发表 venue 等约束。
3. 自动生成多个检索子查询。
4. 调用学术搜索 API 检索候选论文。
5. 支持引用网络扩展和查询演化。
6. 对候选论文进行相关性判断和重排序。
7. 输出结构化论文列表、相关性解释和证据归纳。
8. 控制 API 调用次数、Token 成本和端到端延迟。

## 评分关注点

赛题三的评分重点：

1. F1 Score，占 70%。
2. 运行效率，占 20%，包括 API 调用次数、Token 消耗量和端到端延时。
3. 回复结果结构化，占 10%。

因此所有功能都要服务于：
- 提高 Precision 和 Recall 的平衡。
- 减少无效搜索和无效 LLM 调用。
- 输出清晰、可解释、可复现的结构化结果。

## 技术路线

主参考框架：SPAR。

重点参考 SPAR 的以下模块：
- Query Understanding Agent
- Retrieval Agent
- Judgement Agent
- Query Evolver Agent
- Reranker Agent
- RefChain 单层引用扩展

参考 PaSa 的以下思想：
- Crawler / Selector 架构
- paper queue
- Search / Expand / Stop 动作
- 高召回检索 + 高精度筛选

参考 PaperQA2 的以下思想：
- Gather Evidence
- contextual summarization
- citation traversal
- 带引用的证据归纳
- 证据不足时明确输出 insufficient evidence

不要在 MVP 阶段实现 PaSa 风格的 SFT/PPO/RL 训练。

## 代码要求

1. Python 版本使用 3.11 或以上。
2. 所有核心数据结构使用 Pydantic。
3. 所有模块必须可测试。
4. API Key 从环境变量读取，禁止硬编码。
5. 所有外部 API 调用必须有 timeout、异常处理和日志。
6. 所有最终结果必须保留来源信息。
7. 每篇论文尽量保留 DOI、arXiv ID、Semantic Scholar ID、OpenAlex ID、PubMed ID。
8. 不要修改 third_party 目录中的第三方项目源码，除非用户明确要求。
9. 新增功能必须同步添加或更新测试。
10. 架构变化必须更新 docs/design/architecture.md。

## 常用命令

安装依赖：

pip install -r requirements.txt

运行测试：

pytest -q

运行命令行 Demo：

python -m src.scholar_agent.app.cli "请帮我搜索关于 LLM reranking 的代表性论文" --top-k 20

运行代码检查：

ruff check src tests

## 完成标准

每个开发任务完成时必须做到：

1. 代码可以运行。
2. 有最小测试。
3. pytest 通过。
4. 没有硬编码密钥。
5. 有清晰日志。
6. 文档同步更新。

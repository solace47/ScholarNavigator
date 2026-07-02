# SPAR 前后端分离架构设计

## 1. 目标与边界

本项目面向“中国研究生人工智能创新大赛”华为企业赛题三：科研场景下复杂学术查询的智能论文搜索与推荐。系统设计必须优先服务自动评分中的三个目标：

1. 提高 F1 Score：在 Precision 与 Recall 之间取得平衡。
2. 控制运行效率：记录并优化 API 调用次数、Token 消耗和端到端延迟。
3. 保证结构化输出：返回可解析、可解释、可复现的论文检索结果。

本架构调整为前后端分离系统。后端承担所有 Agent、外部 API、LLM、论文检索、评测、缓存、日志和成本统计逻辑；前端只负责交互、过程展示、结果可视化和演示体验。前端不得直接读取任何 API Key。

## 2. 当前目录现状

当前项目根目录已存在以下主要结构：

```text
.
├── AGENTS.md
├── README.md
├── README_ZH.md
├── api_web.py
├── base_class.py
├── benchmark/
├── data/
│   ├── cache/
│   ├── processed/
│   └── raw/
├── datasets/
│   └── LitSearch/
├── demo_app_with_front.py
├── docs/
│   ├── contest/
│   ├── design/
│   ├── notes/
│   └── reference_papers/
├── experiments/
│   ├── logs/
│   ├── outputs/
│   └── runs/
├── figs/
├── prompts/
├── reports/
├── src/
│   └── scholar_agent/
│       ├── agents/
│       ├── app/
│       ├── connectors/
│       ├── core/
│       └── evaluation/
├── tests/
└── third_party/
```

观察结果：

- `src/scholar_agent/` 目前是后端包的合理落点，但当前只看到目录骨架，尚未看到包内业务文件。
- 根目录保留了 SPAR 原始脚本，例如 `pipeline_spar.py`、`search_engine.py`、`rerank.py`、`api_web.py`、`demo_app_with_front.py` 等，后续应逐步迁移或包裹到 `src/scholar_agent/`，但本次不移动文件。
- `docs/contest/` 已包含赛题需求与评分规则。
- `docs/reference_papers/` 已包含 SPAR、PaSa、PaperQA2、LitSearch、AstaBench 等参考 PDF。
- `third_party/` 已包含参考项目。根据项目要求，不应修改其中源码，除非用户明确要求。
- 尚未看到独立 `frontend/` 目录。若采用前后端分离，应后续新增，而不是让前端混在根目录脚本中。

## 3. 建议目标目录

本次只提出调整方案，不直接移动文件。推荐后续逐步演进为：

```text
.
├── backend/                         # 可选：只在需要独立后端工程时新增
├── frontend/                        # 前端工程，后续由 UI/UX 设计与实现阶段创建
│   ├── package.json
│   ├── src/
│   └── tests/
├── src/
│   └── scholar_agent/               # Python 后端核心包，推荐继续作为主后端代码位置
│       ├── agents/
│       ├── app/
│       │   ├── api/                 # FastAPI 路由、SSE/WebSocket、依赖注入
│       │   └── cli/                 # 命令行入口
│       ├── connectors/              # OpenAlex、arXiv、Semantic Scholar、PubMed 等
│       ├── core/                    # Pydantic 模型、配置、缓存、日志、成本统计
│       ├── evaluation/              # 数据集加载、指标计算、评测任务
│       └── services/                # SearchService、EvaluationService、ExportService
├── tests/
│   ├── backend/
│   ├── evaluation/
│   └── contract/
└── docs/
    ├── contest/
    ├── design/
    └── reference_papers/
```

推荐优先保留 `src/scholar_agent` 作为后端核心包，新增 `frontend/` 作为独立前端工程。只有当部署或依赖隔离确实需要时，再考虑新增顶层 `backend/` 包装目录。

## 4. 总体架构

```text
Browser Frontend
  │
  │ REST: create run, read results, export, evaluate
  │ SSE/WebSocket: stream run progress and intermediate artifacts
  ▼
Backend API Layer
  │
  ├── Search Run API
  ├── Evaluation API
  ├── Cache/Cost/Log API
  └── Safe Runtime Config API
  │
  ▼
Application Services
  │
  ├── SearchService
  ├── EvaluationService
  ├── CostTracker
  ├── CacheManager
  └── TraceLogger
  │
  ▼
Agent Pipeline
  │
  ├── QueryUnderstandingAgent
  ├── RetrieverAgent
  ├── JudgementAgent
  ├── QueryEvolverAgent
  ├── RefChainExpansion
  ├── RerankerAgent
  └── SynthesizerAgent
  │
  ▼
Connectors and Infrastructure
  │
  ├── LLM Provider Client
  ├── OpenAlex / arXiv / Semantic Scholar / PubMed
  ├── Cache Store
  ├── Log Store
  └── Experiment Output Store
```

## 5. 后端职责

后端是系统可信执行边界，负责：

- 查询理解：解析研究主题、方法、数据集、时间范围、venue、作者机构、论文类型等约束。
- 查询分解与演化：生成子查询、follow-up query，并根据中间结果控制下一轮检索。
- 多源论文检索：通过 OpenAlex、arXiv、Semantic Scholar、PubMed 等连接器获取候选论文。
- 引用扩展：实现单层 RefChain，优先限制深度与候选数。
- 去重与实体归一：尽量保留 DOI、arXiv ID、Semantic Scholar ID、OpenAlex ID、PubMed ID。
- 相关性判断：低成本规则初筛与必要的 LLM 精判结合。
- 重排序：综合相关性、时效性、权威性、多样性、来源置信度和约束匹配。
- 结果归纳：输出 JSON 与 Markdown，包含高度相关、部分相关、方法分类、时间线、引文关系、证据不足说明。
- 评测：在 LitSearch、SPARBench 或内部 benchmark 上计算 Precision、Recall、F1、Recall@K 等。
- 成本统计：记录 API 调用次数、LLM 调用次数、Token、缓存命中、延迟、检索轮数。
- 缓存：缓存搜索 API 返回、论文详情、query understanding、judgement、reranking 和最终结果。
- 日志：记录 request id、run id、阶段事件、错误、外部调用耗时。
- 密钥管理：所有 API Key 只从后端环境变量读取，禁止进入前端构建产物或浏览器运行时。

所有核心数据结构应使用 Pydantic 定义，所有外部调用必须有 timeout、异常处理和日志。

## 6. 前端职责

前端是演示与操作界面，负责：

- 提供复杂学术查询输入、多条件约束配置和检索预算配置。
- 调用后端 REST API 创建检索任务。
- 通过 SSE 或 WebSocket 展示检索过程事件。
- 展示查询理解、子查询、检索源、候选数量、Judgement、Reranking、RefChain 和 Query Evolution 过程。
- 展示最终论文列表、相关性解释、证据来源、方法分类、时间线和引用关系图。
- 展示成本与效率统计，包括 API 调用数、Token、延迟、缓存命中、每阶段耗时。
- 提供 JSON、Markdown、CSV 等导出入口。
- 展示评测任务进度和指标图表。

前端不负责：

- 不直接访问外部学术 API。
- 不直接调用 LLM。
- 不读取或保存 API Key。
- 不实现评测指标计算的权威逻辑。
- 不绕过后端缓存与成本统计。

## 7. 推荐后端 Pipeline

```text
SearchRequest
  ↓
QueryUnderstandingAgent
  - intent_type
  - domain
  - constraints
  - expanded_queries
  ↓
SearchPlan
  ↓
RetrieverAgent
  - multi-source search
  - timeout/retry/fallback
  ↓
CandidatePaperSet
  ↓
Deduplication + Normalization
  ↓
Rule-based PreFilter
  ↓
JudgementAgent
  - batch judgement where possible
  - LLM only for selected Top-N
  ↓
QueryEvolverAgent
  - optional next-round queries
  - early stop by budget and marginal gain
  ↓
RefChainExpansion
  - depth 1 by default
  ↓
RerankerAgent
  ↓
SynthesizerAgent
  ↓
SearchResult
```

默认停止条件：

- 达到最大检索轮数。
- 高相关论文数量达到目标。
- API 调用次数达到预算。
- Token 消耗达到预算。
- 端到端耗时达到预算。
- 新增高相关论文数量低于阈值。
- 所有可用检索源失败或返回空结果。

## 8. 数据与状态

推荐将一次检索抽象为 `SearchRun`：

- `run_id`：检索任务 ID。
- `status`：`queued | running | succeeded | failed | cancelled`。
- `request`：用户查询、约束、预算和输出偏好。
- `trace`：各阶段事件、外部调用、成本、缓存命中和错误。
- `intermediate`：查询理解、子查询、候选论文、判断结果、扩展结果。
- `result`：最终结构化结果。

推荐将缓存按以下维度分层：

- `search_response_cache`：检索源、查询词、参数、时间窗口。
- `paper_detail_cache`：标准化论文 ID。
- `llm_cache`：prompt hash、model、参数、输入版本。
- `judgement_cache`：query hash、paper id、judge policy version。
- `rerank_cache`：query hash、candidate set hash、ranker version。

## 9. API 通信方式

推荐组合：

- REST API：创建任务、读取任务状态、读取结果、导出、发起评测、读取缓存和成本统计。
- SSE：优先用于单向实时过程展示，例如检索阶段、候选数量、成本变化、错误告警。
- WebSocket：仅当后续需要双向交互式控制时使用，例如运行中调整预算、手动接受或拒绝候选论文。

MVP 推荐先实现 REST + SSE，复杂度低，足够支撑演示。

## 10. 安全与配置

- API Key 只允许放在后端环境变量或后端部署平台 Secret 中。
- 前端只能请求后端提供的安全配置，例如可用检索源、模型显示名、预算上限、功能开关。
- 后端日志不得输出完整 API Key、Authorization header 或用户敏感输入中的密钥片段。
- 所有外部调用必须设置 timeout。
- 所有错误返回应包含稳定的 `error_code`、可读 `message` 和 `retryable` 标记。

## 11. 评测架构

评测应作为后端能力暴露：

```text
EvaluationRequest
  ↓
DatasetLoader
  ↓
SearchService batch run
  ↓
MetricCalculator
  - Precision@K
  - Recall@K
  - F1@K
  - API call count
  - Token usage
  - latency
  ↓
EvaluationReport
```

评测 API 与普通检索 API 复用同一 SearchService，确保线上演示与离线评测逻辑一致。

## 12. 部署建议

MVP 阶段：

- 后端：Python 3.11+，FastAPI 或等价 ASGI 框架。
- 前端：独立 `frontend/`，React/Next.js/Vite 均可，后续按 UI/UX 实现阶段确定。
- 本地运行：后端监听 `localhost:8000`，前端监听 `localhost:3000` 或 `localhost:5173`。
- 跨域：开发环境显式配置 CORS 白名单，不使用 `*` 作为生产默认配置。

比赛演示阶段：

- 后端、前端可分别部署。
- API Key 只配置在后端环境。
- 导出实验结果到 `experiments/outputs/` 或 `reports/`。

## 13. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 只重 UI、忽视检索质量 | F1 下降 | 前端只展示，核心质量投入后端检索和判断 |
| 无限扩展查询 | 效率分下降 | 后端强制预算、early stop、缓存 |
| LLM 判断过多 | 成本和延迟上升 | 规则初筛 + Top-N LLM 精判 + batch |
| 多源重复论文 | Precision 下降 | 标准化 ID 与标题作者年份相似度去重 |
| 前端泄露 Key | 安全风险 | 前端只访问后端，后端环境变量管理密钥 |
| 引文图过复杂 | 展示不可读 | 默认单层 RefChain，图和表双视图 |

## 14. 架构决策

1. 后端为唯一外部 API 与 LLM 调用方。
2. 前端通过 REST + SSE 与后端通信。
3. Python 后端核心继续建议放在 `src/scholar_agent/`。
4. 后续新增独立 `frontend/`，不要继续扩展根目录中的一体式 demo 前端。
5. MVP 不实现 PaSa 风格 SFT/PPO/RL 训练。
6. MVP 已实现 OpenAlex、arXiv、Semantic Scholar 和最小 PubMed 真实检索连接器；PubMed 可显式选择，但不加入 Recommended 默认源。
7. 评测能力必须复用正式检索 pipeline，避免评测与演示两套逻辑。

# ScholarNavigator 演示脚本

## 演示前准备

确认依赖已安装：

```bash
python3 -m pip install -r requirements.txt
cd frontend && npm install
```

可选环境变量：

```bash
export OPENALEX_MAILTO=your_email@example.com
```

说明：

- `OPENALEX_MAILTO` 仅用于 OpenAlex polite pool，不是 API Key。
- 前端不会读取、保存或展示任何 API Key。
- 当前演示的 Real Preview 会通过后端访问真实 OpenAlex / arXiv。
- 当前 MVP 不调用 LLM，不读取全文 PDF。

建议演示前先执行：

```bash
PYTHONPATH=src pytest -q
cd frontend && npm run lint
cd frontend && npm run build
```

## 后端启动命令

推荐演示时降低真实检索并发，减少 OpenAlex / arXiv 压力：

```bash
REAL_PREVIEW_MAX_WORKERS=1 \
PYTHONPATH=src uvicorn scholar_agent.app.main:app --host 127.0.0.1 --port 8000
```

如果只演示 Mock Demo，也可以使用：

```bash
PYTHONPATH=src uvicorn scholar_agent.app.main:app --reload --host 127.0.0.1 --port 8000
```

后端 OpenAPI 文档：

```text
http://127.0.0.1:8000/docs
```

## 前端启动命令

```bash
cd frontend
npm run dev
```

打开：

```text
http://localhost:3000
```

如需指定后端地址：

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

## Mock Demo 演示步骤

1. 打开 `http://localhost:3000`。
2. 确认页面顶部显示 `ScholarNavigator`。
3. 选择 `Mock Demo` 模式。
4. 使用默认中文示例 query，或输入：

   ```text
   请帮我找近三年关于 LLM reranking 在科研论文检索中的代表性方法和评测数据集
   ```

5. 点击启动搜索。
6. 讲解 Run Progress：
   - run_id 出现。
   - Mock SSE 事件逐步展示。
   - query_understanding、retrieval、judgement、reranking、synthesis 等阶段可视化。
7. 讲解 Results：
   - highly relevant / partially relevant 论文卡片。
   - 每张卡片包含 title、authors、year、venue、abstract、score、reason、evidence、source badges、identifier 和链接。
8. 指出 Mock Demo 的边界：
   - 这是稳定演示数据。
   - 不访问真实检索源。
   - 默认 `synthesis=null`，因此不会显示 Synthesis Panel。

## Real Preview 演示步骤

1. 切换到 `Real Preview` 模式。
2. 输入推荐 query：

   ```text
   latest LLM reranking methods for scientific literature retrieval
   ```

3. 设置参数：
   - `top_k=10`
   - `run_profile=balanced`
   - `current_year=2026`
   - `enable_query_evolution=true`
   - `enable_refchain=false`
4. 点击启动 Real Preview。
5. 讲解交互差异：
   - run_id 应以 `run_preview_` 开头。
   - Preview Transport 会显示 Real Preview 使用一次性 REST 响应，不开启 SSE。
   - 后端会真实调用 OpenAlex / arXiv，前端仍不接触任何密钥。
6. 如果返回论文：
   - 展示前 5 篇论文卡片。
   - 讲解 relevance_score、category、ranking_reason 和 evidence。
   - 指出 sources 中可能出现 `arxiv`、`openalex`。
7. 如果没有论文：
   - 展示“检索源失败/无候选”。
   - 展示 `missing_evidence` 中的 OpenAlex / arXiv 错误诊断。
   - 说明系统没有白屏，而是把失败原因结构化暴露。

## Synthesis Panel 演示步骤

Real Preview 返回 `synthesis` 时，在论文列表上方展示 Citation-backed Synthesis Panel。

建议讲解顺序：

1. status：
   - 例如 `succeeded` 或 insufficient-evidence 状态。
2. answer_summary：
   - 说明 summary 只基于当前 ranked papers 的 evidence rows。
3. key_findings：
   - 每条 finding 显示 citation keys，例如 `[R1]`、`[R2]`。
   - 每条 finding 必须有引用 key。
4. citation_coverage：
   - ranked_paper_count
   - cited_paper_count
   - evidence_row_count
   - coverage_ratio
   - source_error_count
5. limitations / warnings：
   - 外部 source error。
   - no full-text evidence。
   - metadata-only evidence。
6. evidence_table：
   - citation_key
   - paper_title
   - year
   - evidence_source
   - evidence_text

必须明确说明：

- 当前 synthesis 是规则版 metadata/evidence-row synthesis。
- citation-backed 表示结论绑定到当前候选论文证据行。
- 不代表系统已读取全文 PDF。
- 当前没有调用 LLM。

## OpenAlex 503 或 arXiv 超时的解释

如果演示中出现 OpenAlex 503：

- 这是 OpenAlex 上游服务临时不可用或压力较高。
- 系统已经做轻量 retry/backoff。
- 如果重试后仍失败，错误会进入 `source_stats`、`missing_evidence` 和 synthesis limitations。
- arXiv 如果可用，系统仍可继续输出 arXiv 候选。

如果演示中出现 arXiv 429 或 timeout：

- 这是公开检索 API 的限流或网络超时。
- 系统不会把单个 source 失败扩散成整体崩溃。
- 前端会展示“检索源失败/无候选”或保留可用 source 的结果。

如果 OpenAlex 和 arXiv 同时失败：

- 直接展示 `missing_evidence` 诊断。
- 切换回 Mock Demo 展示完整交互体验。
- 可补充展示既有验证记录：
  - `docs/design/frontend_real_preview_validation.md`
  - `docs/design/real_preview_stability_validation.md`
  - `docs/design/frontend_synthesis_validation.md`

## 推荐演示 Query

主推英文 query：

```text
latest LLM reranking methods for scientific literature retrieval
```

中文 query：

```text
请帮我找近三年关于 LLM reranking 在科研论文检索中的代表性方法和评测数据集
```

偏 benchmark query：

```text
recent benchmark datasets for evaluating retrieval augmented generation and LLM reranking
```

偏综述 query：

```text
survey of neural information retrieval methods for scientific literature search
```

## 讲解词草稿

开场：

> ScholarNavigator 面向华为企业赛题三，目标是把复杂自然语言学术查询转成一个可解释、可评测、可控成本的论文搜索流程。系统采用前后端分离架构，前端只负责交互和可视化，后端负责所有检索源调用、agent pipeline、评测和成本统计。

架构：

> 后端 pipeline 从 Query Understanding 开始，生成 SearchPlan 和多个 subqueries；Retriever 调用 OpenAlex 和 arXiv；之后进行去重、规则相关性判断、重排；可选开启 Query Evolution 和单层 RefChain；最终通过规则版 Synthesis 生成带 citation key 的证据归纳。

Mock Demo：

> Mock Demo 用稳定数据展示端到端交互，包括 run 创建、SSE 事件、阶段进度和论文结果卡片。这个模式适合比赛现场网络不稳定时兜底。

Real Preview：

> Real Preview 会调用后端 internal preview endpoint，真实访问 OpenAlex 和 arXiv。这里可以看到 run_id、真实候选论文、source badges、相关性原因和检索错误诊断。前端不保存任何密钥。

Synthesis：

> Synthesis Panel 是当前的规则版 citation-backed synthesis。它只使用候选论文 metadata 和 evidence rows，不读取全文 PDF，也不调用 LLM。每条 finding 都绑定到 R1、R2 这样的 citation key，限制和 source error 也会明确展示。

边界：

> 当前版本是 no-LLM MVP，重点是先把可测试、可复现、可观测的检索 pipeline 搭起来。后续可以在保留无 Key fallback 的前提下，逐步接入 LLM 增强 query understanding、judgement、reranking 和 synthesis。

## 预期展示亮点

- 前后端分离，前端不接触 API Key。
- Mock Demo 与 Real Preview 双模式，兼顾稳定演示和真实检索。
- OpenAlex / arXiv 真实检索源接入，且错误可观测。
- 规则版 Query Understanding、Judgement、Reranker、Query Evolution、RefChain、Synthesis 形成完整 pipeline。
- Citation-backed Synthesis Panel 明确显示引用 key、证据表和 limitations。
- no-LLM MVP 下 Token 成本为 0，便于展示效率与成本控制。
- 离线评测链路已具备 baseline / query_evolution / refchain 对比雏形。

## 演示失败兜底方案

1. 后端未启动：
   - 前端会提示“后端服务不可用，请先启动 FastAPI Mock API”。
   - 按后端启动命令重新启动。
2. 前端端口冲突：
   - 使用 Next.js 输出的新端口。
   - 或停止占用 3000 的进程后重启。
3. Real Preview 网络失败：
   - 展示 `missing_evidence`。
   - 解释 OpenAlex / arXiv 是真实外部依赖。
   - 切换 Mock Demo 完成完整交互展示。
4. 没有 synthesis：
   - Mock Demo 默认隐藏 synthesis，这是预期行为。
   - Real Preview 若无候选，Synthesis 可能进入 insufficient-evidence 或只展示 limitations。
5. 构建或测试需要证明：
   - 展示已有验证记录和本轮命令输出。

## 演示结束

停止后端和前端：

```text
Ctrl-C
```

可补充展示的文档：

- `docs/final/project_delivery_summary.md`
- `docs/design/frontend_synthesis_validation.md`
- `docs/design/real_preview_stability_validation.md`
- `docs/design/evaluation_sample_run.md`

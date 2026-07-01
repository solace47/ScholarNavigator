# 最终提交 readiness 检查记录

执行时间：2026-07-01 20:48:30 CST

本轮只做最终提交前仓库卫生检查与记录。未修改 backend 功能代码、frontend 功能代码或 `third_party`。未调用 LLM，未访问外网。

## 测试与构建

后端测试：

```bash
PYTHONPATH=src pytest -q
```

结果：

```text
190 passed, 1 warning in 1.65s
```

warning 为既有 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`。

前端 lint：

```bash
cd frontend && npm run lint
```

结果：通过。

前端 build：

```bash
cd frontend && npm run build
```

结果：通过。Next.js 16.2.9 Turbopack production build 成功。

构建后检查 `git status --short`，未发现 `frontend/next-env.d.ts` 或其它构建相关文件的非预期变更，因此无需恢复 build 产物。

## Git 状态

运行测试和构建后、写入本 readiness 文档前：

```bash
git status --short
```

结果为空，说明工作区干净。

写入本 readiness 文档后，预期最终状态只包含：

```text
?? docs/final/final_submission_readiness.md
```

## Ignored Artifacts 摘要

执行：

```bash
git status --short --ignored
```

发现的 ignored artifacts 包括：

- `.DS_Store`
- `.pytest_cache/`
- `frontend/.next/`
- `frontend/node_modules/`
- `scripts/__pycache__/`
- `src/**/__pycache__/`
- `tests/__pycache__/`
- 若干目录下的 `.DS_Store`
- `spar/`

这些均为 ignored 状态，没有进入 tracked 或 untracked 待提交状态。

## Third Party 状态

执行：

```bash
git -C third_party/paper-qa status --short
```

结果为空。`third_party/paper-qa` 当前为干净状态。本轮未修改 `third_party`。

## 关键入口文件存在性检查

以下文件均存在：

- `README.md`
- `docs/final/project_delivery_summary.md`
- `docs/final/contest_technical_report_draft.md`
- `docs/final/demo_script.md`
- `docs/final/reviewer_quickstart.md`
- `docs/final/submission_manifest.md`
- `docs/design/final_engineering_acceptance.md`
- `scripts/run_search_batch.py`
- `scripts/summarize_search_batch.py`
- `scripts/evaluate_search_batch.py`

## 不应提交内容检查

检查方式：

```bash
git ls-files -o --exclude-standard
git ls-files | rg '(^|/)(node_modules|\.next|__pycache__|\.pytest_cache|\.DS_Store|\.env)$|(^|/)\.env$'
```

结果：

- 非 ignored untracked 文件：无。
- tracked disallowed artifact paths：无。
- `node_modules`：仅 ignored，未进入待提交状态。
- `frontend/.next`：仅 ignored，未进入待提交状态。
- `__pycache__`：仅 ignored，未进入待提交状态。
- `.pytest_cache`：仅 ignored，未进入待提交状态。
- `.DS_Store`：仅 ignored，未进入待提交状态。
- `.env`：未发现 tracked 或 untracked 待提交项。
- secrets / API keys：未发现文件名层面的待提交项。

## 最终结论

ready。

理由：

- 测试、lint、build 均通过。
- build 未产生待提交代码或构建产物变更。
- root 工作区在写入本记录前干净。
- `third_party/paper-qa` 干净。
- 关键入口文档和脚本均存在。
- 不应提交的缓存、构建产物、系统文件和 `.env` 未进入 tracked/untracked 待提交状态。

## 已知问题

1. pytest 存在既有 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`，不影响当前测试通过。
2. Real Search 依赖真实 OpenAlex / arXiv，外部服务可能出现 `503`、`429` 或 timeout；系统会把这些情况降级为 `missing_evidence`、source stats 和 SSE diagnostics。
3. 当前仍是 no-LLM、metadata/evidence-row MVP，不调用 LLM，不读取全文 PDF。
4. 当前未完整接入 LitSearch / AstaBench benchmark，已有的是本地 fake fixture 与 CLI 评测链路。

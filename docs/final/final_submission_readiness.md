# 最终提交 readiness 检查记录

执行时间：2026-07-01 21:19:37 CST

本文档已从旧 hybrid runtime readiness 更新为 Real Search only 重构后的自动检查记录。旧 `docs/design/final_engineering_acceptance.md` 仍可作为历史验收参考，但不再代表当前产品路径状态。

本轮修改了 backend / frontend 产品路径以删除示例检索接口和前端示例模式。未修改 `third_party`。未调用 LLM，未访问外网。

## 测试与构建

后端测试：

```bash
PYTHONPATH=src pytest -q
```

结果：

```text
189 passed, 1 warning in 1.75s
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

构建后检查 `git status --short`，未发现 `frontend/next-env.d.ts` 或其它构建相关文件的非预期变更。

## Real Search only 检查

- 产品路径只保留 `/api/v1/real/search/runs` lifecycle。
- 旧 `/api/v1/search/runs` 产品路径已删除，测试覆盖 404/405。
- OpenAPI 不再包含旧 `/api/v1/search/runs` paths。
- runtime config 预期为 `mode=real_search`。
- connectors 中不再包含产品级示例 connector。
- OpenAlex / arXiv `available=true`。
- Semantic Scholar / PubMed 仍 `not_implemented`。
- `llm.available=false`，下一阶段再接真实 LLM provider。
- 前端只保留 Real Search 入口，取消按钮、SSE events、Results、Synthesis Panel、Citation Graph Panel、JSON/Markdown 导出保留。

## 指定 rg 检查

执行用户要求的产品路径示例检索残留关键词检查，范围为 `src`、`frontend`、`docs/final` 和 `README.md`。

结果：无匹配。

说明：测试目录中保留旧 endpoint 不可用的断言，以及 fake SearchService / fake retriever / fixture 数据。这些只用于隔离测试，不属于产品路径 fallback。

## Git 状态

本轮最终应只包含 Real Search only 重构相关代码、测试和文档变更，不应包含 `third_party` 变更、构建产物或缓存目录。

## 不应提交内容检查

需要继续确保以下内容没有进入 tracked/untracked 待提交状态：

- `node_modules`
- `.next`
- `__pycache__`
- `.pytest_cache`
- `.DS_Store`
- secrets / API keys
- 临时 curl 输出
- 临时 batch 输出
- 本地虚拟环境目录

## 最终结论

ready for automated checks。

尚未在本记录中重新执行真实 OpenAlex / arXiv 手动端到端验收。外部服务可能出现 `503`、`429` 或 timeout；当前系统应以 diagnostics 展示失败，而不是返回示例数据。

## 已知问题

1. pytest 存在既有 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`，不影响当前测试通过。
2. Real Search 依赖真实 OpenAlex / arXiv，外部服务可能出现 `503`、`429` 或 timeout。
3. 当前仍是 no-LLM、metadata/evidence-row MVP，不调用 LLM，不读取全文 PDF。
4. 当前未完整接入 LitSearch / AstaBench benchmark，已有的是本地 fake fixture 与 CLI 评测链路。
5. Real Search 使用 in-memory run store，不是生产级持久化队列。

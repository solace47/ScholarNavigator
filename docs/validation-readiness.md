# 正式验证就绪证据包

`validation_readiness_bundle_v1` 是只读的证据发布与声明追溯门禁。它把仓库已经跟踪的协议、
聚合审计结果和阻断记录绑定为一个确定性目录，但不运行 Benchmark、Replay、LLM、检索 API
或 evaluator，也不生成新的质量指标。

## 契约边界

契约固定以下内容：

- 实现所基于的 Git commit，以及生成器、CLI、契约和文档的代码树摘要；
- 数据、Snapshot、冻结 Record 的已有摘要，不复制原始 query、论文正文或私有映射；
- 每份机器证据的仓库相对路径、大小、SHA-256、协议版本和依赖；
- README、架构、评测和比赛要求中的声明状态与机器证据引用；
- `current_rules` 默认状态、实验 tie-break 默认关闭状态和关键门禁退出码；
- Full1000、人工 Precision、官方 scorer/schema 三项不可替代的正式阻断。

包还登记 `completion_bias_audit_v1` 的只读证据。该证据精确闭合 Full1000、Record162 和
Record160，并将 Record160 的覆盖、来源、排序、约束与交付声明限制在冻结 160 条总体；它不
解除 Full1000 阻断，也不推断其余查询的检索表现。

`external_scorer_handoff_v1` 作为独立工程声明登记：严格合成 package 已验证 canonical
handoff、隔离子进程、输入不可变和双次确定性，但真实 readiness 仍以退出码 3 保持 blocked。
该声明不提供官方 Schema、指标或成绩，也不解除 Full1000 与官方 scorer 两项阻断。

`human_annotation_delivery_v1` 也仅登记为工程链路就绪：两套 471 项盲化包、operator-only
恢复映射和合成回收/裁决演练已经离线验证，但真实标注数仍为 0，统计保持 `null`。因此
`human_precision_missing` 阻断和 `formal_validation_complete=false` 均保持不变。

声明状态只有 `verified`、`internal_only`、`blocked` 和 `not_applicable`。`verified` 仅用于
工程能力，`internal_only` 仅用于内部冻结验证或诊断；正式验证要求在缺失外部输入时必须是
`blocked`。覆盖、稳定性、来源漏斗、LLM proxy 或交付保真都不能代替这些阻断。

## 生成与一键验证

```bash
PYTHONPATH=src python scripts/check_validation_readiness.py generate \
  --contract benchmark/validation_readiness_bundle_v1_contract.json \
  --bundle benchmark/validation_readiness_bundle_v1_release
```

```bash
PYTHONPATH=src python scripts/check_validation_readiness.py verify \
  --contract benchmark/validation_readiness_bundle_v1_contract.json \
  --bundle benchmark/validation_readiness_bundle_v1_release
```

`verify` 只调用登记过的既有 `verify/check` 入口，校验历史哈希、协议依赖、跨证据计数、声明
边界、默认关闭项和既存嵌套工作树状态。它不调用任何会补采、付费、联网或写 Snapshot 的
`run/generate` 路径。

退出码：

- `0`：`ready_with_declared_blockers`，包完整且阻断已明确保留；
- `2`：证据哈希、字段、声明、默认状态或发布包内容违规；
- `3`：必需证据缺失，无法建立就绪包；
- `4`：命令使用错误。

## 与其他证明的区别

本门禁只证明“声明能否追溯到未篡改的内部证据，以及限制是否完整披露”。它不证明检索
相关性，不是人工 Precision，不是官方 scorer，也不生成官方提交。历史证据由原门禁继续
负责，证据包只登记其原始哈希，不重写历史文件或结论。

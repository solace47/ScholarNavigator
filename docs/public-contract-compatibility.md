# 公共评测接口兼容性门禁

`public_contract_compatibility_v1` 冻结当前可复核的 FastAPI OpenAPI、前端消费类型、关键离线
CLI 和机器产物契约。它只治理工程兼容性，不定义官方 Schema、质量指标或官方成绩。

## 覆盖与归一化

- OpenAPI 路径、方法、参数、响应 Schema 和状态码；描述、示例等非语义展示字段不进入摘要。
- `frontend/src/types/api.ts` 的导出类型、字段可空性，以及与同名 OpenAPI 模型的顶层字段闭合。
- run provenance、readiness、formal clearance、人工标注交付和外部 scorer handoff CLI 的命令、参数、默认值与固定退出码。
- `run_manifest_v1` 及 readiness、clearance、人工标注和 scorer handoff 机器 JSON 的版本与顶层字段类型。

基线不复制 query、论文正文、私有映射或凭据，也排除绝对路径、时间戳和描述文本顺序。

## 兼容性语义

- 删除或重命名字段、类型/可空性变化、枚举收窄、退出码变化、顺序语义变化均为 `breaking`。
- 对严格消费者新增字段默认也是 `breaking`。
- 只有调用方显式声明允许未知可选字段时，新增字段才是 `additive_review_required`；它仍不能自动通过当前基线。
- 同一协议版本的 breaking 漂移直接失败。版本升级必须保留旧版读取验证或提供显式迁移器，不能只刷新基线。

## 使用

```bash
PYTHONPATH=src python scripts/check_public_contract_compatibility.py snapshot
PYTHONPATH=src python scripts/check_public_contract_compatibility.py verify-current
PYTHONPATH=src python scripts/check_public_contract_compatibility.py compare \
  --from old.json --to new.json
PYTHONPATH=src python scripts/check_public_contract_compatibility.py audit-readiness
```

退出码固定为：`0=contracts_compatible`、`2=breaking_or_versioning_violation`、
`3=not_ready_missing_contract_baseline`、`4=usage_error`。输出始终为规范 JSON；门禁不联网、不调用
LLM、不写 Snapshot，也不读取 gold/qrels 或计算质量指标。

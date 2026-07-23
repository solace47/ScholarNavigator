# Full1000 启动控制

`full1000_launch_control_v1` 是未来正式联网执行前的离线启动封印，不执行检索、不读取
凭据，也不代表 Full1000 已完成。协议绑定冻结执行计划、1000 条 opaque query 的身份与顺序、
20 个 shard、两级 attempt、`current_rules` 配置、来源响应取证 addendum 及权威输出根。

## 两阶段授权

`prepare` 只在权威运行目录不存在或为空、旧 Record160/162 未被当作 checkpoint、计划与
freshness 均闭合时生成自哈希 preparation。`authorize-dry-run` 再生成绑定 preparation、
代码身份、plan/config/query 摘要、允许 shard/attempt 和唯一命令模板的自哈希 authorization。
二者都不使用私钥，也不允许命令行凭据。授权状态依次为
`prepared → authorized → started`；任一阶段可撤销为 `revoked`，畸形输入为 `invalid`。

没有 preparation、authorization 和连续操作审计链的直接 runner 输出不是权威 Full1000
结果。修改计划或配置后复用凭证、跨代码身份、重复启动、旧 attempt、未授权 resume、
部分 shard aggregate 及撤销后继续执行均 fail-closed。

## 操作审计

启动后的授权、启动、暂停、恢复、取消、shard 失败、attempt 替代、shard 完成与 aggregate
请求按稳定序号和前序 SHA-256 形成 append-only 链。记录只含 opaque shard/attempt 身份和
详情摘要，不含环境值、绝对路径、URL、请求头或凭据。只有 20 个 shard 均以唯一最终
attempt 完成后才允许请求 aggregate。

## 离线命令

```bash
PYTHONPATH=src python scripts/check_full1000_launch_control.py simulate-operations
PYTHONPATH=src python scripts/check_full1000_launch_control.py audit-readiness
PYTHONPATH=src python scripts/check_full1000_launch_control.py prepare \
  --state-dir <empty-control-directory>
PYTHONPATH=src python scripts/check_full1000_launch_control.py authorize-dry-run \
  --state-dir <control-directory>
PYTHONPATH=src python scripts/check_full1000_launch_control.py verify-authorization \
  --state-dir <control-directory>
```

退出码为 `0=launch_controls_ready`、
`2=authorization_or_operation_violation`、`3=external_activation_blocked`、
`4=usage_error`。当前真实审计固定返回 3：控制链可复核，但网络、真实凭据和正式运行均未
激活；Full1000、真实人工 Precision、官方 scorer/schema 三项正式阻断不变。

未来真实 authorization 还必须绑定
[`formal_run_storage_governance_v1`](formal-run-storage-governance.md) addendum。它要求主运行
目录与异地备份目标的字节、inode 和文件系统配额均在启动前通过，且原始响应取证与
reserve→commit→release 存储账本显式启用。现有 v1 启动协议和历史授权不会被改写，也不能
越过该 addendum 复用于未来正式运行。

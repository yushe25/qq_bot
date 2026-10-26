# T-04C Stage 6 60 分钟终验汇报

- 汇报日期：2026-09-13
- 汇报对象：项目总设计师
- 冻结基线：`7ba13ac`
- 验收提交：`acda24f`
- 当前结论：T-04C 通过，T-04A 可以关闭，T-04 仍需等待 T-04B。

## 一、结论

T-04C 已在冻结基线上完成 60 分钟连续运行和一次同库受控重启。

机器判定结果：

```text
overall_pass: true
failed_checks: []
script_exit_code: 0
```

因此可以确认：

- Stage 6 mock 稳定性通过。
- 重启一致性通过。
- T-19 pending wake 持久化通过终验。
- T-20 event dedupe 持久化通过终验。
- T-04A 可以关闭。
- T-04 还不能单独关闭，仍需 T-04B 真实 NapCat 协议矩阵。

## 二、运行信息

| 项目 | 结果 |
| --- | --- |
| 开始时间 | 2026-09-13 19:55:41 |
| 结束时间 | 2026-09-13 20:55:50 |
| 实际时长 | 3609.44 秒 |
| 受控重启 | 约 1800 秒处执行一次 |
| mock 连接次数 | 2 |
| 固定场景事件 | 20 |
| 周期消息 | 123 |
| wake_log | 118 |
| wake 决策 | 40 |
| deferred 决策 | 78 |
| 最高活跃会话 | 1 |
| 配置并发上限 | 1 |
| Traceback | 0 |
| ERROR | 0 |
| 永久 ACTIVE | 无 |

## 三、场景覆盖

| 编号 | 结果 |
| --- | --- |
| S6-M01 普通消息 | 通过 |
| S6-M02 @ 与直接触发 | 通过 |
| S6-M03 拍一拍 | 通过 |
| S6-M04 潜水 | 通过 |
| S6-M05 记忆 | 通过 |
| S6-M06 表情 | 通过 |
| S6-M07 限流 | 通过 |
| S6-M08 defer | 通过 |
| S6-M09 多轮工具 | 通过 |
| S6-M10 长时间稳定 | 通过 |

## 四、重启一致性

| 编号 | 结果 | 证据 |
| --- | --- | --- |
| S6-R01 会话状态 | 通过 | sessions 逐行一致 |
| S6-R02 会话历史 | 通过 | conversation 和 tool 配对完整 |
| S6-R03 长期记忆 | 通过 | memory_items 2 条且一致 |
| S6-R04 学习数据 | 通过 | 笔记、状态、observation 一致 |
| S6-R05 每日发送 | 通过 | daily_send_counts 一致 |
| S6-R06 待处理唤醒 | 通过 | pending_wakes 1 条且一致 |
| S6-R07 去重 | 通过 | event_dedupe 72 条且一致 |
| S6-R08 SQLite 完整性 | 通过 | 三次 integrity_check 均为 ok |

重启前后逐行一致的表：

- sessions
- memory_items
- learning_notes
- learning_state
- observations
- daily_send_counts
- sticker_usage_counts
- wake_log
- audit_logs
- pending_wakes
- event_dedupe

停止后：

- `qqf.db-wal` 不存在。
- `qqf.db-shm` 不存在。

## 五、重放防护

第一轮：

```text
action_count: 8
daily_send_total: 3
sticker_usage_total: 1
pending_wake_count: 1
event_dedupe_count: 72
```

第二轮重放相同 event/message ID：

```text
action_count: 0
daily_send_total: 3
sticker_usage_total: 1
pending_wake_count: 1
event_dedupe_count: 73
```

结论：

- 没有重复群消息。
- 没有重复私聊消息。
- 没有重复表情动作。
- 限流、每日发送和表情计数没有因重放重复消耗。

## 六、门禁

```text
.venv\Scripts\python.exe -m pytest -q
结果：182 passed, 1 warning

.venv\Scripts\python.exe -m ruff check .
结果：All checks passed!

node .pytest-tmp\package\index.js --pythonpath .venv\Scripts\python.exe
结果：0 errors, 0 warnings, 0 informations
```

## 七、证据与提交

验收记录：

- `docs/acceptance/T-04C-stage6-60m-final-20260913.md`

原始证据：

- `.pytest-tmp/stage6-20260913-60m-final/summary.json`
- `.pytest-tmp/stage6-20260913-60m-final/mock-run1.json`
- `.pytest-tmp/stage6-20260913-60m-final/mock-run2.json`
- `.pytest-tmp/stage6-20260913-60m-final/qqf-run1.log`
- `.pytest-tmp/stage6-20260913-60m-final/qqf-run2.log`
- `.pytest-tmp/stage6-20260913-60m-final/state/qqf.db`

数据库 SHA-256：

`35E78974A6224A37B0CC5A13872E322331FA0820CFC83551BFB376E51CA518B8`

摘要 SHA-256：

`3F3E26F2C73D50726F1AFEDD2A37F5AEE0FBE0C753D47620E0A734FDFE610216`

提交：

```text
acda24f docs(T-04C): record Stage 6 60-minute final acceptance
```

## 八、剩余边界与风险

- T-04 仍不能关闭，必须等待 T-04B 完成真实 NapCat 协议矩阵。
- T-04C 只验证 mock 稳定性和重启一致性，不替代真实出站 @、引用、媒体、私聊和断线重连。
- 当前仍选择“TTL 内防重复优先”：事件 claim 后、执行前若进程崩溃，可能丢事件。
- 外部动作正在发送时若进程被强制终止，重启后仍可能重复发送；彻底解决需要 outbox 和动作幂等键。

## 九、建议

1. 关闭 T-04A。
2. 进入 T-04B 真实协议矩阵。
3. T-04B 通过后再关闭完整 T-04。
4. 后续按计划进入 T-05。
5. 远端 CI 继续延后，不 push。

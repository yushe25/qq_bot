# T-04A Stage 6 修复后 30 分钟复验

- 验收日期：2026-09-13
- 修复提交：T-19 `20daa04`、T-20 `1baf2cb`
- 运行目录：`.pytest-tmp/stage6-20260913-30m-after-fix/`
- 开始时间：2026-09-13 18:40:23（Asia/Shanghai）
- 结束时间：2026-09-13 19:10:33（Asia/Shanghai）
- 实际运行时长：`1809.52` 秒，约 `30.16` 分钟
- 验收结论：`通过`

## 一、验收命令

```text
.venv\Scripts\python.exe scripts\stage6_acceptance.py --duration-minutes 30 --report-dir .pytest-tmp\stage6-20260913-30m-after-fix
```

脚本退出码：

```text
0
```

汇总结果：

```text
overall_pass: true
failed_checks: []
```

## 二、逐项检查

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
| S6-R01 会话状态 | 通过 |
| S6-R02 会话历史 | 通过 |
| S6-R03 长期记忆 | 通过 |
| S6-R04 学习数据 | 通过 |
| S6-R05 每日发送 | 通过 |
| S6-R06 待处理唤醒 | 通过 |
| S6-R07 去重 | 通过 |
| S6-R08 SQLite 完整性 | 通过 |

## 三、关键修复证据

### T-19 待处理唤醒

停止前和启动后：

- `pending_wakes` 行数均为 `1`。
- 表内容逐行一致。
- 重启后 Scheduler 恢复原 wake 上下文，不再退化为无上下文 scheduled wake。

### T-20 跨重启去重

第一轮 mock：

- `action_count=8`。
- 群消息、私聊和表情动作正常发生。
- `daily_send_total=3`。
- `sticker_usage_total=1`。

第二轮 mock 重放相同 event/message ID：

- `action_count=0`。
- 没有重复 `send_group_msg`。
- 没有重复 `send_private_msg`。
- 没有重复 sticker action。

重启前后：

- `daily_send_total` 均为 `3`。
- `sticker_usage_total` 均为 `1`。
- `event_dedupe` 行数均为 `27`。
- 重放未再次消耗限流、每日发送或表情计数。

## 四、数据库与稳定性

| 指标 | 结果 |
| --- | --- |
| mock 连接次数 | 2 |
| 固定场景事件 | 20 |
| 周期消息 | 33 |
| wake_log | 73 |
| `wake` 决策 | 40 |
| `deferred` 决策 | 33 |
| 最高活跃会话 | 1 |
| Traceback | 0 |
| ERROR | 0 |
| `integrity_check` | 重启前、启动后、最终均为 `ok` |
| 停止后 WAL/SHM | 均不存在 |

重启前后逐行一致的持久化表：

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

## 五、证据位置

- 汇总 JSON：
  `.pytest-tmp/stage6-20260913-30m-after-fix/summary.json`
- 第一轮 mock：
  `.pytest-tmp/stage6-20260913-30m-after-fix/mock-run1.json`
- 第二轮 mock：
  `.pytest-tmp/stage6-20260913-30m-after-fix/mock-run2.json`
- 数据库：
  `.pytest-tmp/stage6-20260913-30m-after-fix/state/qqf.db`
- 数据库 SHA-256：
  `48635C1B708A6A749A2F4F3C1D449C66C5B594C39087637103A522D115C35364`
- 摘要 SHA-256：
  `D36648C87A187C63275E96CE10CF3E13B3D3C6060A9736504B87470BBF38E485`

## 六、门禁

```text
.venv\Scripts\python.exe -m pytest -q
结果：182 passed, 1 warning

.venv\Scripts\python.exe -m ruff check .
结果：All checks passed!

node .pytest-tmp\package\index.js --pythonpath .venv\Scripts\python.exe
结果：0 errors, 0 warnings, 0 informations
```

## 七、剩余风险

- 当前方案选择“TTL 内防重复优先”：事件 claim 先于执行，进程若在 claim 后、执行前崩溃，该事件可能不再执行。
- deferred wake 正在发送外部动作时若进程被强制终止，重启后仍可能重复发送。彻底解决需要 outbox 和动作幂等键，不在 T-19/T-20 范围内。

## 八、结论

- T-19、T-20 的 30 分钟复验要求已经满足。
- S6-R06、S6-R07 已从失败变为通过。
- T-04 仍不能直接关闭，需要另开 60 分钟终验。

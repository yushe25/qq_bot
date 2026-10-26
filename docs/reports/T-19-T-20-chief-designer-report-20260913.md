# T-19/T-20 修复完成汇报

- 汇报日期：2026-09-13
- 汇报对象：项目总设计师
- 任务范围：T-19 待处理唤醒持久化、T-20 跨重启事件去重
- 当前结论：交办项已完成，修复后 30 分钟 Stage 6 复验通过。
- T-04 状态：仍未关闭，需要另开 60 分钟终验。

## 一、给总设计师的结论

本次修复关闭了两个重启一致性阻塞缺陷：

- T-19：deferred/pending wake 现在持久化完整执行意图，重启后恢复原上下文。
- T-20：事件去重指纹现在跨进程持久化，相同 message/event ID 重放不会再次执行。

修复后的 30 分钟复验中：

- `overall_pass=true`
- `failed_checks=[]`
- S6-M01 至 S6-M10 全部通过
- S6-R01 至 S6-R08 全部通过
- 第二轮重放相同事件时 `action_count=0`
- 群消息、私聊、表情、每日发送和限流计数均未因重放重复增加

## 二、提交记录

| 顺序 | 提交 | 内容 |
| --- | --- | --- |
| 1 | `1721ce5` | T-04A 验收脚本失败时返回非零 |
| 2 | `20daa04` | T-19 pending wake 持久化 |
| 3 | `1baf2cb` | T-20 event dedupe 持久化 |
| 4 | `7ba13ac` | 修复后 30 分钟复验记录 |

全部提交均为本地提交，未 push。

## 三、T-19 修复内容

### 实现

- 新增 `pending_wakes` 表及 due 索引。
- 新增 `WakeInput` 显式 JSON 序列化与反序列化。
- `events_json` 只保存标准化字段，不保存 `NormalizedEvent.raw`。
- `save_deferred_wake()` 在单个 SQLite 事务中更新：
  - `sessions`
  - `pending_wakes`
- `Scheduler.start()` 启动时恢复 pending wake。
- 重复调用 `Scheduler.start()` 不重复创建任务。
- 到期后使用原 category、reason、events、probability 和 defer flags 执行。
- 正常返回后删除 pending 记录。
- 异常时记录 attempts，最多重试 3 次。
- 超过重试上限后删除并写审计。
- 坏 JSON 或孤立 session 记录删除并写审计，不阻断应用启动。

### 验证

- 存储关闭重开后记录仍存在。
- 重启后恢复内容与原 `WakeInput` 一致。
- 已到期任务首次 tick 使用原上下文执行。
- 成功执行后记录删除。
- 异常重试和上限删除通过。
- 序列化内容不包含 raw 字段。

## 四、T-20 修复内容

### 实现

- 新增 `event_dedupe` 表及 expiry 索引。
- `MessageDedupe` 的 key 生成规则提取为 `message_dedupe_key()`。
- `StateStore.claim_event()` 使用原子 `INSERT OR IGNORE`。
- TTL 到期后相同 key 可重新 claim。
- `Scheduler.ingest()` 改为异步：
  1. 白名单检查。
  2. 忽略 bot 自己发送的消息。
  3. 计算事件 key。
  4. 持久化 claim。
  5. claim 成功后才进入 observation 和 inbox。
- `Scheduler.tick()` 最多每 60 秒执行一次 dedupe 清理。
- 清理按 `expires_at` 和数量上限执行。
- 表内只保存 key、seen_at、expires_at，不保存消息文本。

### 验证

- message ID 去重跨 `StateStore` 重启生效。
- fallback key 跨重启生效。
- 相同文本但事件时间不同仍可分别接收。
- TTL 到期后 key 可再次 claim。
- 白名单拒绝事件不写 fingerprint。
- 数量上限清理通过。

## 五、30 分钟复验

运行命令：

```text
.venv\Scripts\python.exe scripts\stage6_acceptance.py --duration-minutes 30 --report-dir .pytest-tmp\stage6-20260913-30m-after-fix
```

运行结果：

- 实际时长：`1809.52` 秒。
- 开始：2026-09-13 18:40:23。
- 结束：2026-09-13 19:10:33。
- 脚本退出码：`0`。
- `overall_pass=true`。
- `failed_checks=[]`。
- wake_log：73 条。
- `wake`：40 次。
- `deferred`：33 次。
- 最高活跃会话：1。
- Traceback：0。
- ERROR：0。

第一轮 mock：

| 指标 | 结果 |
| --- | --- |
| action_count | 8 |
| daily_send_total | 3 |
| sticker_usage_total | 1 |
| pending_wake_count | 1 |
| event_dedupe_count | 27 |

第二轮重放相同 ID：

| 指标 | 结果 |
| --- | --- |
| action_count | 0 |
| repeat send_group_msg | 0 |
| repeat send_private_msg | 0 |
| repeat sticker action | 0 |
| daily_send_total | 3 |
| sticker_usage_total | 1 |
| event_dedupe_count | 27 |

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

SQLite：

- 重启前 `integrity_check=ok`
- 启动后 `integrity_check=ok`
- 最终 `integrity_check=ok`
- 停止后 WAL/SHM 均不存在

## 六、门禁

```text
.venv\Scripts\python.exe -m pytest -q
结果：182 passed, 1 warning

.venv\Scripts\python.exe -m ruff check .
结果：All checks passed!

node .pytest-tmp\package\index.js --pythonpath .venv\Scripts\python.exe
结果：0 errors, 0 warnings, 0 informations
```

## 七、验收材料

- `docs/acceptance/T-04A-stage6-mock-restart-20260913.md`
- `docs/acceptance/T-04A-stage6-post-fix-20260913.md`
- `.pytest-tmp/stage6-20260913-30m-after-fix/summary.json`
- `.pytest-tmp/stage6-20260913-30m-after-fix/mock-run1.json`
- `.pytest-tmp/stage6-20260913-30m-after-fix/mock-run2.json`
- `.pytest-tmp/stage6-20260913-30m-after-fix/state/qqf.db`

数据库 SHA-256：

`48635C1B708A6A749A2F4F3C1D449C66C5B594C39087637103A522D115C35364`

摘要 SHA-256：

`D36648C87A187C63275E96CE10CF3E13B3D3C6060A9736504B87470BBF38E485`

## 八、剩余风险

- 当前实现选择“TTL 内防重复优先”。
- 事件 claim 后、执行前若进程崩溃，该事件可能丢失。
- deferred wake 正在发送外部动作时若被强制终止，重启后仍可能重复发送。
- 彻底解决需要 outbox 状态机和动作幂等键，不在 T-19/T-20 范围内。

## 九、下一步建议

1. 另开 T-04 60 分钟终验。
2. T-04B 真实协议矩阵可以继续并行。
3. 60 分钟通过后关闭 T-04A 和 T-04。
4. 远端 CI 继续延后，不 push。

# T-26 待唤醒优先级合并验收记录

- 日期：2026-09-15
- 基线：`a6e1c33`
- 交办单：`docs/reports/T-26-deferred-mention-priority-handoff-20260915.md`
- 状态：自动测试与真实冷却期 @ 复验均已通过

## 一、修复内容

- 新增 `WAKE_PRIORITY`：`force > mention > name > question > keyword > poke > normal > scheduled`。
- 新增 `_merge_pending_wakes()`：低优先级消息不能覆盖高优先级 category、reason 和 probability。
- 合并双方 events，按到达顺序保留最近 40 条。
- `deferred` 固定为 true，`defer_until_active` 与 `phase_ready` 使用逻辑或。
- `_defer()` 的所有延后路径统一写入合并结果，不改变 `pending_wakes` 表结构。
- 合并结果继续通过 `save_deferred_wake()` upsert，重启后可从 SQLite 恢复。

## 二、自动验证

```text
.venv\Scripts\python.exe -m pytest -q tests/test_scheduler.py
21 passed

.venv\Scripts\python.exe -m ruff check app/runtime/scheduler.py tests/test_scheduler.py
All checks passed!
```

覆盖场景：

- mention + normal 保持 mention，events 同时保留。
- normal + mention 提升为 mention。
- force + normal 保持 force。
- 超过 40 条时保留最新事件。
- COOLDOWN 期间 mention 被普通消息合并后不降级，冷却到期执行 mention。
- 合并结果关闭并重开 `StateStore` 后 category、reason、events 顺序保持一致。

## 三、真实复验

QQF 重启到含 T-26 修复的代码后，在测试群完成以下顺序：

1. 让机器人回复一次，进入正常冷却。
2. 冷却期间发送普通群消息。
3. 紧接着 @ 机器人提问。
4. 再发送普通群消息。
5. 等待冷却到期。

实测结果：

- 普通消息先记录为 `normal` deferred。
- mention 到达后记录为 `mention` deferred，并继承冷却到期时间。
- 冷却到期最终 `wake_log` 的 category 为 `mention`，reason 为“消息中包含 @”。
- 最终 prompt 的最近消息按顺序包含：

```text
我这边先整理一下
[CQ:at,...] 刚才说的文件在哪？
整理好我再发你
```

- 机器人只发送一条文字回复，没有重复发送或发送第二张表情/第二条消息。
- SafetyPipeline、限流和审计保持有效。

结论：普通消息没有把待唤醒降级为 `normal`，高优先级 mention 在冷却期得到保留并最终执行。

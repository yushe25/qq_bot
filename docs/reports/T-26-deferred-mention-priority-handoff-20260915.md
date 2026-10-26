# T-26 冷却期高优先级唤醒保护交办单

- 发布日期：2026-09-15
- 当前基线：`a6e1c33`
- 输入报告：`docs/reports/T-25B-engineer-closure-report-20260915.md`
- 任务性质：调度正确性、待唤醒优先级、事件上下文合并
- 严重度：高，会导致部分 @ 和私聊待回复丢失
- T-25 状态：可以关闭，本任务不重新打开 T-25

## 一、结论

T-25 的代码、自动标注、真实视觉服务和真实群选图验收已完成，可以关闭。

T-26 应作为下一个独立任务。根因不是模型、NapCat 或表情模块，而是每个会话只有一个
`pending_wakes` 槽位。冷却期间后续普通消息会把已有的 mention 待唤醒记录整体覆盖，
包括 category、reason、events 和概率状态。

## 二、已确认事实

- `pending_wakes.session_id` 是主键，每个会话最多一条待唤醒。
- `_defer()` 对任何传入的 wake 都执行覆盖保存：

```python
self._pending_wakes[session.id] = wake
await self.store.save_deferred_wake(session, wake)
```

- 冷却、限流、全局并发、作息延后都会走 `_defer()`。
- 普通消息进入冷却后同样会调用 `_defer()`。
- 因此：

```text
mention 入待唤醒槽
  -> 普通消息到达
  -> 普通消息覆盖 category/reason/events
  -> 冷却到期后只执行普通消息
  -> @ 回复丢失
```

- 本地复验门禁：

```text
pytest: 247 passed
ruff: All checks passed
pyright: 0 errors
```

## 三、目标

1. 后到的低优先级消息不能覆盖未执行的高优先级唤醒。
2. 后到的高优先级消息可以把普通待唤醒提升为高优先级。
3. 合并后的上下文应保留优先级最高的原因，同时包含期间的新消息。
4. 同一会话仍只保留一条待唤醒，不新增队列表或数据库迁移。
5. 重启后合并结果保持一致。

## 四、优先级定义

采用明确、可测试的内部顺序：

```python
WAKE_PRIORITY = {
    TriggerCategory.FORCE: 70,
    TriggerCategory.MENTION: 60,
    TriggerCategory.NAME: 50,
    TriggerCategory.QUESTION: 40,
    TriggerCategory.KEYWORD: 30,
    TriggerCategory.POKE: 20,
    TriggerCategory.NORMAL: 10,
    TriggerCategory.SCHEDULED: 0,
}
```

规则：

- 新 wake 优先级更高：采用新 wake 的 category 和 reason。
- 已有 wake 优先级更高或相同：保留已有 category、reason 和 probability。
- 无论谁胜出，events 都按到达顺序合并。
- `deferred=True` 始终保留。
- `defer_until_active` 使用逻辑或。
- `phase_ready` 使用逻辑或。
- 合并后的 events 最多保留最近 40 条，避免 SQLite 记录无限增长。

## 五、实现设计

### 5.1 新增合并函数

涉及 `app/runtime/scheduler.py`：

```python
def _merge_pending_wakes(
    current: WakeInput | None,
    incoming: WakeInput,
) -> WakeInput:
    ...
```

实现要点：

- `current is None` 时直接返回 `incoming`。
- 按 `WAKE_PRIORITY` 选择胜出者。
- 合并事件：

```python
events = (*current.events, *incoming.events)
events = events[-MAX_PENDING_WAKE_EVENTS:]
```

- 返回 `dataclasses.replace()` 构造的新对象，不原地修改 dataclass。

建议伪代码：

```python
winner = current
if _wake_priority(incoming.category) > _wake_priority(current.category):
    winner = incoming

return replace(
    winner,
    events=events,
    deferred=True,
    defer_until_active=current.defer_until_active or incoming.defer_until_active,
    phase_ready=current.phase_ready or incoming.phase_ready,
)
```

### 5.2 接入 `_defer()`

当前：

```python
if wake is not None:
    self._pending_wakes[session.id] = wake
    await self.store.save_deferred_wake(session, wake)
```

改为：

```python
if wake is not None:
    merged = _merge_pending_wakes(self._pending_wakes.get(session.id), wake)
    self._pending_wakes[session.id] = merged
    await self.store.save_deferred_wake(session, merged)
```

不得修改数据库表结构，`save_deferred_wake()` 继续执行 upsert。

### 5.3 日志和审计

- 不需要给每次合并新增审计噪声。
- 可在 `debug` 级别记录：

```text
session=<id> pending wake kept=<category> incoming=<category>
```

- 日志不得写消息正文、QQ 号或真实群号。

## 六、边界条件

- mention 已在待执行，普通消息继续合并，不降级。
- normal 已在待执行，随后 mention 到达，提升为 mention。
- mention 已在待执行，随后 FORCE 到达，提升为 FORCE。
- 多个普通消息到达，不改变普通优先级，只扩充上下文。
- 待执行 wake 已到期但尚未被 tick 消费时，新消息仍按合并逻辑处理。
- 合并后重启，必须从 SQLite 恢复完整 category、reason 和 events。
- 超过 40 条时保留最新消息，避免因为旧消息挤掉 @ 后的新上下文。

## 七、工程任务清单

### T-26.1 增加待唤醒优先级和合并函数

- 目标：建立单槽位下的优先级合并规则。
- 涉及文件：
  - `app/runtime/scheduler.py`
  - `tests/test_scheduler.py`
- 实现要点：
  - 增加 `WAKE_PRIORITY` 和 `MAX_PENDING_WAKE_EVENTS`。
  - 实现 `_merge_pending_wakes()`。
- 验收：
  - mention + normal 保持 mention。
  - normal + mention 提升为 mention。
  - force + normal 保持 force。
- 依赖：无。
- 风险：低，纯内存逻辑。

### T-26.2 接入 `_defer()`

- 目标：所有延后路径统一使用合并保存。
- 涉及文件：
  - `app/runtime/scheduler.py`
  - `tests/test_scheduler.py`
- 实现要点：
  - `_defer()` 读取当前 `_pending_wakes`，合并后再写入内存和 SQLite。
- 验收：
  - 冷却、限流、作息延后、全局并发延后都不丢高优先级 category。
- 依赖：T-26.1。
- 风险：中，需覆盖所有 `_defer()` 调用方。

### T-26.3 冷却期 mention 回归测试

- 目标：复现并锁死真实观察到的漏 @ 场景。
- 涉及文件：
  - `tests/test_scheduler.py`
- 测试步骤：

```text
1. session 进入 COOLDOWN。
2. _decide_and_run(mention) 写入待唤醒。
3. _decide_and_run(normal) 到达。
4. 断言 pending category 仍为 MENTION。
5. 断言 events 同时包含 mention 和 normal。
6. 断言 SQLite load_pending_wakes 返回同样结果。
7. 冷却到期后 tick，wake_log 的 category/reason 为 mention。
```

- 验收：
  - 新增测试在旧代码上失败，在修复后通过。
  - 不依赖真实时间，使用 monkeypatch 或手工设置到期时间。
- 依赖：T-26.2。
- 风险：低。

### T-26.4 重启持久化测试

- 目标：确认合并后的高优先级待唤醒跨重启不退化。
- 涉及文件：
  - `tests/test_scheduler.py`
- 实现要点：
  - 写入 mention + normal 合并结果。
  - 关闭并重开 `StateStore`。
  - 调用 `scheduler.start()`。
- 验收：
  - 恢复后 category 为 mention。
  - reason 为 mention 原因。
  - events 数量和顺序与保存前一致。
- 依赖：T-26.3。
- 风险：低。

### T-26.5 文档和验收记录

- 目标：把“待唤醒优先级合并”写入契约和报告。
- 涉及文件：
  - `docs/design-contracts/schedule-behavior.md`
  - `docs/PROJECT_REPORT.md`
  - 新增 `docs/acceptance/T-26-deferred-priority-20260915.md`
- 实现要点：
  - 说明每会话单槽位规则。
  - 说明高优先级不可被普通消息覆盖。
  - 记录自动测试和真实复验证据。
- 验收：
  - 文档与代码行为一致。
  - 真实复现场景不再漏 @。
- 依赖：T-26.4。
- 风险：低。

## 八、真实复验

在测试群按顺序执行：

1. 让机器人回复一次，进入正常冷却。
2. 冷却期间发送一条普通群消息。
3. 紧接着 @ 机器人提问。
4. 再发送一条普通群消息。
5. 等待冷却到期。
6. 检查审计、`wake_log` 和群消息。

通过标准：

- 到期执行的是 mention 唤醒。
- 回复内容包含 @ 问题相关上下文。
- 后续普通消息不会把 mention 改为 normal。
- 不出现重复发送。
- SafetyPipeline、限流和审计保持有效。

## 九、统一门禁

```text
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pyright
git diff --check
```

## 十、提交建议

```text
fix(T-26): preserve high-priority deferred wakes
test(T-26): cover cooling-period mention merge
docs(T-26): record deferred wake priority rules
```

禁止提交：

- `config.json`
- `stickers/`
- `personas/sticker-pilot.yaml`
- `state/`
- 日志、Token、真实群号

## 十一、待项目方确认

T-26 已具备直接施工条件。没有新的产品选项需要项目方决策。

开始修改代码前，需要项目方明确说“开始执行”。

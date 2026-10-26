# T-19/T-20 重启状态持久化修复交办单

- 发布日期：2026-09-13
- 基线提交：`0b53770`
- 输入报告：`docs/reports/T-04A-chief-designer-report-20260913.md`
- 验收记录：`docs/acceptance/T-04A-stage6-mock-restart-20260913.md`
- 任务性质：缺陷修复、回归测试、30 分钟复验
- 并行任务：T-04B 真实 NapCat 协议矩阵验收
- 远端策略：继续不 push，不触发远端 CI

## 一、总设计师决定

1. 接受 T-04A 当前状态为“30 分钟验收已完成、整体未通过”。
2. T-19、T-20 必须排在 T-04 60 分钟终验之前完成。
3. T-04B 可与 T-19/T-20 修复并行执行，不等待 mock 修复。
4. 修复后先重跑 30 分钟验收；30 分钟全部通过后再执行 60 分钟终验。
5. T-04A 当前脚本、报告和验收材料先形成独立提交，不得与业务修复混在同一提交。
6. 不提前实现完整 T-05；本任务只允许使用现有 additive schema 方式增加本任务需要的表和索引。

## 二、任务目标

修复两个重启一致性缺陷，使同类事件在进程重启后不会丢失延后上下文，也不会因消息重放而重复执行：

- T-19：持久化 deferred/pending wake 的完整执行意图。
- T-20：持久化消息去重指纹及过期时间。

本任务完成标准不是“表和字段已增加”，而是：

- 30 分钟 Stage 6 验收中的 S6-R06、S6-R07 从失败变为通过。
- 重启后不出现相同 message ID 的重复发送或重复表情发送。
- 待处理唤醒恢复原 category、reason、events 和 defer flags。
- 验收脚本在任一要求项失败时返回非零退出码。

## 三、不在本任务范围内

- 不实现 T-05 的完整 schema version、自动备份和恢复演练。
- 不处理 T-08 的控制台 Token URL 泄漏。
- 不开发 T-09 至 T-12。
- 不修改防重复发送的产品策略、限流阈值或拟人化参数。
- 不给 `pending_wakes` 或 `event_dedupe` 增加与重启恢复无关的字段。
- 不修改真实 `config.json`，不提交真实群号、Token、Key 或 `state/`。

## 四、提交顺序

1. `test(T-04A): make failed acceptance checks return nonzero`
   - 先提交现有 T-04A 验收脚本、测试、报告和 PROJECT_REPORT 变更。
   - 修正验收驱动退出码语义。
2. `fix(T-19): persist pending wake context`
   - 完成 pending wake 表、序列化、恢复和清理。
3. `fix(T-20): persist event dedupe across restart`
   - 完成 event dedupe 表、claim、TTL 和清理。
4. `docs(T-04A): record post-fix 30-minute acceptance`
   - 加入修复后的 30 分钟验收结果。

四个提交均不 push。若必须合并提交，至少保持 T-19 与 T-20 的代码和测试边界清晰。

## 五、T-19 技术方案

### 5.1 涉及文件

- `app/runtime/scheduler.py`
- `app/domain/types.py`
- `app/storage/db.py`
- `app/main.py`
- `tests/test_scheduler.py`
- `tests/test_stage6_acceptance.py`

### 5.2 数据表

在现有 additive schema 中增加：

```sql
CREATE TABLE IF NOT EXISTS pending_wakes (
    session_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    reason TEXT NOT NULL,
    events_json TEXT NOT NULL DEFAULT '[]',
    probability REAL,
    deferred INTEGER NOT NULL DEFAULT 0,
    defer_until_active INTEGER NOT NULL DEFAULT 0,
    phase_ready INTEGER NOT NULL DEFAULT 0,
    next_wake_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pending_wakes_due
    ON pending_wakes(next_wake_at, session_id);
```

布尔字段写入 SQLite 时统一使用 `0/1`。`events_json` 只保存恢复执行所需的标准化字段，不保存 `NormalizedEvent.raw`。

### 5.3 WakeInput 序列化

在 `WakeInput` 附近增加显式序列化函数，禁止依赖 `dataclasses.asdict()` 后直接存储整个对象：

```python
def wake_input_to_json(wake: WakeInput) -> str: ...
def wake_input_from_json(session_id: str, payload: str) -> WakeInput: ...
```

序列化字段固定为：

```text
event_id, session_id, timestamp, sender_id, sender_name,
kind, segments, text, bot_id
```

其中 `segments` 固定为 `[{"kind": str, "data": dict}]`。反序列化遇到坏 JSON 或字段缺失时返回 `None` 并写审计，不得让整个应用启动失败。

### 5.4 StateStore 接口

新增以下接口，具体命名可保持仓库风格，但语义不得变化：

```python
async def save_deferred_wake(
    self,
    session: Session,
    wake: WakeInput,
) -> None: ...

async def load_pending_wakes(self) -> list[tuple[str, float, WakeInput]]: ...

async def delete_pending_wake(self, session_id: str) -> None: ...

async def increment_pending_wake_attempts(self, session_id: str) -> int: ...
```

`save_deferred_wake()` 必须在同一 SQLite 事务内更新 `sessions` 和 `pending_wakes`，避免出现会话说“已延后”但任务表没有上下文，或任务表存在但会话未更新。

### 5.5 生命周期

```text
store.init()
  -> sessions.load()
  -> scheduler.start()
       -> load_pending_wakes()
       -> restore Scheduler._pending_wakes
  -> gateway.connect()
  -> scheduler.run()
```

`Scheduler.start()` 是可重复调用的：测试中重复调用不得生成重复任务。

恢复后：

- `session.next_wake_at <= now`：下一次 `tick()` 立即处理。
- `session.next_wake_at > now`：保持原到期时间等待。
- `_running` 已包含该会话：保留记录，不并发执行。
- 记录对应会话不存在：写审计并删除孤立待处理记录。

### 5.6 完成、失败和重试语义

- `orchestrator.run_wake()` 正常返回且没有异常：删除 `pending_wakes` 中对应记录。
- `_defer()` 被再次调用：覆盖原记录的 `next_wake_at`、defer flags、events 和 `updated_at`。
- `orchestrator.run_wake()` 抛出异常：保留记录，`attempts + 1`，最多重试 3 次；每次重试间隔递增，超过上限后删除记录并写错误审计。
- 进程在任务执行中被强制终止：记录保留，重启后按原上下文恢复执行。允许极端情况下重复执行外部动作，但不能静默丢失任务。

## 六、T-20 技术方案

### 6.1 涉及文件

- `app/runtime/message_dedupe.py`
- `app/runtime/scheduler.py`
- `app/storage/db.py`
- `tests/test_message_dedupe.py`
- `tests/test_scheduler.py`
- `tests/test_stage6_acceptance.py`

### 6.2 数据表

```sql
CREATE TABLE IF NOT EXISTS event_dedupe (
    key TEXT PRIMARY KEY,
    seen_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_event_dedupe_expiry
    ON event_dedupe(expires_at);
```

### 6.3 状态接口

`MessageDedupe` 保留 key 生成规则，但改为由 `StateStore` 做原子 claim：

```python
def message_dedupe_key(event: NormalizedEvent) -> str: ...

async def claim_event(
    self,
    key: str,
    now_s: float,
    ttl_seconds: int,
) -> bool: ...

async def prune_event_dedupe(self, now_s: float, max_entries: int) -> int: ...
```

`claim_event()` 使用 `INSERT OR IGNORE`。返回 `True` 表示首次接收，返回 `False` 表示 TTL 内已处理过。

### 6.4 接收时序

`Scheduler.ingest()` 改为异步，执行顺序固定为：

1. 白名单检查。
2. 忽略 bot 自己发送的消息。
3. 计算 dedupe key。
4. 调用 `StateStore.claim_event()`。
5. claim 成功后才采集 observation、创建会话和追加 inbox。

不得在白名单检查前写 fingerprint，避免被拒绝事件污染去重表。

### 6.5 清理策略

- 默认 TTL 保持 `MessageDedupe` 当前值 `3600` 秒。
- 每次 `claim_event()` 只处理当前 key，不做全表扫描。
- `Scheduler.tick()` 最多每分钟调用一次 `prune_event_dedupe()`。
- 超过 TTL 的 key 可重新 claim。
- 清理不得按文本内容删除，只按 `expires_at` 和数量上限处理。

## 七、验收脚本修正

`scripts/stage6_acceptance.py` 当前无论 S6-R06、S6-R07 是否失败都返回退出码 `0`，与 T-04A 交办要求不一致。本任务必须修正：

1. 在 `summary.json` 增加 `overall_pass: bool` 和 `failed_checks: list[str]`。
2. 只有所有必测 S6-M、S6-R 项通过时退出码才为 `0`。
3. 任一必测项失败、未执行或证据缺失时退出码为 `1`。
4. 修复后跑一次故意失败用例，证明脚本确实返回非零。

## 八、测试要求

至少新增以下测试：

### T-19

1. deferred wake 写入后关闭并重开 `StateStore`，记录仍存在。
2. 重启后 scheduler 恢复相同 category、reason、events 和 defer flags。
3. 已到期 pending wake 在首次 tick 使用原上下文执行。
4. 成功执行后 pending wake 被删除。
5. 执行异常时 attempts 增加，记录保留并按上限重试。
6. 孤立 session 的 pending wake 被删除并写审计。
7. 序列化不包含 `raw`，坏 JSON 不阻断启动。

### T-20

1. message ID 去重跨 `StateStore` 重启生效。
2. fallback key 跨重启生效。
3. 相同文本但事件时间不同仍可分别接收。
4. TTL 到期后相同 key 可再次 claim。
5. 白名单拒绝的事件不会写入 fingerprint。
6. `event_dedupe` 表中不保存原始消息文本。

### 验收驱动

1. 注入一个失败检查时 `run_acceptance()` 返回非零。
2. 所有检查通过时返回 `0`。
3. `summary.json` 的 `failed_checks` 与实际失败项一致。

## 九、复验步骤

修复后先执行短时门禁：

```text
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
node .pytest-tmp\package\index.js --pythonpath .venv\Scripts\python.exe
```

然后执行 30 分钟复验：

```text
.venv\Scripts\python.exe scripts\stage6_acceptance.py --duration-minutes 30 --report-dir .pytest-tmp\stage6-20260913-30m-after-fix
```

30 分钟验收必须确认：

- S6-R06 通过。
- S6-R07 通过。
- 两轮 mock 使用相同 event/message ID 时，第二轮不产生重复群消息或重复表情 action。
- `daily_send_counts` 不因重放再次增加。
- `sticker_usage_counts` 不因重放再次增加。
- `summary.json.overall_pass == true`，脚本退出码为 `0`。

30 分钟通过后另开 60 分钟终验，不在本任务中直接宣称 T-04 完成。

## 十、并行 T-04B 安排

T-04B 使用独立工作分支或独立 worktree，不与本任务共享未提交改动。验收矩阵至少覆盖：

- HTTP `get_version_info`。
- 正向 WS 鉴权与生命周期事件。
- 真实群文本发送。
- 出站 @ 和引用回复。
- 白名单表情或其他媒体发送。
- 私聊出站。
- 断开 WS 后自动重连，恢复后不重复发送已确认动作。

T-04B 只产出真实验收报告；发现代码缺陷时登记新任务，不当场混入 T-19/T-20 提交。

## 十一、完成定义

- T-19、T-20 均有独立回归测试。
- 30 分钟复验中 S6-R06、S6-R07 通过。
- 验收脚本失败时返回非零，通过时返回零。
- pytest、ruff、pyright 全绿。
- 原有 172 个用例保持通过，新增行为持续提高覆盖数。
- 无真实数据、密钥、`state/` 或无关改进入提交。
- 文档更新后明确写明：T-04A 已复验通过或仍失败；T-04 是否仍需 60 分钟终验。

## 十二、风险与处理

- 若 claim 先于执行且进程崩溃，可能丢事件；当前任务明确选择“TTL 内防重复优先”。后续如要消除该取舍，另开 event inbox 状态机任务。
- 若 deferred wake 在进程被杀时正在发送，重启后可能重复发送。解决该问题需要 outbox 和动作幂等键，不在本任务范围内，但必须在报告中保留为风险。
- 若把 pending wake 与会话分两个事务写入，可能在崩溃时产生不一致；必须使用单事务接口。
- 若 `event_dedupe` 永久保留，会持续增长；必须按 TTL 和数量上限清理。
- 若 T-04B 与修复工作共用 worktree，未提交文件会污染真实协议证据；必须隔离分支或 worktree。

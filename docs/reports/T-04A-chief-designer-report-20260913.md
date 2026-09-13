# T-04A Stage 6 验收工作汇报

- 汇报日期：2026-09-13
- 汇报对象：项目总设计师
- 基线提交：`0b53770`
- 任务范围：T-04A mock 30 分钟长跑与重启一致性验收
- 当前结论：验收流程完整执行，30 分钟长跑稳定，但整体判定为未通过。

## 一、给总设计师的结论

T-04A 已经完成可复查的 30 分钟隔离长跑、完整场景注入、受控停止、同库重启和重启前后数据库逐行比对。

本轮验收发现两个真实重启缺陷：

- T-19：待处理唤醒只存在于内存，重启后原有 deferred 上下文丢失。
- T-20：去重指纹只存在于内存，重启后相同 message/event ID 会再次执行。

T-04B 不必等待这两个缺陷，可以并行开展真实协议矩阵验收。T-04 最终关闭必须先修复 T-19、T-20，并补跑 60 分钟终验。

## 二、本次完成的工作

### 验收工具

- 扩展 `scripts/mock_qq_server.py`：
  - 增加确定性 Stage 6 场景序列。
  - 支持 seed、场景间隔、周期消息。
  - 支持 stop file 受控停止。
  - 支持运行结束 JSON 报告。
- 新增 `scripts/stage6_acceptance.py`：
  - 启动和停止隔离 mock 服务。
  - 启动和重启隔离 QQF。
  - 使用验收专用 Provider 覆盖记忆、潜水、表情、私聊和多轮工具。
  - 轮询健康状态和活跃会话。
  - 保存重启前、启动后、最终数据库快照。
- 新增 `tests/test_stage6_acceptance.py`：
  - 覆盖 Provider 工具路径。
  - 覆盖两轮工具调用。
  - 覆盖确定性事件矩阵。
  - 覆盖数据库快照差异和审计表统计。

### 30 分钟长跑

运行命令：

```text
.venv\Scripts\python.exe scripts\stage6_acceptance.py --duration-minutes 30 --report-dir .pytest-tmp\stage6-20260913-30m
```

运行结果：

- 实际运行：`1807.00` 秒，约 `30.12` 分钟。
- 开始：2026-09-13 15:58:49（Asia/Shanghai）。
- 结束：2026-09-13 16:28:56（Asia/Shanghai）。
- mock 固定场景事件：20 次。
- mock 周期消息：33 次。
- mock action：15 次。
- wake_log：100 条。
- `wake`：40 次。
- `deferred`：60 次。
- 最高活跃会话：1。
- Traceback：0。
- ERROR：0。

### 重启一致性

在约 900 秒处完成：

1. 受控停止 mock 和 QQF。
2. 保存停止前数据库快照。
3. 使用同一数据库启动 QQF。
4. 保存启动后快照。
5. 重启 mock，重放相同场景。
6. 保存最终快照。

重启前后逐行一致的持久化数据：

- sessions
- conversation 和 pending_output
- memory_items
- learning_notes
- learning_state
- observations
- daily_send_counts
- wake_log
- audit_logs

SQLite `integrity_check`：

- 重启前：`ok`
- 启动后：`ok`
- 最终：`ok`

停止后：

- `qqf.db-wal` 不存在。
- `qqf.db-shm` 不存在。

## 三、逐项验收结论

### Mock 长跑矩阵

| 编号 | 结果 | 说明 |
| --- | --- | --- |
| S6-M01 | 通过 | 普通消息和周期消息均被处理，状态能离开 ACTIVE |
| S6-M02 | 通过 | @、关键词、私聊直接触发均进入正确分类 |
| S6-M03 | 通过 | 拍一拍被归一化为 poke |
| S6-M04 | 通过 | `qq_dive` 生效，最终会话状态为 diving |
| S6-M05 | 通过 | 记忆成功写入并在重启后保留 |
| S6-M06 | 通过 | 白名单表情发送成功，usage_count 和审计存在 |
| S6-M07 | 通过 | 60 次 deferred，7 次发送被频率限制拒绝 |
| S6-M08 | 通过 | deferred 后按原关键词上下文再次唤醒 |
| S6-M09 | 通过 | 9 组 tool call 与 result 完整配对，无孤立消息 |
| S6-M10 | 通过 | 30 分钟无崩溃、无永久 ACTIVE；跨重启重复发送单独记入 R07 |

### 重启一致性矩阵

| 编号 | 结果 | 说明 |
| --- | --- | --- |
| S6-R01 | 通过 | 会话状态重启前后一致 |
| S6-R02 | 通过 | 会话历史和 tool 配对完整 |
| S6-R03 | 通过 | 长期记忆逐行一致 |
| S6-R04 | 通过 | 学习笔记、状态和 observation 逐行一致 |
| S6-R05 | 通过 | 每日发送计数重启本身未重置或重复累加 |
| S6-R06 | 失败 | pending wake 上下文未持久化 |
| S6-R07 | 失败 | 去重指纹未持久化，重放后再次执行 |
| S6-R08 | 通过 | SQLite 完整性与 WAL 收敛 |

## 四、缺陷说明

### T-19：待处理唤醒未持久化

现象：

- `_pending_wakes` 只保存于进程内存。
- 重启后 category、reason、events 和 defer flags 无法恢复。
- 原 deferred 工作会退化成默认 scheduled wake。

影响：

- 被 @ 或关键词触发的延后任务可能在重启后失去原始上下文。
- 拟真行为会出现语义漂移。

建议：

- 新增 pending wake 持久化表和恢复逻辑。
- 启动后恢复未到期和已到期待执行任务。
- 修复后重跑 S6-R06。

### T-20：去重指纹未跨重启保存

现象：

- 第二轮 mock 重放相同的 message/event ID。
- M02 mention 和 M06 sticker 再次执行。
- mock action 重复出现群消息 ID `1002` 和表情 ID `1004`。
- 群聊每日发送最终为 4，表情 usage_count 为 2。

影响：

- 断线重连或进程重启后可能重复发言和重复发表情。
- 每日发送和限流计数会被重复消耗。

建议：

- 持久化 event/message 指纹。
- 清理策略与 dedupe TTL 一致。
- 修复后重跑 S6-R07。

## 五、证据位置

- 验收报告：
  `docs/acceptance/T-04A-stage6-mock-restart-20260913.md`
- 汇总：
  `.pytest-tmp/stage6-20260913-30m/summary.json`
- mock 第一轮：
  `.pytest-tmp/stage6-20260913-30m/mock-run1.json`
- mock 第二轮：
  `.pytest-tmp/stage6-20260913-30m/mock-run2.json`
- QQF 第一轮日志：
  `.pytest-tmp/stage6-20260913-30m/qqf-run1.log`
- QQF 第二轮日志：
  `.pytest-tmp/stage6-20260913-30m/qqf-run2.log`
- 数据库：
  `.pytest-tmp/stage6-20260913-30m/state/qqf.db`
- 数据库 SHA-256：
  `B2336265BBB6251C7BE691190DC87A2FCEE116AD161F9F07799BC047A6CB0864`
- 摘要 SHA-256：
  `C7835227DA24FB8EFEB3EB5603B50A48BAEA4327CAAE1AAB2693E11CFD9C5EBD`

## 六、门禁

```text
.venv\Scripts\python.exe -m pytest -q
结果：172 passed, 1 warning
```

```text
.venv\Scripts\python.exe -m ruff check .
结果：All checks passed!
```

```text
node .pytest-tmp\package\index.js --pythonpath .venv\Scripts\python.exe
结果：0 errors, 0 warnings, 0 informations
```

## 七、需要总设计师确认的事项

1. 是否将 T-19、T-20 排到 T-04 60 分钟终验之前。
2. 是否允许 T-04B 真实协议矩阵与 T-19、T-20 修复并行执行。
3. 是否接受 T-04A 当前状态为“30 分钟验收完成但整体未通过”。
4. 是否继续维持远端 CI 延后策略。

建议默认执行：

- 先修复 T-19、T-20。
- T-04B 并行推进。
- 修复后重跑 30 分钟验收，再补跑 60 分钟终验。
- 远端 CI 继续延后，不 push。

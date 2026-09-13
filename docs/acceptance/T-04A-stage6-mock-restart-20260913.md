# T-04A Stage 6 Mock 长跑与重启一致性验收

- 验收日期：2026-09-13
- 基线提交：`0b53770`
- 验收环境：Windows、Python `3.13.0`、uv `0.12.12`、隔离 mock OneBot
- mock 长跑时长：`1807.00` 秒，约 `30.12` 分钟
- 开始时间：2026-09-13 15:58:49（Asia/Shanghai）
- 结束时间：2026-09-13 16:28:56（Asia/Shanghai）
- 验收结论：`未通过`。R06、R07 失败，60 分钟终验未执行。

## 一、隔离与命令

- 未连接真实 NapCat 或真实 QQ 群。
- 使用随机本机端口：HTTP `51869`、WS `51870`、控制台 `51871`。
- 使用隔离目录：`.pytest-tmp/stage6-20260913-30m/`。
- 数据库：`.pytest-tmp/stage6-20260913-30m/state/qqf.db`。
- 长跑命令：

```text
.venv\Scripts\python.exe scripts\stage6_acceptance.py --duration-minutes 30 --report-dir .pytest-tmp\stage6-20260913-30m
```

- 本次历史运行由退出码修正前的驱动执行，原退出码为 `0`，但 S6-R06、S6-R07 已失败。
- 当前驱动已修正为：任一必测项失败时退出码为 `1`，全部通过时才为 `0`。

## 二、长跑摘要

| 指标 | 结果 |
| --- | --- |
| mock 连接次数 | 2 |
| 固定场景事件 | 每轮 M01 至 M09 各 1 次，共 20 次 |
| 周期消息 M10 | 33 次 |
| mock action | 15 次 |
| wake_log | 100 条 |
| `wake` 决策 | 40 次 |
| `deferred` 决策 | 60 次 |
| 最高活跃会话 | 1 |
| 未处理 Traceback | 0 |
| ERROR 日志 | 0 |
| SQLite `integrity_check` | 重启前、启动后、最终均为 `ok` |
| 停止后 WAL/SHM | 均不存在 |

数据库最终状态：

- 会话数：2，分别为群聊与私聊。
- 记忆记录：2 条。
- 学习笔记：1 条。
- observation：21 条。
- 每日发送总数：5。
- 表情使用计数：2。
- 审计记录：53 条。
- conversation：28 条消息，9 组 tool call 与 9 个 tool result 完整配对，无孤立 tool ID。

原始证据：

- `.pytest-tmp/stage6-20260913-30m/summary.json`
- `.pytest-tmp/stage6-20260913-30m/mock-run1.json`
- `.pytest-tmp/stage6-20260913-30m/mock-run2.json`
- `.pytest-tmp/stage6-20260913-30m/qqf-run1.log`
- `.pytest-tmp/stage6-20260913-30m/qqf-run2.log`
- SHA-256（数据库）：`B2336265BBB6251C7BE691190DC87A2FCEE116AD161F9F07799BC047A6CB0864`
- SHA-256（摘要）：`C7835227DA24FB8EFEB3EB5603B50A48BAEA4327CAAE1AAB2693E11CFD9C5EBD`

## 三、Mock 长跑矩阵

| 编号 | 结果 | 证据与说明 |
| --- | --- | --- |
| S6-M01 普通消息 | 通过 | M01 和 M10 被接收，产生 normal/scheduled wake_log；无双进程崩溃或永久 ACTIVE |
| S6-M02 @ 与直接触发 | 通过 | M02 mention、M03 poke、M06/M09 keyword 均被正确分类并写入 wake_log |
| S6-M03 拍一拍 | 通过 | 两轮 M03 均归一化为 `category=poke` |
| S6-M04 潜水 | 通过 | `qq_dive` 工具链执行，最终群会话状态为 `diving`，存在 `next_wake_at` |
| S6-M05 记忆 | 通过 | 写入 `active_topic`，重启前、启动后记录逐行一致，后续 prompt 快照可见 |
| S6-M06 表情 | 通过 | 白名单 `stage6-01` 使用计数为 2，存在 `sticker_send` 审计和 mock 图片发送 action |
| S6-M07 限流 | 通过 | 出现 60 条 `deferred` wake_log，7 条发送因频率限制被 blocked |
| S6-M08 defer | 通过 | 第一轮出现 deferred keyword 后以相同关键词上下文再次唤醒；上下文未在单进程内退化为无事件定时唤醒 |
| S6-M09 多轮工具 | 通过 | conversation 中 9 组 tool call/result 完整配对，无孤立 tool 消息或 API 400 类错误 |
| S6-M10 长时间稳定 | 通过 | 30 分钟运行无崩溃、无永久 ACTIVE、峰值并发为 1；跨重启重复发送单独在 R07 记失败 |

## 四、重启一致性矩阵

| 编号 | 结果 | 证据与说明 |
| --- | --- | --- |
| S6-R01 会话状态 | 通过 | 重启前后 sessions 表逐行一致；最终群聊为 `diving`，私聊为 `idle` |
| S6-R02 会话历史 | 通过 | conversation 内容与顺序一致，tool call/result 完整，无截断 |
| S6-R03 长期记忆 | 通过 | memory_items 重启前后均为 2 条并逐行一致 |
| S6-R04 学习数据 | 通过 | learning_notes、learning_state、observations 重启前后逐行一致 |
| S6-R05 每日发送 | 通过 | daily_send_counts 在重启前后一致，重启本身未重置或即时重复累加 |
| S6-R06 待处理唤醒 | 失败 | `_pending_wakes` 仅保存在内存，重启后原 category/reason/events/defer flags 丢失，见缺陷 T-19 |
| S6-R07 去重 | 失败 | 第二轮重放相同 event/message ID 后再次执行 mention 与 sticker，见缺陷 T-20 |
| S6-R08 SQLite 完整性 | 通过 | 三次 `integrity_check` 均为 `ok`，停止后 WAL/SHM 均不存在 |

## 五、失败项

### T-19：待处理唤醒未持久化

- 现象：第一轮中大量 deferred wake 的上下文只存在于 `Scheduler._pending_wakes`。
- 问题：重启后没有数据库表或字段恢复 category、reason、events、defer flags。
- 复现：30 分钟运行在 900 秒处受控重启；重启后先出现新的 normal wake，再进入默认 scheduled wake，未恢复第一轮 deferred 上下文。
- 原始证据：`wake_log` 第 45 至 51 行、`qqf-run1.log`、`qqf-run2.log`。
- 可能根因：`app/runtime/scheduler.py` 的 `_pending_wakes` 是纯内存字典。
- 建议：T-19 持久化 pending wake，并在启动时恢复。

### T-20：去重指纹未跨重启保存

- 现象：第二轮 mock 重放完全相同的 message ID。
- 问题：第一轮已处理的 M02 mention 和 M06 sticker 在重启后再次执行。
- 证据：
  - 两轮 mock report 均出现 M02、M06。
  - mock action 中重复出现 message ID `1002` 的群消息和 message ID `1004` 的表情发送。
  - 群聊 daily_send_counts 最终为 4，表情 usage_count 最终为 2。
- 可能根因：`MessageDedupe._seen` 是纯内存字典，没有 event/message 指纹持久化。
- 建议：T-20 持久化已处理指纹，并设置与 TTL 一致的清理策略。

### 验收驱动退出码修正

- 旧驱动无论检查结果如何都返回 `0`，不符合 T-04A 和 T-19/T-20 交办要求。
- 新驱动在 `summary.json` 中写入 `overall_pass`、`failed_checks` 和每项检查结果。
- 新驱动仅在全部必测项通过时返回 `0`；失败、未执行或证据缺失时返回 `1`。
- 故意失败 smoke：

```text
.venv\Scripts\python.exe scripts\stage6_acceptance.py --duration-minutes 0.6 --scenario-interval 1 --report-dir .pytest-tmp\stage6-intentional-failure
```

结果：

```text
exit code: 1
overall_pass: false
failed_checks: S6-M06, S6-M07, S6-M08, S6-R06, S6-R07
```

证据位置：

- `.pytest-tmp/stage6-intentional-failure/summary.json`

## 六、未执行项

| 项目 | 状态 | 原因 |
| --- | --- | --- |
| 60 分钟最终长跑 | 未执行 | 30 分钟已发现 T-19、T-20 阻塞项，应在业务修复后重跑 |
| T-04B 真实协议矩阵 | 未执行 | 属于独立任务；不受 mock 长跑失败阻塞，可在隔离真实环境并行执行 |

## 七、对 T-04B 的影响

- T-04B 可以并行开展，真实协议验收不依赖 mock 重启修复。
- T-04 最终关闭被 T-19、T-20 阻塞。
- T-04 只有在修复后重跑 30 分钟、并补跑 60 分钟全部通过后，才能标记完成。

## 八、门禁结果

```text
.venv\Scripts\python.exe -m pytest -q
结果：173 passed, 1 warning

.venv\Scripts\python.exe -m ruff check .
结果：All checks passed!

node .pytest-tmp\package\index.js --pythonpath .venv\Scripts\python.exe
结果：0 errors, 0 warnings, 0 informations
```

本记录只使用测试群 `100000`、隔离端口和临时数据库，未写入真实群号、Token、模型 Key 或真实私聊内容。

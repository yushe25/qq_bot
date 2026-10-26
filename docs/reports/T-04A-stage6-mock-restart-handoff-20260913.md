# T-04A Stage 6 Mock 长跑与重启一致性验收交办单

- 发布日期：2026-09-13
- 基线提交：`0b53770`
- 前置报告：`docs/reports/engineer-completion-report-20260913.md`
- 任务性质：验收、证据收集、缺陷定位；不新增业务功能
- 后续任务：T-04B 真实 NapCat 协议矩阵验收

## 一、任务目标

完成 Stage 6 中尚未完成的 mock 稳定性与重启一致性验收，回答以下问题：

1. QQF 在 30 至 60 分钟持续运行中，是否会出现未处理异常、会话卡死、重复发送或状态漂移。
2. 消息、@、拍一拍、潜水、记忆写入、表情发送、限流、defer 和多轮工具调用是否都真实发生并可观测。
3. 在受控停止并重启后，会话状态、会话历史、记忆、学习笔记、每日发送计数和待处理唤醒是否保持一致。

本任务只负责把事实验证清楚，并产出可复查证据。发现缺陷时更新缺陷清单；除验收脚本或测试自身的明显错误外，不顺手修复业务代码。业务修复另开任务，避免验收任务无限扩张。

## 二、不在本任务范围内

- 不实现 T-05 版本化迁移、自动备份和恢复演练。
- 不处理 T-08 控制台 Token 鉴权加固。
- 不实现 T-09 至 T-12。
- 不修改真实 QQ 群配置，不提交 `config.json`、真实群号、Token、模型 Key 或 `state/`。
- 不把远端 CI 未执行误报为通过。
- 不用现有单元测试替代 30 至 60 分钟持续运行证据。

## 三、执行前准备

1. 从 `0b53770` 开始，确认工作区除已存在的未跟踪报告外无额外改动。
2. 使用隔离目录运行验收，例如 `.pytest-tmp/stage6-20260913/`；数据库、日志和验收产物不得写入 tracked 的 `state/`。
3. mock 长跑使用 `llm.provider=mock`，仅允许测试群 `100000`，不连接真实 NapCat。
4. 记录运行环境、Python 版本、启动命令、配置文件摘要、开始时间和结束时间。
5. 长跑默认持续 30 分钟；项目方要求最终验收时执行 60 分钟。若中途失败，保留日志和数据库，不覆盖重跑。
6. 不得在验收过程中删除数据库、缓存或日志来制造“通过”。

## 四、需要新增的验收工具

优先扩展现有文件，不另起一套运行时：

- `scripts/mock_qq_server.py`：增加可控的 Stage 6 事件序列、持续时间、随机种子和结束报告能力。
- `scripts/stage6_acceptance.py`：新增独立验收驱动，负责启动、持续投递、等待收敛、受控停止、重启和状态比对。
- `tests/test_stage6_acceptance.py`：覆盖验收驱动的短时逻辑，保证长跑脚本本身正确。

如验收工具需要可控的模型决策，可在脚本内实现仅用于验收的 `Stage6Provider`，但不得改变生产 Provider 行为。验收 Provider 至少应能稳定产生以下结果：

- `memory_update + qq_send_group_message`。
- `qq_dive`。
- `sticker_send`。
- 至少两轮工具调用后结束。
- 一次触发限流或 defer。

## 五、Mock 长跑矩阵

| 编号 | 场景 | 最小操作 | 必须观察到的证据 | 通过标准 |
| --- | --- | --- | --- | --- |
| S6-M01 | 普通消息 | 周期性发送普通文本 | 收到事件、唤醒日志、最终 `wake/no_action/deferred` 之一 | 无未处理异常，状态最终离开 `ACTIVE` |
| S6-M02 | @ 与直接触发 | 向 bot 发送 `@`、昵称、提问和关键词 | `TriggerCategory`、wake_log、出站动作或明确拒绝 | 触发归类正确，直接触发不静默丢失 |
| S6-M03 | 拍一拍 | 发送 `notice/notify/poke` | 归一化事件和 wake_log | 事件被识别为 poke，不会导致进程异常 |
| S6-M04 | 潜水 | 调用 `qq_dive` | `SessionState.DIVING`、`next_wake_at`、`WakeOutcome.dived=true` | 潜水后不发送文字或表情，后续能被再次调度 |
| S6-M05 | 记忆 | 调用 `memory_update` | `memory_items` 新记录和后续 snapshot 注入 | 重启后记录仍在，内容未被重复写入 |
| S6-M06 | 表情 | 调用 `sticker_send` | sticker 使用计数、安全审计、mock 出站记录 | 只发送白名单文件，计入限流和每日计数 |
| S6-M07 | 限流 | 在窗口内制造超过上限的唤醒或发送 | `deferred/rate_limited` wake_log 或 blocked 审计 | 超限不发送，恢复容量后可继续 |
| S6-M08 | defer | 制造会话冷却、全局并发或作息延后 | `next_wake_at` 和待执行上下文 | 延后到期后按原上下文执行，不重复执行 |
| S6-M09 | 多轮工具 | 一轮内执行至少两次工具调用 | 完整 tool call/result 配对、最终文本 | 不出现孤立 tool 消息或 API 400 类错误 |
| S6-M10 | 长时间稳定 | 连续运行 30 分钟，最终版本 60 分钟 | 运行日志、DB、进度快照、结束摘要 | 无进程崩溃、无会话永久停留 `ACTIVE`、无重复发送 |

## 六、重启一致性矩阵

执行顺序固定为：启动 -> 注入场景 -> 等待状态落盘 -> 优雅停止 -> 查询并保存停止前快照 -> 使用同一数据库重启 -> 查询并保存启动后快照 -> 逐项比较。

| 编号 | 状态 | 停止前证据 | 重启后通过标准 |
| --- | --- | --- | --- |
| S6-R01 | 会话状态 | `sessions.state`、`next_wake_at`、`cooldown_until` | 非瞬态状态保持一致；`ACTIVE/WAKING` 重启后恢复为 `IDLE` 并被记录 |
| S6-R02 | 会话历史 | `conversation`、`pending_output` | 内容和顺序一致，tool 调用/结果没有被截断 |
| S6-R03 | 长期记忆 | `memory_items` | 记录数量、key、value、importance、expires_at 一致 |
| S6-R04 | 学习数据 | `learning_notes`、`learning_state`、`observations` | 已落盘数据一致，未落盘的观察必须明确说明 |
| S6-R05 | 每日发送 | `daily_send_counts` | 日期、计数一致，重启不重置、不重复累加 |
| S6-R06 | 待处理唤醒 | pending wake 的 category、reason、events、defer flags | 重启后可继续原任务，而不是退化成无上下文定时唤醒 |
| S6-R07 | 去重 | event/message 指纹 | 重启后重复投递同一事件不会再次执行 |
| S6-R08 | SQLite 完整性 | WAL/SHM、数据库文件 | 受控停止后连接关闭；`PRAGMA integrity_check` 返回 `ok` |

## 七、验收产物

1. `docs/acceptance/T-04A-stage6-mock-restart-20260913.md`
   - 必须逐项列出 S6-M01 至 S6-M10、S6-R01 至 S6-R08 的结果。
   - 每项只能标记为 `通过`、`失败`、`未执行`，不得使用“基本通过”。
   - 失败项必须包含：现象、复现步骤、原始日志位置、数据库证据、可能根因、建议任务编号。
2. 长跑摘要 JSON 或 Markdown。
   - 至少包含开始/结束时间、运行时长、事件数、wake 数、发送数、拒绝数、限流数、最高并发数、重启前后差异。
3. 原始日志和数据库。
   - 放在 gitignored 的 `.pytest-tmp/stage6-20260913/` 下。
   - 报告中只引用脱敏后的必要片段，不抄写真实群号、Token、模型 Key 或私聊内容。
4. 代码变更。
   - 仅限验收脚本、测试和发现的验收工具缺陷。
   - 每个改动附对应命令和结果；不得混入 T-05 或其他业务功能。

## 八、执行命令与门禁

验收前后至少执行：

```text
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
node .pytest-tmp\package\index.js --pythonpath .venv\Scripts\python.exe
```

长跑结束后额外执行：

```text
.venv\Scripts\python.exe scripts\stage6_acceptance.py --duration-minutes 30 --report-dir .pytest-tmp\stage6-20260913
```

最终验收时把 `30` 改为 `60`。验收脚本退出码必须为 `0` 才能宣称 mock 长跑通过；数据库比对失败或存在未解释差异时必须返回非零退出码。

## 九、完成定义

满足以下全部条件才算 T-04A 完成：

- 30 分钟 mock 长跑完成并生成证据；最终验收版本补跑 60 分钟。
- S6-M01 至 S6-M10 全部明确标记结果。
- S6-R01 至 S6-R08 全部明确标记结果，且关键状态有重启前后对照。
- 基础门禁仍为 pytest、ruff、pyright 全绿。
- 没有未提交密钥、真实群数据、`state/` 数据或无关改动。
- 验收报告明确列出失败项、风险、是否阻塞 T-04B 和对应后续任务。
- 未擅自修复业务缺陷；发现的问题已形成独立、可复现的缺陷记录。

## 十、风险与处理

- 30 分钟等待会造成反馈慢。处理方式：先用短时 smoke 验证脚本，再启动正式长跑；短时 smoke 不得替代正式结果。
- mock Provider 当前不能覆盖表情、限流和多轮工具。处理方式：使用验收专用 Provider；若仍无法稳定覆盖，不得伪造通过，应记录场景缺口。
- 待处理唤醒、去重和 inbox 可能只在内存中，重启后大概率出现一致性失败。处理方式：保留证据并记录根因，不在本任务中顺手扩成 T-05。
- 真实 NapCat 联调与 mock 验收混跑会干扰结果。处理方式：T-04A 全程禁用真实 QQ；T-04B 单独执行。

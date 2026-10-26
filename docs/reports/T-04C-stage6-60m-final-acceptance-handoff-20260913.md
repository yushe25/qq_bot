# T-04C Stage 6 60 分钟终验交办单

- 发布日期：2026-09-13
- 终验基线：`7ba13ac`
- 前置报告：`docs/reports/T-19-T-20-chief-designer-report-20260913.md`
- 前置验收：`docs/acceptance/T-04A-stage6-post-fix-20260913.md`
- 任务性质：冻结版本终验、证据归档、T-04A 关闭判定
- 并行任务：T-04B 真实 NapCat 协议矩阵验收
- 远端策略：继续不 push，不触发远端 CI

## 一、总设计师决定

1. T-19、T-20 修复完成，30 分钟复验全部通过，可以进入 60 分钟终验。
2. 下一步主任务为 T-04C：在 `7ba13ac` 上执行 60 分钟 Stage 6 终验。
3. 终验期间冻结代码。除验收脚本自身的致命执行错误外，不修改业务代码。
4. 若终验失败，只记录证据并登记新缺陷，不在原运行中热修或拼接结果。
5. T-04B 真实协议矩阵继续独立并行；T-04C 通过只能关闭 T-04A，不能单独关闭完整 T-04。
6. 远端 CI 继续延后。

## 二、任务目标

确认 QQF 在冻结基线上连续运行 60 分钟、完成一次受控重启后，仍满足 Stage 6 的全部 mock 稳定性与重启一致性要求。

本任务只回答一个问题：

> `7ba13ac` 是否可以作为 T-04A 的最终通过基线？

任何代码改动都会使本次结果失效，修复后必须从零重新运行完整 60 分钟。

## 三、执行前条件

1. 确认当前 HEAD 为 `7ba13ac`。
2. 确认没有未提交的 tracked 源码或配置改动。
3. 未跟踪的报告文档可以保留，但不得影响运行。
4. 确认 `config.json`、真实群号、Token、模型 Key 和 `state/` 不会进入提交或验收产物。
5. 确认真实 NapCat/QQ 进程与 mock 验收隔离，不能同时占用 mock 使用的端口或状态目录。
6. 使用新的空目录，不能复用 30 分钟运行的数据库：

```text
.pytest-tmp/stage6-20260913-60m-final/
```

7. 验收开始后不得修改代码、依赖、persona、配置或验收脚本。

## 四、执行命令

终验前门禁：

```text
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
node .pytest-tmp\package\index.js --pythonpath .venv\Scripts\python.exe
```

60 分钟终验：

```text
.venv\Scripts\python.exe scripts\stage6_acceptance.py --duration-minutes 60 --report-dir .pytest-tmp\stage6-20260913-60m-final
```

退出码必须为 `0`。不要只依据终端文本判断，必须同时检查 `summary.json`。

## 五、强制验收条件

### 5.1 运行完整性

- 实际运行时长不少于 3600 秒，记录时钟开始与结束。
- 在约 1800 秒处执行一次受控停止和同库重启。
- QQF 和 mock 子进程均正常退出，不遗留端口和进程。
- Traceback 为 `0`。
- ERROR 日志为 `0`。
- 无会话永久停留在 `ACTIVE`。
- 最高活跃会话不超过配置并发上限。

### 5.2 场景覆盖

以下项目必须全部通过：

```text
S6-M01 普通消息
S6-M02 @ 与直接触发
S6-M03 拍一拍
S6-M04 潜水
S6-M05 记忆
S6-M06 表情
S6-M07 限流
S6-M08 defer
S6-M09 多轮工具
S6-M10 长时间稳定
```

### 5.3 重启一致性

以下项目必须全部通过：

```text
S6-R01 会话状态
S6-R02 会话历史
S6-R03 长期记忆
S6-R04 学习数据
S6-R05 每日发送
S6-R06 待处理唤醒
S6-R07 去重
S6-R08 SQLite 完整性
```

停止后 `qqf.db-wal` 和 `qqf.db-shm` 必须消失；三次 `PRAGMA integrity_check` 必须均为 `ok`。

### 5.4 重放防护

第二轮 mock 重放相同 event/message ID 后：

- `action_count` 必须为 `0`。
- 不产生重复群消息。
- 不产生重复私聊消息。
- 不产生重复表情动作。
- `daily_send_counts` 不增加。
- `sticker_usage_counts` 不增加。
- `pending_wakes`、`event_dedupe` 重启前后逐行一致。

### 5.5 机器判定

`summary.json` 必须满足：

```text
overall_pass == true
failed_checks == []
```

仅当脚本退出码为 `0`、`overall_pass=true` 且必测项无 `未执行` 时，才能标记 T-04C 通过。

## 六、交付物

1. `docs/acceptance/T-04C-stage6-60m-final-20260913.md`
   - 写明基线提交、开始/结束时间、实际时长、命令和退出码。
   - 逐项列出 S6-M01 至 S6-M10、S6-R01 至 S6-R08。
   - 写明重启前后关键表差异。
   - 写明 T-04A 是否可关闭。
2. 原始证据：
   - `.pytest-tmp/stage6-20260913-60m-final/summary.json`
   - `mock-run1.json`
   - `mock-run2.json`
   - `qqf-run1.log`
   - `qqf-run2.log`
   - `state/qqf.db`
3. 摘要和数据库 SHA-256。
4. 全量门禁输出。
5. 更新 `docs/PROJECT_REPORT.md` 的 Stage 6 状态。

## 七、判定规则

### 通过

- T-04C 标记通过。
- T-04A 标记关闭。
- T-04 仍等待 T-04B；只有 T-04B 也通过后，完整 T-04 才能关闭。
- 后续主任务按优先级进入 T-05 或真实协议缺陷修复。

### 失败

- T-04C 标记失败，禁止标记“基本通过”。
- 保留完整日志、数据库和摘要，生成新缺陷 T-21 或后续编号。
- 不得在失败运行后热修再继续同一个数据库。
- 修复后从零运行完整 60 分钟，不复用失败运行的前半段。

## 八、并行 T-04B 边界

T-04B 使用独立分支或 worktree，不修改 T-04C 的运行目录和数据库。至少覆盖：

- HTTP `get_version_info`。
- 正向 WS 鉴权与生命周期事件。
- 真实群文本发送。
- 出站 @ 与引用回复。
- 表情或媒体发送。
- 私聊出站。
- WS 断开后的自动重连。
- 重连后不重复发送已确认动作。

T-04B 发现代码缺陷时登记新任务。T-04C 终验期间不合并 T-04B 代码修复，避免改变 `7ba13ac` 基线。

## 九、完成定义

- 60 分钟完整运行和受控重启全部完成。
- 脚本退出码为 `0` 且 `overall_pass=true`。
- S6-M01 至 S6-M10、S6-R01 至 S6-R08 无失败、无未执行。
- 门禁 pytest、ruff、pyright 全绿。
- 验收报告和原始证据完整。
- 未 push、未修改真实配置、未提交真实数据和 `state/`。

## 十、不需要项目方补充的信息

当前 60 分钟终验所需的环境、脚本、基线、判定规则和证据目录均已明确，无需项目方再提供技术信息。

# QQF 工程师完成情况汇报

- 汇报日期：2026-09-13
- 汇报对象：项目总工程师
- 汇报范围：T-01 至 T-03、T-01b、T-13 至 T-14、T-16 至 T-18 的已完成工作
- 当前基线：本地 `main`，最新提交 `0b53770`
- 总体结论：已完成的阻塞项和优化项均通过本地门禁；项目尚未完成 Stage 6 全量验收，远端 CI 按项目方决定延后。

## 一、总体状态

| 编号 | 任务 | 状态 | 提交 | 说明 |
| --- | --- | --- | --- | --- |
| T-01 | pytest 默认临时目录无权限 | 完成 | `0dc59a7` | 默认 pytest 不再触发白名单外的 Windows Temp ACL 错误 |
| T-02 | 批处理窗口口径不一致 | 完成 | `6ca5c5a` | 默认值统一为 `8000ms` |
| T-03 | 遗留进程处置 | 完成 | `7a3aa2c` | 先确认常驻；后续按项目方要求优雅停止 QQF，QQ 客户端保留 |
| T-01b | 清理残留 pytest 目录 | 完成 | 无仓库提交 | 两个目标目录均不存在，删除目标已被 `.gitignore` 覆盖 |
| T-13 | Pyright 与类型清理 | 完成 | `5c648c6` | Pyright basic 为 `0 errors`，测试中的 `type: ignore` 清零 |
| T-14 | CI 锁定安装 | 本地部分完成 | `0b53770` | CI 已改为锁定同步；远端 CI 按项目方决定延后 |
| T-16 | 控制台唤醒日志与结构化流 | 完成 | `ecedd05` | 已启用 `/api/wake-logs`，实时流按事件类型展示 |
| T-17 | 会话小时限流与去重口径 | 完成 | `d88dc88` | 增加会话小时桶并移除 fallback 去重的接收分钟分桶 |
| T-18 | 控制台无 token 语义文档 | 完成 | `1e20134` | README 与当前鉴权代码一致 |

辅助归档提交：

- `d5a6ea2`：T-01 至 T-03 实施报告。
- `2feb473`：T-01b 与 T-18 完成报告。

## 二、已完成功能

### 1. 测试与工程门禁

T-01 解决的问题：

- Windows 默认临时目录中的 pytest 目录存在 ACL 权限问题。
- 默认运行曾产生 81 个 fixture setup error。
- 现在 pytest 的 basetemp 和 cache 固定到项目内 `.pytest-tmp/` 下的可写目录。
- 其他既有测试产物未被误删。

T-01b 清理结果：

- `C:\Users\<user>\AppData\Local\Temp\pytest-of-<user>` 不存在。
- `<项目根>\.pytest_cache` 不存在。
- 没有为清理引入绕过开关或额外脚本。

T-13 结果：

- 使用锁定版本 `pyright 1.1.411`。
- Pyright basic 当前为 `0 errors, 0 warnings, 0 informations`。
- 已修复 Provider、OneBot WebSocket、工具返回结构、调度类型和测试 fixture 的类型问题。
- 仓库中已无 `type: ignore` 或 `pyright: ignore`。

### 2. 配置与运行口径

T-02 结果：

- 批处理窗口默认值统一为 `8000ms`。
- 代码、测试、README、示例配置、项目报告和 Token 预算契约口径一致。

T-03 结果：

- 初始核查确认 PID `13496` 为 QQF 项目进程，监听控制台 `3100` 并连接 OneBot `3001`。
- PID `33028` 为 QQ 客户端 NodeService，不属于 QQF 项目自身。
- 后续按项目方指令优雅停止 QQF、venv 启动器和 `start.ps1`。
- QQF 停止后 `3100` 已释放，`state/qqf.db-wal` 和 `state/qqf.db-shm` 不存在。
- QQ 客户端进程未被项目停机流程强制结束。

### 3. 控制台

T-18 结果：

- 补全 `console.token` 配置说明。
- 明确无 token 模式只允许回环地址访问。
- 明确非回环来源会返回 `401 loophole blocked`。
- 明确 token 模式支持 Bearer header 和 query token。
- 文档说明 query token 会有浏览器历史和日志泄漏风险，该加固仍属于 T-08。

T-16 结果：

- 控制台调用已有 `GET /api/wake-logs`。
- 支持按会话筛选唤醒日志和手动刷新。
- 展示触发类别、判定结果、原因、概率、下次唤醒时间和 Token 用量。
- WebSocket 实时流按唤醒、工具调用、模型错误、出站消息、会话状态等类型结构化展示。
- 不再直接输出原始 JSON。

### 4. 限流与去重

T-17 结果：

- `RateLimiter` 增加每会话小时桶。
- `allow`、`has_capacity`、`next_available_at` 和 `reset` 均覆盖会话小时窗口。
- dedupe fallback 键不再包含接收分钟。
- 同一逻辑事件跨分钟重复到达时仍可识别为重复。
- 相同文本但事件时间不同，不会被分钟分桶误杀。

### 5. CI 锁定安装

T-14 已完成的部分：

- CI 使用 `astral-sh/setup-uv@v6`。
- 固定 uv 版本为 `0.12.12`。
- 安装命令改为 `uv sync --locked --extra dev`。
- Ruff、Pyright、pytest 均通过 `uv run python -m ...` 执行。
- 本地 `uv lock --check` 成功，锁文件解析 `30 packages`。

T-14 未完成的部分：

- 仓库当前没有 remote。
- 本机未安装 GitHub CLI。
- Docker daemon 不可用。
- 项目方决定所有功能完成前不 push，因此 GitHub Actions 尚未真实触发。

## 三、验证证据

最终本地门禁：

```text
.venv\Scripts\python.exe -m pytest -q
结果：167 passed, 1 warning
```

```text
.venv\Scripts\python.exe -m ruff check .
结果：All checks passed!
```

```text
node .pytest-tmp\package\index.js --pythonpath .venv\Scripts\python.exe
结果：0 errors, 0 warnings, 0 informations
```

```text
uv lock --check --cache-dir .pytest-tmp\uv-cache
结果：Resolved 30 packages
```

唯一 pytest warning 为 FastAPI/Starlette TestClient 的上游弃用提示，不属于本次改动。

工作区状态：

```text
git status --short
?? QQF交办单-T01b与T18-20260911.md
?? docs/reports/engineer-completion-report-20260913.md
```

除甲方提供的未跟踪交办单和本报告文件外，没有未提交的源码、配置、密钥、日志或 `state/` 数据。

## 四、当前可交付能力

当前本地基线已经具备：

- 稳定的 pytest、Ruff、Pyright 本地门禁。
- 可重复的锁定依赖解析和 CI 配置。
- 与控制台鉴权代码一致的使用文档。
- 可观察的唤醒日志和结构化控制台实时流。
- 全局与会话的分钟、小时多层限流。
- 更明确的跨分钟 fallback 去重语义。
- 可持续维护的测试与文档口径。

当前仍不能对外声称：

- Stage 6 mock 30 至 60 分钟稳定性验收已完成。
- 重启后的全部状态一致性已经验证。
- 真实 QQ 的出站 @、引用、媒体、私聊和断线重连已经全部验收。
- GitHub Actions 已经在远端真实执行。

## 五、未完成项与风险

### 阻塞后续验收

| 编号 | 状态 | 原因 |
| --- | --- | --- |
| T-04 | 未完成 | 尚缺 mock 长跑、重启一致性和真实协议逐项验收 |
| T-05 | 未完成 | 版本化迁移、自动备份和恢复演练尚未实现 |
| T-15 | 阻塞 | 依赖 T-05 |

### 仍待实现

- T-08：控制台 WebSocket 和 query token 需要改为不带 URL token 的鉴权方式。
- T-09：记忆和自主学习需要一次真机可验证产出，依赖 T-04。
- T-10：将控制台操作从 Scheduler 中拆出。
- T-11：确认表情素材库目录，或按 D-04 明确停用。
- T-12：校准文档数字和行号，并增加一致性校验。

### 风险

- T-03 的原始 acceptance 文档记录了“常驻”决策，后续停止动作属于运行状态变更，不改变当时的代码验收结论。
- T-14 的远端 CI 未执行，目前只能确认配置和锁文件有效。
- 真实 OneBot 兼容性仍主要依赖受托环境的实测结果，不能让 mock 验收替代真实协议验收。

## 六、下一步建议

1. 先完成 T-04 Stage 6 验收，补齐 mock 长跑、重启一致性和真实协议结果。
2. 实现 T-05，随后完成 T-15 的 WAL checkpoint 策略。
3. 处理 T-08，消除 token 进入 URL 的泄漏面。
4. 再推进 T-09 至 T-12，最后统一配置远端并触发一次真实 CI。

## 七、提交记录

```text
0b53770 chore(T-14): CI 改用 uv 锁定安装
5c648c6 fix(T-13): 跑通 Pyright basic 并清理类型错误
ecedd05 fix(T-16): 启用唤醒日志和结构化实时流
d88dc88 fix(T-17): 补会话小时限流并修正去重口径
2feb473 docs: 汇总 T-01b 与 T-18 完成报告
1e20134 docs(T-18): 说明控制台无 token 访问语义
d5a6ea2 docs: 汇总 T-01 至 T-03 实施报告
7a3aa2c docs(T-03): 确认运行进程常驻状态
6ca5c5a fix(T-02): 统一批处理窗口默认值
0dc59a7 fix(T-01): 固定可写 pytest 临时目录
```

本报告只记录已经验证的本地结果，不将远端 CI、Stage 6 长跑或真实协议未完成部分计入完成项。

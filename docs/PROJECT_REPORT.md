# QQF 项目现况与优化方向报告

- 报告日期：2026-09-10；P0 修复完成日期：2026-09-08；Stage 1/2/3a/3b 完成日期：2026-09-09；Stage 3c/4/5 完成日期：2026-09-10
- 评估范围：`new-bridge/`（QQF）。不含同级 `qq-bridge-main/`（Node.js + DSH 旧版）
- 评估方式：全量代码走读、测试与 Lint 执行、mock 全链路演示观测（约 47 分钟运行）

## 一、项目概述

QQF（QQ Friend Simulator）是一个自托管的 QQ 仿真群友运行时：

- **定位**：把任意 OpenAI 兼容 LLM 变成“会潜水、会接梗、有记忆”的群成员，而不是收到消息就回的客服机器人。
- **协议**：OneBot v11（NapCat / Lagrange 等实现均可接入；正向 WS 收事件 + HTTP 发动作）。
- **技术栈**：Python 3.12+、FastAPI、httpx、websockets、pydantic v2、SQLite（WAL）。
- **与旧版关系**：旧版 qq-bridge 绑定 DSH；QQF 是去平台化的独立重写，模型侧只要求 OpenAI 兼容 `/chat/completions`。

## 二、架构与模块

```text
OneBot v11（WS 事件 + HTTP 动作）
    │
    ▼
platform/qq          事件标准化（消息/拍一拍）+ 网关客户端（自动重连）
    │
    ▼
runtime/scheduler    8 秒批处理 + 触发分类 + 唤醒决策 + 唤醒限流
    │
    ▼
runtime/orchestrator 会话锁 + LLM 工具循环（≤6 步）+ 出站安全管线
    │
    ▼
agent/providers      mock / openai-compatible / multi-provider router
agent/tools          9 个工具（历史/成员/发送/群回复/私聊/等待/潜水/记忆/表情）
    │
    ▼
storage(SQLite)      会话 / 记忆 / 唤醒日志 / 审计
console              FastAPI API + WS 实时流 + 单页控制台
```

规模：45 个 Python 模块（含 `__init__`），连同测试、脚本、控制台页面约 4000 行。

## 三、项目现况

### 3.1 代码与测试

| 项 | 结果 |
| --- | --- |
| 测试 | 18 个测试目录 Python 文件、127 个用例，全部通过（`pytest -q`） |
| Lint | `ruff check .` 全过 |
| 类型检查 | pyright 已声明在 dev 依赖，本机未安装，未执行 |
| 版本控制 | 已 `git init`；`.gitignore` 拦截配置、状态库、`.env`、`.venv` |
| 依赖管理 | `uv.lock` 已生成 |
| 部署物料 | `start.ps1`（UTF-8 + mock/真实模式）、GitHub Actions（pytest + ruff + pyright）已补齐 |

### 3.2 运行观测（mock 演示）

演示从 22:06 运行至 22:53 手动停止，链路整体可用：WS 事件 → 批处理 → 分类 → mock LLM → 工具调用 → 出站安全管线 → 控制台实时流。运行期间暴露两个实际问题：

1. 会话在 22:26 后进入**限流 defer 循环**：每 30 秒一条“限流延时重试”日志并写一条 wake_log，持续 27 分钟未自愈（详见问题 1）。
2. 日志中文在重定向下乱码（Windows 控制台编码问题）。

停止时状态库数据：wake_log 379 行（deferred 223、wake 137、no_action 22）、审计 164 行、记忆 0 行。记忆链路从未被真实演练（mock provider 不调用 `memory_update`）。

### 3.3 功能完成度

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| OneBot v11 事件接入 | 可用 | 消息 + 拍一拍；断线自动重连、指数退避 |
| 批处理调度 | 可用 | 8 秒窗口，20 条强制 flush |
| 触发分类 | 可用 | @ > 昵称 > 提问 > 关键词 > 拍一拍 > 概率唤醒 |
| LLM 工具循环 | 可用 | 最多 6 步；mock 全链路验证通过 |
| 出站安全管线 | 可用 | 白名单默认拒绝 + 双窗口限流 + 内容过滤 + 审计 |
| 持久化 | 可用 | SQLite WAL，异步封装，单实例锁 |
| Web 控制台 | 可用 | 本机与 token 模式均可访问（问题 4 已修复） |
| 记忆 | 可用 | 三类 kind 白名单、分类型过期、时间衰减、快照条数与单条长度限制、mock 写入与 prompt 注入已覆盖 |
| 群历史/成员工具 | 可用 | 返回逐行可读摘要，不再 300 字符截断（问题 2 已修复） |
| 表情发送 | 可用 | 本地白名单表情库 + `sticker_send` + 使用计数（问题 5 已修复） |
| 媒体理解 | 可用 | 入站图片/表情/语音/文件/转发摘要为结构化文本（问题 7 已修复） |
| 作息节奏 | 可用 | 活跃/安静窗口、随机延迟、表情概率、每日发送上限，契约标记为 implemented |
| 自主学习 | 可用 | 观察、学习任务、严格 JSON、置信度/敏感过滤、持久化、过期、prompt 注入和控制台删除链路已实现；契约标记为 implemented |
| 私聊 | 可用 | owner/当前私聊会话工具、白名单、内容过滤、限流、每日上限与审计链路已覆盖 |
| Token 预算 | 可用 | 唤醒降到最近 12 条、历史最多 28 条、工具历史默认 10 条、最终步骤移除工具 schema；结果与消息限长、重复内容压缩、usage 写入 `wake_log` |
| 国内模型 Provider | 可用 | 显式注册 DeepSeek、智谱、通义、Moonshot、硅基流动等 OpenAI-compatible 服务；主备单次降级、错误归一化、环境变量 Key 与旧扁平配置兼容 |
| 工程化 | 已补齐 | git / CI / 锁文件 / 启动脚本 / UTF-8 日志（问题 9/10/11/13 已修复） |

**总体判断**：架构与安全设计质量较高；Stage 1/2/3a/3b/3c/4/5 与国内 Provider 路由已通过 pytest 与 ruff。NapCat HTTP 动作、正向 WS 连通性、真实群消息接收和文本发送已完成受控验收；当前仍不能声称可长期接入真实 QQ：出站 @/引用、媒体动作、断线重连和 30～60 分钟 mock 重启一致性验收尚未完成。

## 四、问题清单（按优先级）

### P0 已修复（2026-09-08）

1. **限流 defer 循环**（`app/runtime/scheduler.py:224`）
   唤醒限流达到窗口上限后固定每 30 秒 defer 重试，而限流窗口最长 1 小时才释放。已修复：`RateLimiter.next_available_at()` 返回综合全局分钟、全局小时、会话分钟三个队列的下一次可用时间，`_defer` 按该时间对齐，不再每 30 秒刷日志/写库。

2. **工具结果 300 字符截断**（`app/runtime/orchestrator.py:224`）
   `_tool_result_text` 把 data JSON 截到 300 字符，历史/成员数据给模型的是残缺 JSON。已修复：`qq_get_group_history` 输出 `[message_id] 昵称(user_id): 文本` 逐行摘要，`qq_get_members` 输出 `昵称(user_id) [role]`，通用工具 JSON 上限现由 `tokenBudget.toolResultChars` 控制并默认截到 1600 字符。

3. **限流 defer 丢失唤醒上下文**（`app/runtime/scheduler.py:255`）
   @ 提及被 defer 后重试时变成无事件的“定时唤醒”。已修复：Scheduler 增加 `_pending_wakes`，全局并发/冷却/限流三处 defer 均保存原 `WakeInput`，到期 tick 优先消费 pending 唤醒，保留触发类别与 reason。

4. **控制台 token 模式不可用**（`app/console/router.py:139`）
   设置 token 后页面与 API 均要求 Bearer header，浏览器无法直接打开。已修复：`GET /` 与 REST API 支持 `?token=` 或 `Authorization: Bearer`；控制台页读取 URL/sessionStorage 中的 token，fetch 统一附加 Bearer，WS 连接带 `?token=`，并加 `no-referrer` 防泄漏。

### P1 功能缺口

5. ~~**sticker_send 空壳**~~：已实现白名单表情库、路径/MediaGuard 校验、真实发送与 usage_count（2026-09-09）。
6. ~~**dive 中断批次破坏对话历史**~~：已为未执行调用补“已取消”tool result（2026-09-09）。
7. ~~**入站媒体不可见**~~：非文本 segment 已摘要为 `[图片]`/`[表情]`/`[语音]`/`[文件]` 再进入 prompt（2026-09-09）。
8. ~~**私聊工具残缺**~~：已补 `qq_send_private_message`，限制为当前私聊会话或 `ownerQq`，并复用完整安全管线（2026-09-10）。

### P2 工程化

9. ~~无版本控制~~：已 `git init`；首次提交仍由项目方决定，当前仓库保持未提交状态。
10. ~~无启动脚本 / CI~~：已补 `start.ps1`（mock/真实两种模式，`PYTHONUTF8=1`）与 GitHub Actions（pytest + ruff + pyright）。
11. ~~依赖浮版本~~：已生成 `uv.lock`。
12. pyright 声明未执行：安装并跑通 basic 级别，清理测试中的 `type: ignore`。
13. ~~日志乱码~~：`app/logging_setup.py` 显式 UTF-8 handler，启动脚本统一 `PYTHONUTF8=1`。
14. ~~**文档引用断链**~~：README 与 AGENTS.md 已收敛到本项目现有的设计契约和 ADR，不再依赖同级旧项目文档；`get_group_msg_history` 为 NapCat 扩展，Lagrange 兼容性仍需实测标注。

### P3 体验打磨

15. 发言冷却结束后不自排唤醒（`next_wake_at=0`），只有新消息才会再次触发，“主动冒泡”未闭环；冷却后应转 DIVING 并排下一次唤醒。
16. ~~`memory_update` 运行时不校验 kind；记忆链路无 mock/测试覆盖。~~ 已补齐 kind、过期、衰减、快照限制和安全拒绝用例（2026-09-10）。
17. 控制台已有 wake-logs API 但 UI 未使用；实时流直接展示原始 JSON。
18. RateLimiter 每会话缺小时桶；dedupe fallback key 按分钟分桶，可能误杀真实重复消息。

## 五、优化方向

### 当前阶段：Stage 5 已收尾，准备进入 Stage 6

Stage 1 工程化、Stage 2 稳健全链路、Stage 3a 表情媒体、Stage 3b 作息节奏、Stage 3c 自主学习、Stage 4 记忆与私聊、Stage 5 Token 优化均已完成并全绿。Stage 6 尚未开始。

### Stage 3c：自主学习

- **观察数据**：新增 `observations` 表，按成员隔离、bot 不采集、容量滚动淘汰。
- **学习任务**：独立于普通唤醒，按 `learning.interval_minutes` 与 `min_new_messages` 触发。
- **学习结果**：严格 JSON、kind 白名单、置信度阈值、ContentFilter 前置过滤。
- **控制台**：单条/按成员/清空会话三个删除粒度。
- **状态恢复**：观察、学习状态和笔记持久化到 SQLite；重启后继续沿用未过期笔记。

### Stage 4：记忆与 owner 私聊

- **记忆**：三类 kind 白名单、分类型过期、按更新时间衰减、稳定性排序、最多 12 条快照、单条 120 字符截断。
- **真实覆盖**：mock `@` 唤醒写入 `active_topic`，后续唤醒 invocation 可读取该快照。
- **私聊**：新增 `qq_send_private_message`；私聊会话只能发给当前用户，群聊只能发给 owner。
- **安全**：私聊继续执行白名单、内容过滤、分钟/小时限流、每日上限、单次唤醒上限和审计。

### Stage 5：Token 优化

- **少唤醒**：普通消息唤醒概率降到 0.04，批处理窗口调到 8000ms，发言冷却调到 90 秒。
- **压体积**：唤醒只注入最近 12 条事件，每条最多 160 字符；会话历史最多 28 条；群历史工具默认 10 条；单次工具结果最多 1600 字符；出站单条消息最多 200 字符；普通回复 `max_tokens=200`。
- **去重复**：连续相同消息合并为带次数的单行；同一唤醒内重复工具结果压缩为短提示，同时保留 `tool_call_id`。
- **工具层**：工具 schema 按稳定顺序生成；最后一次模型调用传空工具列表，不再重复携带全部定义。
- **观测**：OpenAI 兼容 provider 和 mock provider 的 prompt/completion/total usage 汇总写入最新一条 `wake_log`；写日志失败只审计错误，不阻断会话。
- **契约**：新增 `docs/design-contracts/token-budget.md` 并标记为 implemented。

### Stage 6：端到端验收

- **mock 稳定性**：连续运行 30～60 分钟，覆盖消息、@、拍一拍、潜水、记忆、表情、限流、defer 和多轮工具调用。
- **重启一致性**：确认调度状态、会话历史、记忆、学习笔记、每日发送计数和待处理唤醒在重启后可恢复。
- **真实协议**：NapCat 的 HTTP 动作、正向 WS、真实群收消息与文本发送已连通；继续完成出站 @/引用、表情/媒体发送、私聊与断线重连验收，再回填完整兼容性结论。

## 六、风险与依赖

- `get_group_msg_history` 是 NapCat 扩展动作，Lagrange / 其他实现未验证，接入前需实测。
- NapCat 已确认 HTTP 动作、正向 WS、真实群收消息和文本发送可用；出站 @/引用、媒体动作、私聊和长时间断线恢复仍未实测。
- OpenAI 兼容 API 对 tool_calls 的行为差异较大，问题 6 的现实触发率取决于模型是否会一次输出多个工具调用。
- Windows 控制台日志已统一 UTF-8；残留风险是容器/代理重定向环境下终端本身无法解码。
- 仓库已 `git init` 但尚无首次提交，所有改动仍是 untracked。项目方决定提交时机。

## 七、验证记录（2026-09-07）

- `pytest -q`：32 passed（P0 修复前基线）
- `ruff check .`：All checks passed
- `pyright`：未安装，未执行
- mock 演示：约 47 分钟，链路可用，暴露限流 defer 循环；两个后台进程已停止，端口 3000/3001/3100 已释放

## 八、验证记录（2026-09-08，P0 修复后）

- `pytest -q`：39 passed（新增 `RateLimiter.next_available_at`、scheduler pending wake、工具可读摘要、console token 鉴权共 7 个用例）
- `ruff check .`：All checks passed
- `pyright`：未安装，未执行
- 未启动 mock 后台进程，端口 3000/3001/3100 保持空闲

## 九、验证记录（2026-09-09，Stage 1/2/3a/3b）

- `pytest -q`：87 passed（新增多工具批次取消、LLM 重试审计、成员数 fail-closed、每日发送熔断、表情/媒体、作息慢响应、reset-daily-sends 等用例）
- `ruff check .`：All checks passed
- `pyright`：未安装，未执行
- mock 端到端：尚未执行 30～60 分钟连续验收，Stage 6 补做

## 十、验证记录（2026-09-10，Stage 3c）

- `pytest -q`：100 passed（新增学习观察、触发、JSON 重试、失败审计、敏感/低置信度拒绝、过期与滚动淘汰、prompt 注入、成员数据删除和控制台删除端点等用例）
- `ruff check .`：All checks passed
- `pyright`：未安装，未执行
- mock 端到端：尚未执行 30～60 分钟连续验收，Stage 6 补做

## 十一、验证记录（2026-09-10，Stage 4）

- `pytest -q`：112 passed，1 个 FastAPI TestClient 上游弃用警告
- `ruff check .`：All checks passed
- `pyright`：`.venv` 中未安装，当前无法执行
- 聚焦验收：记忆 kind 拒绝、过期剔除、时间衰减、快照 12 条/120 字符限制、mock 记忆写入与后续 prompt 注入、私聊工具路由、白名单/敏感内容/限流拒绝及审计均通过
- mock 端到端：尚未执行 30～60 分钟连续验收，Stage 6 补做

## 十二、验证记录（2026-09-10，Stage 5）

- `pytest -q`：127 passed，1 个 FastAPI TestClient 上游弃用警告
- `ruff check .`：All checks passed
- `pyright`：`.venv` 中未安装，当前无法执行；CI 已配置 pyright，待本地安装后补跑
- 配置校验：`config.example.json` 可解析；`llm.maxTokens=200`、`scheduler.batchWindowMs=8000`、`scheduler.wakeProbability=0.04`、`scheduler.cooldownSeconds=90`，`tokenBudget` 的 12/28/160/1600 与契约一致
- 聚焦验收：20 条事件只保留最近 12 条、连续 4 条相同消息合并、重复工具结果压缩且保留配对、最终模型步骤 tools 为空、历史不超过 28 条、mock/OpenAI usage 汇总写入 `wake_log`、usage 写库失败不阻断会话均通过
- mock 端到端：尚未执行 30～60 分钟连续验收，Stage 6 补做

## 十三、验证记录（2026-09-10，国内 Provider 路由与 NapCat 连通性）

- `pytest -q`：149 passed，1 个 FastAPI TestClient 上游弃用警告
- `ruff check .`：All checks passed
- `pyright`：`.venv` 中未安装，当前无法执行
- Provider 验收：DeepSeek、智谱、通义、Moonshot、硅基流动显式配置可解析；命名 Provider 缺失 Key、未知 fallback、自引用/重复 fallback、明文 Key、远端 HTTP 地址均被拒绝；主 Provider 成功不降级，限流/超时/不可用按顺序最多切换一次，鉴权/上下文/无效请求不切换
- 配置兼容：旧版扁平 OpenAI-compatible 配置可继续构建，本机 OpenCode 代理配置构建通过
- NapCat：`get_version_info` HTTP 200，返回 `NapCat.Onebot 4.18.19`、protocol v11；正向 WS 鉴权连接成功并收到生命周期事件
- 真实群文本链路：本地审计记录显示 1 个非 mock 群会话、1 次 `mention` 唤醒、5 次 `send_group_message` 安全放行，且没有发送失败记录；会话工具调用中未出现 `qq_reply` 或 `sticker_send`
- 尚未执行：出站 @/引用回复、表情/媒体发送、私聊出站、断线重连和 30～60 分钟重启一致性验收

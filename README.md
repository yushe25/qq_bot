# QQF

QQ Friend Simulator，一个自托管、不绑定单一模型平台，也不依赖 DSH / Hermes 的 QQ 仿真群友运行时。它把任意的 OpenAI 兼容 LLM 变成“会潜水、会接梗、有记忆”的群成员，而不是收到消息就回复的客服机器人。

Python 3.12+ 实现，无 Node 依赖。

## 快速开始（离线演示）

不需要真实 QQ 账号，也不需要真实模型，先在本地把整条链路跑起来：

```text
python scripts/mock_qq_server.py
python -m app.main --mock
```

打开 `http://127.0.0.1:3100` 即可看到控制台。mock OneBot 会定时向群 `100000` 推送消息，其中包含一次 `@`，调度器应唤醒 Mock LLM 并“发言”；未命中触发条件时会潜水并安排下一次唤醒。

演示默认使用这套配置：

```json
{
  "qq": {
    "wsUrl": "ws://127.0.0.1:3001",
    "httpUrl": "http://127.0.0.1:3000"
  },
  "llm": { "provider": "mock" },
  "safety": { "allowGroups": ["100000"], "allowPrivate": [] }
}
```

## 接入真实 QQ（OneBot v11）

QQ 侧使用 [OneBot v11] 协议，NapCat、Lagrange.OneBot 等实现均可：

1. 复制配置并修改：

   ```text
   Copy-Item config.example.json config.json
   ```

2. 在 `config.json` 中填入：

   ```json
   {
     "qq": {
       "wsUrl": "ws://127.0.0.1:3001",
       "httpUrl": "http://127.0.0.1:3000",
       "accessToken": "onebot-token",
       "ownerQq": "你的QQ号"
     },
     "llm": {
       "provider": "openai-compatible",
       "baseUrl": "http://127.0.0.1:8000/v1",
       "model": "qwen2.5-32b"
     },
     "safety": {
       "allowGroups": ["群号1"],
       "allowPrivate": []
     }
   }
   ```

3. 设置模型密钥并启动：

   ```text
   $env:QQF_ONEBOT_TOKEN = "onebot-token"
   $env:QQF_LLM_API_KEY = "sk-..."
   python -m app.main --config config.json
   ```

如果模型走本机 CC Switch / OpenCode Go 代理，把 `llm.baseUrl` 指向代理地址，并在 `llm.extraHeaders` 里配置 `x-opencode-session`。代理侧自行处理上游鉴权与模型路由，不要求 DeepSeek 官方 API key。

只有 `allowGroups` / `allowPrivate` / `ownerQq` 内的会话才会被处理，所有对外消息都会经过白名单、内容过滤、频率限制和审计。

接入前可以先单独验证 OneBot 的 HTTP 动作和正向 WS 是否可连：

```text
python scripts/onebot_check.py --ws-url ws://127.0.0.1:3001 --http-url http://127.0.0.1:3000
```

需要 Token 时，从环境变量读取，不要把明文写进 README 或提交进仓库：

```text
$env:QQF_ONEBOT_TOKEN = "onebot-token"
python scripts/onebot_check.py
```

如果本机 Python 缺少依赖，先执行 `python -m pip install -e .`（需要网络）。本项目不依赖 Node，也不会要求你接入任何 DSH / Hermes 平台。

## 调度与拟真逻辑

- 事件先进入 8 秒批处理窗口，合并成一次唤醒决策。
- 唤醒类型优先级：`@` 提及 > 昵称 > 提问 > 关键词 > 拍一拍 > 普通消息概率唤醒 > 定时唤醒。
- LLM 通过工具自主决定看群历史、查成员、发送、引用回复、私聊 owner、等待、潜水或更新记忆。
- 发言后进入 90 秒冷却；无行动时自动潜水，连续无行动会强制缩短下一次潜水间隔，避免“永眠”。
- 全局和按会话都有发送/唤醒频率上限，防止刷屏。
- 作息窗口来自 persona 的 `schedule`：活跃时段正常响应，安静时段普通消息排队到下一活跃窗口，owner/@/拍一拍走慢响应延迟。
- 每会话每日发送达到 `safety.maxSendsPerDay` 后熔断，只写审计；owner 可在控制台手动恢复。

## 表情与入站媒体

- 表情库使用本地目录 + `manifest.yaml` 白名单：只发送 `approved: true` 且通过 MediaGuard 校验的本地文件。
- 模型可调用 `sticker_send`，发送计入限流、每日上限、成员数护栏与审计；发送后 `usage_count` 递增。
- 入站图片、QQ 表情、语音、文件、转发卡片在归一化阶段摘要为 `[图片]`、`[表情:178:抠鼻]`、`[语音]`、`[文件:文件名]`、`[分享卡片]`，不携带原始文件内容。
- 禁止远程抓取、绝对路径逃逸和未知 id；路径类或文件大小/像素超限的请求直接拒绝。

## 自主学习

- 调度器按会话收集最近消息摘要；`learning.intervalMinutes` 到期且新增非 bot 消息达到 `learning.minNewMessages` 时，触发一次独立学习任务。
- 学习任务只允许写入 `speech_style`、`group_norm`、`topic_trend` 三类笔记；低置信度、敏感内容、CQ 码、密钥、路径和手机号会被拒绝并写审计。
- 笔记按会话持久化，默认最多 20 条、7 天过期，并作为“群内说话习惯”注入 system prompt。它只影响语气与表达，不能覆盖 persona 或安全规则。
- `learning.excludeMembers` 可按 QQ 号排除观察来源；控制台支持删除单条笔记、删除某成员相关数据和清空会话学习数据。

## 记忆与私聊

- `memory_update` 只接受 `active_topic`、`pending_thought`、`impression` 三类；按类型分别设置有效期，并按更新时间衰减排序。
- 记忆快照最多注入 12 条，每条 value 最多 120 字符；学习笔记仍放在独立“群内说话习惯”区块。
- `qq_send_private_message` 在当前私聊会话中只能回复当前用户，在群聊中只能向 `qq.ownerQq` 发送。
- 私聊发出前仍经过白名单、内容过滤、分钟/小时限流、每日上限、单次唤醒上限和审计；任一校验失败都不会发送。

## Token 预算

- 每次唤醒只注入最近 12 条事件，每条事件最多 160 字符；连续刷屏会压缩成带次数的单行。
- 会话历史默认保留 28 条消息，截断时保留完整 tool call/result 批次，避免 OpenAI 兼容 API 因消息配对不完整返回 400。
- 单个工具结果最多 1600 字符；同一唤醒内重复的工具结果只保留一条并标记后续结果已省略。
- 最后一次模型调用不携带工具定义；工具 schema 保持固定顺序，便于支持 prefix caching 的服务复用前缀。
- OpenAI 兼容 provider 返回的 prompt/completion/total token 会写入最新一条 `wake_log`；provider 不返回 usage 时记录 0。

## 配置项

| 配置 | 说明 | 默认值 |
| --- | --- | --- |
| `qq.wsUrl` | OneBot 正向 WS 地址 | `ws://127.0.0.1:3001` |
| `qq.httpUrl` | OneBot HTTP 动作地址 | `http://127.0.0.1:3000` |
| `qq.accessToken` | OneBot Authorization Bearer | 空 |
| `qq.ownerQq` | 允许的私聊所有者 | 空 |
| `llm.provider` | `mock` 或 `openai-compatible` | `openai-compatible` |
| `llm.baseUrl` | OpenAI 兼容 API 地址 | `http://127.0.0.1:8000/v1` |
| `llm.model` | 模型名 | `qwen2.5-32b` |
| `llm.extraHeaders` | 额外请求头，例如 CC Switch / OpenCode Go 的 `x-opencode-session` | `{}` |
| `llm.maxRetries` | LLM 请求失败后的最大重试次数 | `2` |
| `llm.maxTokens` | 单次模型调用输出上限 | `200` |
| `scheduler.batchWindowMs` | 群消息批处理窗口（毫秒） | `8000` |
| `scheduler.wakeProbability` | 普通消息唤醒概率 | `0.04` |
| `scheduler.names` | 能被昵称唤起的名字列表 | `[]` |
| `scheduler.keywords` | 关键词唤醒列表 | `[]` |
| `scheduler.maxWakesPerMinute` | 每分钟唤醒批次上限 | `4` |
| `scheduler.maxWakesPerHour` | 每小时唤醒批次上限 | `20` |
| `scheduler.cooldownSeconds` | 发言后的会话冷却时间（秒） | `90` |
| `tokenBudget.wakeMessages` | 单次唤醒注入的最近事件数 | `12` |
| `tokenBudget.historyMessages` | 持久化和发送给模型的会话消息上限 | `28` |
| `tokenBudget.eventTextChars` | 单条事件/历史工具消息的字符上限 | `160` |
| `tokenBudget.toolResultChars` | 单次工具结果字符上限 | `1600` |
| `learning.enabled` | 是否开启自主学习 | `true` |
| `learning.intervalMinutes` | 两次学习尝试的最小间隔（分钟） | `20` |
| `learning.minNewMessages` | 触发学习所需的新增非 bot 消息数 | `10` |
| `learning.confidenceThreshold` | 接受笔记的最低置信度 | `0.7` |
| `learning.maxNotesPerSession` | 每会话保留的学习笔记上限 | `20` |
| `learning.notesTtlDays` | 学习笔记有效期（天） | `7` |
| `learning.excludeMembers` | 不参与观察学习的 QQ 号 | `[]` |
| `safety.allowGroups` | 允许的群号列表 | `[]` |
| `safety.maxGroupMembers` | 群成员数上限；超限时只读不发言 | `20` |
| `safety.maxSendsPerMinute` | 每分钟发送上限 | `4` |
| `safety.maxSendsPerHour` | 每小时发送上限 | `60` |
| `safety.maxSendsPerWake` | 单次唤醒最多发送条数 | `3` |
| `safety.maxSendsPerDay` | 每会话每日发送上限，熔断后需控制台恢复 | `200` |
| `safety.maxMessageChars` | 单条出站文本最大字符数 | `200` |
| `console.host` / `console.port` | 控制台监听地址 | `127.0.0.1:3100` |
| `storage.databasePath` | SQLite 状态库路径 | `state/qqf.db` |
| `personaPath` | 人格 YAML 路径 | `personas/default.yaml` |

环境变量会被 JSON 配置覆盖逻辑合并：`QQF_LLM_API_KEY`、`QQF_ONEBOT_TOKEN`、`QQF_CONSOLE_TOKEN`、`QQF_DATA_DIR`。其中 `QQF_DATA_DIR` 会改变状态库目录。程序启动时会在状态目录创建 `qqf.lock`，重复启动会直接退出，避免两个实例同时写同一个 SQLite 库。

## 控制台

默认地址 `http://127.0.0.1:3100`：

- `GET /api/health`、`GET /api/sessions`、`GET /api/sessions/{id}`
- `POST /api/sessions/{id}/wake`、`/pause`、`/resume`、`/reset-memory`
- `POST /api/sessions/{id}/reset-daily-sends`：重置该会话的每日发送计数
- `DELETE /api/sessions/{id}/learning-notes?kind=&key=`：删除单条学习笔记
- `DELETE /api/sessions/{id}/learning-members/{member_id}`：删除某成员相关学习数据
- `DELETE /api/sessions/{id}/learning`：清空该会话的学习数据
- `GET /api/audit`、`GET /api/wake-logs`
- `WS /ws/live`：实时事件流

默认只允许本机访问；设置 `QQF_CONSOLE_TOKEN` 后，HTTP API 需要 `Authorization: Bearer <token>`。

## 目录结构

```text
app/
  agent/       LLM Provider 与工具执行
  console/     控制台 API、WebSocket 与页面
  domain/      会话、事件、唤醒等核心类型
  memory/      轻量记忆
  platform/    OneBot v11 网关与事件标准化
  runtime/     调度器、编排器、安全管线、会话管理
  safety/      白名单、频率限制、内容过滤、审计
  storage/     SQLite 状态存储
personas/      人格 YAML
scripts/       mock_qq_server.py 离线 OneBot 模拟器
                onebot_check.py OneBot v11 HTTP/WS 连通性检查
tests/         pytest 测试
```

## 测试

```text
python -m pytest
python -m ruff check .
```

## 与设计文档的关系

产品与技术设计见同级目录下的 `qq-bridge-main/docs/PRODUCT_DESIGN.md` 和 `qq-bridge-main/docs/TECHNICAL_DESIGN.md`；本目录是依照该方案实现的 Python MVP。

# QQF

QQ Friend Simulator，一个自托管、不绑定单一模型平台，也不依赖 DSH / Hermes 的 QQ 仿真群友运行时。它把任意的 OpenAI 兼容 LLM 变成“会潜水、会接梗、有记忆”的群成员，而不是收到消息就回复的客服机器人。

Python 3.12+ 实现，无 Node 依赖。

## 启动脚本

项目根目录的 `start.ps1` 是推荐的启动入口。脚本会自动切换到项目根目录，检查 Python 3.12+、运行依赖和配置文件，并使用虚拟环境中的 Python 启动 QQF。

首次使用先创建虚拟环境并安装依赖：

```powershell
uv sync --locked --extra dev
```

常用命令：

```powershell
# 离线 Mock 模式，不需要真实 QQ 和模型密钥
.\start.ps1 -Mock

# 使用指定配置启动真实 OneBot 模式
.\start.ps1 -Config config.json

# 只检查 Python、依赖和配置文件，不启动服务
.\start.ps1 -Config config.json -CheckOnly

# 使用指定虚拟环境
.\start.ps1 -VenvDir D:\venvs\qqf
```

参数说明：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-Mock` | 关闭 | 使用离线 Mock LLM 和 `scripts/mock_qq_server.py` |
| `-Config` | `config.json` | 配置文件路径，支持相对项目根目录或绝对路径 |
| `-VenvDir` | `.venv` | 虚拟环境目录，支持相对项目根目录或绝对路径 |
| `-CheckOnly` | 关闭 | 完成检查后退出，不启动长期运行的服务 |

Mock 模式找不到 `config.json` 时会使用 `config.example.json`；真实模式缺少指定配置时会直接报错。Mock 子进程会在主程序退出时自动清理，Python 的退出码也会原样返回给 PowerShell。

如果本机 PowerShell 禁止直接运行脚本，可以使用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Mock
```

## 快速开始（离线演示）

不需要真实 QQ 账号，也不需要真实模型，先在本地把整条链路跑起来：

```text
.\start.ps1 -Mock
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
       "provider": "deepseek",
       "fallbacks": ["zhipu"],
       "providers": {
         "deepseek": {
           "baseUrl": "https://api.deepseek.com/v1",
           "apiKeyEnv": "DEEPSEEK_API_KEY",
           "model": "deepseek-chat"
         },
         "zhipu": {
           "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
           "apiKeyEnv": "ZHIPU_API_KEY",
           "model": "glm-4-flash"
         }
       }
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
   $env:DEEPSEEK_API_KEY = "sk-..."
   $env:ZHIPU_API_KEY = "sk-..."
   .\start.ps1 -Config config.json
   ```

可在真正启动前只做环境和配置检查：

```text
.\start.ps1 -Config config.json -CheckOnly
```

`config.example.json` 已列出国内常见 Provider 模板：

| Provider ID | 默认模型示例 | API Key 环境变量 |
| --- | --- | --- |
| `deepseek` | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| `zhipu` | `glm-4-flash` | `ZHIPU_API_KEY` |
| `qwen` | `qwen-plus` | `DASHSCOPE_API_KEY` |
| `moonshot` | `moonshot-v1-8k` | `MOONSHOT_API_KEY` |
| `siliconflow` | `deepseek-ai/DeepSeek-V3` | `SILICONFLOW_API_KEY` |

Provider 的地址、模型名和环境变量均在 JSON 中显式配置，不作为代码内置默认值。只启动主
Provider 时只要求对应环境变量；加入 `fallbacks` 后，备用 Provider 的密钥也必须存在。
`RATE_LIMIT`、`TIMEOUT`、`PROVIDER_UNAVAILABLE` 会按 `fallbacks` 顺序降级一次，
鉴权、上下文超限和无效请求不会自动切换。

如果模型走本机 CC Switch / OpenCode Go 兼容代理，仍可使用旧的扁平配置：

```json
{
  "llm": {
    "provider": "openai-compatible",
    "baseUrl": "http://127.0.0.1:8000/v1",
    "model": "qwen2.5-32b",
    "extraHeaders": {"x-opencode-session": "your-session"}
  }
}
```

代理侧自行处理上游鉴权与模型路由，不要求官方 API key。

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
- 需要明确 @ 群友时，模型使用 `mentionUserIds` 提交成员 QQ 号；目标必须来自当前群成员列表，最多 5 人，非成员或查询失败时整条消息拒绝。
- 出站文本会按短句拆分并清理无关标点；字面 `@` 会保留，CQ 码仍由安全管线拒绝。
- 发言后进入 90 秒冷却；无行动时自动潜水，连续无行动会强制缩短下一次潜水间隔，避免“永眠”。
- 全局和按会话都有发送/唤醒频率上限，防止刷屏。
- 作息窗口来自 persona 的 `schedule`：活跃时段正常响应，安静时段普通消息排队到下一活跃窗口，owner/@/拍一拍走慢响应延迟。
- 每会话每日发送达到 `safety.maxSendsPerDay` 后熔断，只写审计；owner 可在控制台手动恢复。

## 表情与入站媒体

- 表情总开关 `sticker.enabled` 默认关闭；关闭时模型看不到 `sticker_send`，也不会发送表情。
- 表情库使用本地目录 + `manifest.yaml` 白名单：只发送 `approved: true` 且通过 MediaGuard 校验的本地文件。
- 模型读取受限语义目录后调用 `sticker_send`；每轮最多一张，可只发表情或文字加表情，没有合适素材时必须纯文字。
- 表情发送计入限流、每日上限、成员数护栏与审计；发送后 `usage_count` 递增。
- `sticker.autoTag` 使用独立视觉 Provider 自动生成名称、说明和标签；密钥只放环境变量，结果立即写入 manifest，重启后生效。
- persona `schedule` 中的 `sticker_only_probability`、`sticker_with_text_probability` 已 deprecated，仅保留解析兼容。
- 入站图片、QQ 表情、语音、文件、转发卡片在归一化阶段摘要为 `[图片]`、`[表情:178:抠鼻]`、`[语音]`、`[文件:文件名]`、`[分享卡片]`，不携带原始文件内容。
- 禁止远程抓取、绝对路径逃逸和未知 id；路径类或文件大小/像素超限的请求直接拒绝。

### 素材自动标注

自动标注使用独立的 `sticker.autoTag` Provider，不复用聊天模型密钥。密钥只放在 `apiKeyEnv` 指定的环境变量中；图片会先经过 MediaGuard，再发送给视觉模型。

```json
{
  "sticker": {
    "autoTag": {
      "enabled": true,
      "provider": "vision-main",
      "providers": {
        "vision-main": {
          "baseUrl": "https://example.invalid/v1",
          "apiKeyEnv": "QQF_VISION_API_KEY",
          "model": "vision-model"
        }
      }
    }
  }
}
```

```text
.venv\Scripts\python.exe scripts\tag_stickers.py --config config.json --id pilot-01
.venv\Scripts\python.exe scripts\tag_stickers.py --config config.json --all
.venv\Scripts\python.exe scripts\tag_stickers.py --config config.json --id pilot-01 --force
.venv\Scripts\python.exe scripts\tag_stickers.py --config config.json --id pilot-01 --dry-run
```

自动结果立即写入 `stickers/manifest.yaml`，并备份为 `manifest.yaml.bak`；YAML 注释不保证保留。`tag_source=manual` 的素材默认跳过，`--force` 可覆盖。修改 manifest 后需要重启 QQF，运行中的实例不会热加载。模型没有合适素材时应直接发纯文字，不随机补图。

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
| `qq.allowRemote` | 显式允许连接非回环 OneBot 地址；默认拒绝远端 NapCat | `false` |
| `llm.provider` | `mock`、旧的 `openai-compatible`，或 `llm.providers` 中的 Provider ID | `openai-compatible` |
| `llm.providers` | 命名 Provider 注册表；支持 DeepSeek、智谱、通义、Moonshot、硅基流动等 OpenAI 兼容服务 | `{}` |
| `llm.fallbacks` | 主 Provider 失败时按顺序尝试的备用 Provider ID | `[]` |
| `llm.allowFallback` | 是否启用自动降级 | `true` |
| `llm.baseUrl` / `llm.model` | 旧版扁平 OpenAI 兼容配置 | 本地地址 / `qwen2.5-32b` |
| `llm.extraHeaders` | 额外请求头，例如 CC Switch / OpenCode Go 的 `x-opencode-session` | `{}` |
| `llm.maxRetries` | 旧版扁平配置的 LLM 最大重试次数 | `2` |
| `llm.maxTokens` | 旧版扁平配置和 mock 的单次输出上限 | `200` |
| `scheduler.batchWindowMs` | 群消息批处理窗口（毫秒） | `8000` |
| `scheduler.wakeProbability` | 普通消息唤醒概率 | `0.04` |
| `scheduler.mentionDiveProbability` | 被 @ 或私聊时未回复直接潜水的概率 | `0.05` |
| `scheduler.names` | 能被昵称唤起的名字列表 | `[]` |
| `scheduler.keywords` | 关键词唤醒列表 | `[]` |
| `scheduler.maxWakesPerMinute` | 每分钟唤醒批次上限 | `4` |
| `scheduler.maxWakesPerHour` | 全局及每会话每小时唤醒批次上限 | `20` |
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
| `learning.maxOutputTokens` | 单次学习任务最大输出 Token；推理模型需要为最终 JSON 预留空间 | `1024` |
| `learning.reasoningEffort` | 学习请求的推理强度；结构化提取默认 `none`，不支持时设为 `null` | `none` |
| `learning.excludeMembers` | 不参与观察学习的 QQ 号 | `[]` |
| `safety.allowGroups` | 允许的群号列表 | `[]` |
| `safety.maxGroupMembers` | 群成员数上限；超限时只读不发言 | `20` |
| `safety.maxSendsPerMinute` | 每分钟发送上限 | `4` |
| `safety.maxSendsPerHour` | 全局及每会话每小时发送上限 | `60` |
| `safety.maxSendsPerWake` | 单次唤醒最多发送条数 | `3` |
| `safety.maxSendsPerDay` | 每会话每日发送上限，熔断后需控制台恢复 | `200` |
| `safety.maxMessageChars` | 单条出站文本最大字符数 | `200` |
| `console.host` / `console.port` | 控制台监听地址 | `127.0.0.1:3100` |
| `console.token` | 控制台访问 Token；为空时只放行回环地址来源 | 空 |
| `storage.databasePath` | SQLite 状态库路径 | `state/qqf.db` |
| `sticker.enabled` | 表情发送总开关；默认关闭 | `false` |
| `sticker.allowedIds` | 灰度期间允许发送的素材 ID；开启时必须非空 | `[]` |
| `sticker.autoTag.enabled` | 是否启用独立视觉模型自动标注 | `false` |
| `sticker.autoTag.provider` | 自动标注主 Provider ID | 空 |
| `sticker.autoTag.fallbacks` | 仅限流、超时、服务不可用时尝试的备用 Provider ID | `[]` |
| `sticker.autoTag.providers` | 独立视觉 Provider 注册表；API Key 只从 `apiKeyEnv` 读取 | `{}` |
| `personaPath` | 人格 YAML 路径 | `personas/default.yaml` |

环境变量会被 JSON 配置覆盖逻辑合并：`QQF_LLM_API_KEY`、`QQF_ONEBOT_TOKEN`、`QQF_CONSOLE_TOKEN`、`QQF_DATA_DIR`。命名 Provider 的 Key 由各自的 `apiKeyEnv` 指定，例如 `DEEPSEEK_API_KEY`；JSON 中的明文 `apiKey` 会被拒绝。`QQF_DATA_DIR` 会改变状态库目录。程序启动时会在状态目录创建 `qqf.lock`，重复启动会直接退出，避免两个实例同时写同一个 SQLite 库。

`qq.wsUrl` 和 `qq.httpUrl` 默认只允许 `127.0.0.1`、`localhost`、`::1`。连接非回环 NapCat 时必须显式设置 `qq.allowRemote=true`；启动时会记录远端风险 WARN。该开关不表示 Token 鉴权已经可靠，NapCat 错误 Token 可连接时仍应保持回环部署。

## 控制台

默认地址 `http://127.0.0.1:3100`：

- `GET /api/health`、`GET /api/sessions`、`GET /api/sessions/{id}`
- `POST /api/sessions/{id}/wake`、`/pause`、`/resume`、`/reset-memory`
- `POST /api/sessions/{id}/reset-daily-sends`：重置该会话的每日发送计数
- `DELETE /api/sessions/{id}/learning-notes?kind=&key=`：删除单条学习笔记
- `DELETE /api/sessions/{id}/learning-members/{member_id}`：删除某成员相关学习数据
- `DELETE /api/sessions/{id}/learning`：清空该会话的学习数据
- `GET /api/audit`、`GET /api/wake-logs`
- `GET /api/stickers/review?limit=10&cursor=`：跨白名单群分页汇总待审核素材；传入
  `groupId` 时只查看指定群
- `GET /api/stickers/{media_id}/preview`：通过 Bearer 鉴权读取素材预览
- `PATCH /api/stickers/{media_id}/metadata`：修改名称、说明、标签、情绪、适合场景和避免场景
- `POST /api/stickers/{media_id}/approve`、`/reject`：按 `(media_id, groupId)` 批准或拒绝
- `GET /api/logs?type=all&limit=20&cursor=`：合并分页查询唤醒日志与审计日志；`type` 可为
  `all`、`wake`、`audit`
- `POST /api/ws-ticket`：申请一次性 WebSocket ticket，有效期 60 秒且只能使用一次
- `WS /ws/live`：实时事件流

控制台页面按“顶部状态栏、统计卡片、会话列表与实时事件、素材审核工作台、日志时间线”
排列。素材审核默认跨群显示 10 条并滚动加载；审批后会保位刷新。图片预览通过带 Bearer
Header 的 `fetch` 转成 Object URL，长期 Token 不进入图片 URL。日志时间线默认显示 20 条，
可在全部、唤醒日志和审计日志之间切换。

默认只允许本机访问。未配置 `console.token` 时，只有来自回环地址（`127.0.0.1`、`::1`、
`localhost`）的请求被放行，其他来源一律返回 `401 loophole blocked`；此模式下本机上的任何
进程和任何用户都能读写控制台（唤醒、暂停、改记忆、清学习数据）。
设置 `QQF_CONSOLE_TOKEN`（或 `console.token`）后，HTTP API 只接受
`Authorization: Bearer <token>`，不再接受 URL 查询参数。浏览器连接 WebSocket 前先通过
受保护的 `POST /api/ws-ticket` 申请一次性 ticket，`WS /ws/live?ticket=<ticket>` 只携带
短时凭证；ticket 60 秒过期、单次使用并绑定来源。控制台页面启动后可通过右上角 Token 按钮录入。

## 数据库备份与恢复

```text
.venv\Scripts\python.exe scripts\backup_db.py --database state\qqf.db --backup-dir state\backups --keep 7
.venv\Scripts\python.exe scripts\restore_db.py --backup state\backups\<backup>.db --database state\qqf.db --force
```

数据库启动时会执行版本迁移；迁移前自动生成备份，失败时保留原库并停止启动。干净停机会执行 WAL checkpoint，运行中 WAL 超过 64 MiB 会记录 WARN。恢复前必须先停止 QQF；不带 `--force` 时不会覆盖现有数据库。

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

`pyproject.toml` 已将 pytest 临时目录固定为 `.pytest-tmp/pytest`，缓存目录固定为
`.pytest-tmp/pytest-cache`，避免 Windows 默认临时目录权限导致测试收集失败。二者均由
`.gitignore` 排除。

## 与设计文档的关系

当前实现的设计依据集中在 `docs/design-contracts/` 和 `docs/decisions/`；旧版 qq-bridge 设计文档不随本仓库分发，也不是运行前置依赖。

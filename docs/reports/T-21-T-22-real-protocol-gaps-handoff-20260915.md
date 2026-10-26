# T-21 / T-22 真实协议缺口交办单

- 发布日期：2026-09-15
- 当前基线：`c899c97`
- 输入验收：`docs/acceptance/T-26-deferred-priority-20260915.md`
- 任务性质：真实协议安全边界、正式出站 @ 接口、完整矩阵复验
- T-26 状态：关闭
- T-04 状态：保持打开

## 一、总设计师决定

1. T-26 自动测试、全量门禁和真实冷却期 @ 复验均通过，可以关闭。
2. 下一主任务为 T-21、T-22，之后重新执行 T-04B 完整真实协议矩阵。
3. T-21 是外部 NapCat 鉴权缺陷。QQF 不能把错误 Token 可连接解释为安全；
   在缺陷确认修复前，只允许连接本机 NapCat。
4. T-22 是 QQF 产品缺口。本轮新增正式出站 @ segment，不要求模型手写 CQ 码。
5. 所有出站 @ 继续经过 SafetyPipeline、白名单、成员校验、限流、每日上限和审计。
6. 本任务不修改数据库结构，不扩大大群能力。

## 二、已确认事实

### 2.1 T-26

- `WAKE_PRIORITY`、`_merge_pending_wakes()` 和 `_defer()` 合并逻辑已落地。
- 全量门禁：

```text
pytest: 252 passed, 1 warning
ruff: All checks passed
pyright: 0 errors, 0 warnings, 0 informations
```

- 真实冷却期顺序验证通过：

```text
normal deferred
  -> mention deferred，提升优先级
  -> normal 合并但不降级
  -> 冷却到期执行 mention
```

### 2.2 T-21

- NapCat 版本：`NapCat.Onebot 4.18.19`。
- 正确 Token 可以连接 WS。
- 错误 Token `Bearer invalid-token` 同样可以连接。
- NapCat 当前只监听回环地址，因此风险暂时受本机边界限制。
- QQF 当前对非回环地址没有额外拒绝规则；如果用户把 `qq.wsUrl` 或
  `qq.httpUrl` 指向远端 NapCat，错误 Token 可连接问题会直接变成远程暴露风险。

### 2.3 T-22

- 当前 `QQGateway` 只有：

```python
send_group_message(group_id, text, reply_to_message_id=None)
```

- `OneBotV11Gateway.send_group_message()` 支持纯文本和 reply segment。
- 没有正式构造 `at` segment 的产品接口。
- 模型当前不能通过安全工具路径 @ 指定成员。
- 手工在文本中写 CQ 码会被内容过滤和产品规则拒绝，不属于允许方案。

## 三、T-21 安全边界方案

### 3.1 默认只允许回环

对 `qq.wsUrl` 和 `qq.httpUrl` 增加地址校验：

- 回环主机允许：
  - `127.0.0.1`
  - `localhost`
  - `::1`
- 非回环地址默认拒绝启动配置。

建议新增显式逃生开关：

```json
{
  "qq": {
    "allowRemote": false
  }
}
```

规则：

- `allowRemote=false` 且 URL 非回环：配置校验失败。
- `allowRemote=true` 且 URL 非回环：允许启动，但记录 WARN 审计。
- 该开关只表示项目方明确接受“远端 NapCat 鉴权边界未验证”的风险。
- 不能把 `accessToken` 非空当作鉴权有效证明。

### 3.2 NapCat 侧复检

工程师在可操作 NapCat 的环境中检查：

1. WebSocket Server 监听地址是否为 `127.0.0.1`。
2. HTTP Server 监听地址是否为 `127.0.0.1`。
3. Token 是否同时配置到正向 WS 和 HTTP Server。
4. 重启 NapCat 后重新执行错误 Token 连接测试。
5. 如果 NapCat 新版本修复，记录版本和配置。

验收结果只能有三种：

- `fixed`：错误 Token 被拒绝。
- `loopback_only`：仍接受错误 Token，但只监听本机。
- `unsafe_remote`：错误 Token 可连接且监听非回环，禁止接入并立即整改。

## 四、T-22 出站 @ 接口

### 4.1 产品接口

扩展群消息工具，不新增独立工具：

```python
class SendGroupArgs(BaseModel):
    group_id: str = Field(alias="groupId")
    message: str = Field(min_length=1, max_length=500)
    mention_user_ids: list[str] = Field(default_factory=list, alias="mentionUserIds")
```

工具 schema：

```json
{
  "mentionUserIds": {
    "type": "array",
    "items": {"type": "string"},
    "maxItems": 5,
    "description": "需要明确 @ 的群成员 QQ 号，只能来自最近消息或成员列表"
  }
}
```

规则：

- 不传时保持现有纯文本行为。
- 最多 5 个 mention。
- 空字符串、重复 id、非法数字 id 全部拒绝。
- 必须来自当前群成员列表，否则 fail-closed。
- 消息正文仍由模型提供，不得手写 CQ 码。

### 4.2 Tool 层

涉及 `app/agent/tools.py`：

- `SendGroupArgs` 增加 `mention_user_ids`。
- 校验：

```python
if len(args.mention_user_ids) > 5:
    return ToolResult(success=False, message="单条消息最多 @ 5 人")
```

- 出站队列增加：

```python
ctx.outbox.append(
    {
        "type": "send_group",
        "group_id": args.group_id,
        "message": args.message,
        "mention_user_ids": args.mention_user_ids,
    }
)
```

- 不在 Tool 层直接调用网关，仍由 SafetyPipeline 统一发送。

### 4.3 SafetyPipeline

涉及 `app/runtime/safety_pipeline.py`：

- 在群发送前读取 `mention_user_ids`。
- 使用当前群的 `get_group_members()` 结果校验所有 id。
- 成员列表获取失败时 fail-closed。
- 任一提及用户不存在时拒绝整条消息，不能静默去掉 @ 后继续发。
- 文本分段时，mention 只加到第一段，避免多段重复 @。
- 审计 detail 记录 mention 数量，不记录无关个人信息。

建议内部契约：

```python
mention_user_ids: tuple[str, ...]
```

### 4.4 Gateway

涉及：

- `app/platform/gateway.py`
- `app/platform/qq/onebot_client.py`

扩展接口：

```python
async def send_group_message(
    self,
    group_id: str,
    text: str,
    reply_to_message_id: str | None = None,
    mention_user_ids: Sequence[str] = (),
) -> dict[str, object]:
    ...
```

OneBot 消息段顺序：

```python
message = []
if reply_to_message_id:
    message.append({"type": "reply", "data": {"id": str(reply_to_message_id)}})
for user_id in mention_user_ids:
    message.append({"type": "at", "data": {"qq": str(user_id)}})
message.append({"type": "text", "data": {"text": text}})
```

要求：

- 有任何 mention 时，`message` 使用 segment list，并移除 `auto_escape`。
- 没有 reply 和 mention 时保持现有纯文本路径。
- `NullGateway.sent` 记录 mention id，供 mock 验收。
- 私聊不增加 @ 能力。

## 五、测试要求

### 5.1 配置边界

- 回环 `wsUrl` 和 `httpUrl` 允许。
- 非回环且 `allowRemote=false` 拒绝。
- 非回环且 `allowRemote=true` 可以构建并记录风险。

### 5.2 Tool 和 Safety

- 无 mention 时消息段与旧行为一致。
- 一个 mention 生成 `at` segment。
- 多个 mention 顺序稳定且去重。
- 超过 5 个拒绝。
- 不存在的群成员拒绝整条消息。
- 成员列表接口失败时拒绝发送。
- mention 文本仍经过内容过滤。
- @ 发送计入限流、每日上限和审计。

### 5.3 OneBot 客户端

- 使用 mock transport 检查 `send_group_msg` payload。
- reply + at + text 的顺序正确。
- mention 场景不携带 `auto_escape`。
- QQ id 始终作为字符串发送。

### 5.4 真实协议

恢复账号在线后执行：

1. P08 在测试群发送 `@群友 + 文本`。
2. 客户端确认出现真实蓝色 @，不是普通文本 `@昵称`。
3. NapCat 返回 `status=ok`、`retcode=0` 和 message ID。
4. 审计记录 `send_group_message` 为 allowed。
5. 成员不存在、超过 5 人和非白名单目标均被拒绝。
6. 继续执行 T-04B 的 P04 至 P11、P14、P15。

## 六、工程任务清单

### T-21.1 增加 QQ 连接地址安全边界

- 目标：阻止未验证鉴权的 NapCat 被默认远程连接。
- 涉及文件：
  - `app/config.py`
  - `config.example.json`
  - `README.md`
  - `tests/test_config.py`
- 验收：
  - 非回环默认拒绝。
  - `allowRemote=true` 显式放行。
  - 错误信息和文档不暴露 Token。
- 依赖：无。
- 风险：可能影响本来就使用远端 NapCat 的配置，必须通过显式开关迁移。

### T-21.2 复检 NapCat 错误 Token

- 目标：确认问题是 NapCat 配置还是产品缺陷。
- 涉及文件：
  - `docs/acceptance/T-04B-real-protocol-20260913.md`
  - 或新增 `docs/acceptance/T-21-napcat-auth-20260915.md`
- 验收：
  - 记录监听地址、版本、Token 配置摘要和错误 Token 测试结果。
  - 只允许出现 `fixed`、`loopback_only`、`unsafe_remote` 三种结论。
- 依赖：项目方可操作 NapCat。
- 风险：外部软件行为不受 QQF 控制。

### T-22.1 扩展群发送工具参数

- 目标：模型可通过产品工具提交 `mentionUserIds`。
- 涉及文件：
  - `app/agent/tools.py`
  - `app/agent/tool_schemas.py`
  - `tests/test_tools.py`
  - `tests/test_token_budget.py`
- 验收：
  - 工具 schema 有数组字段。
  - 超过 5 个、重复、空值拒绝。
- 依赖：无。
- 风险：模型可能滥用 @，依靠提示词和成员校验限制。

### T-22.2 SafetyPipeline 校验提及成员

- 目标：所有 @ 目标必须真实属于当前群。
- 涉及文件：
  - `app/runtime/safety_pipeline.py`
  - `tests/test_safety_pipeline.py`
- 验收：
  - 合法成员放行。
  - 非成员、成员查询失败、超限全部拒绝。
  - 文本分段时只有第一段带 mention。
- 依赖：T-22.1。
- 风险：成员接口异常会导致发送拒绝，这是预期 fail-closed。

### T-22.3 Gateway 构造 @ segment

- 目标：OneBot 出站消息使用正式 `at` segment。
- 涉及文件：
  - `app/platform/gateway.py`
  - `app/platform/qq/onebot_client.py`
  - `tests/test_onebot_client.py`
- 验收：
  - reply/at/text 顺序正确。
  - 无 mention 时保持旧文本路径。
  - mock payload 和 NullGateway 记录正确。
- 依赖：T-22.2。
- 风险：不同 OneBot 实现可能对 segment 顺序有差异，NapCat 为准。

### T-22.4 更新提示词与文档

- 目标：明确何时使用 mention，禁止编造 QQ 号。
- 涉及文件：
  - `app/agent/prompt_builder.py`
  - `README.md`
  - `docs/design-contracts/schedule-behavior.md`
- 验收：
  - 提示词说明成员 id 只能来自最近消息或成员工具。
  - 不鼓励每条消息都 @。
- 依赖：T-22.1。
- 风险：模型仍可能尝试无效 id，SafetyPipeline 必须兜底拒绝。

### T-22.5 重跑真实协议矩阵

- 目标：关闭 T-22，并推进 T-04B。
- 涉及文件：
  - `docs/acceptance/T-04B-real-protocol-20260913.md`
  - 或新增复验记录。
- 验收：
  - P08 通过。
  - P02 状态明确。
  - 剩余可执行项逐项记录，不得用 mock 代替真实结果。
- 依赖：T-21.2、T-22.4，且测试 QQ 在线。
- 风险：账号状态、NapCat 配置和人工观察条件。

## 七、统一门禁

```text
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pyright
git diff --check
```

## 八、提交建议

```text
fix(T-21): restrict QQ transport to loopback by default
feat(T-22): add safe outbound group mentions
test(T-22): cover mention validation and protocol segments
docs(T-21-T-22): record auth boundary and real protocol result
```

禁止提交：

- `config.json`
- Token、真实群号、真实 QQ 号
- `stickers/`
- `personas/sticker-pilot.yaml`
- `state/`
- 日志

## 九、需要项目方确认

T-21 的安全策略默认采用“只允许回环地址”。只有项目方明确需要远端 NapCat 时，
才设置 `qq.allowRemote=true`。开始修改代码前，需要项目方明确说“开始执行”。

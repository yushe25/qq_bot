# T-23 表情包灰度试点交办单

- 发布日期：2026-09-14
- 当前基线：`acda24f`
- 输入验收：`docs/acceptance/T-04B-real-protocol-20260913.md`
- 决策结果：选择方案 A，表情包先灰度，不直接全面上线
- 任务性质：安全开关、真实发送验证、低频率试点
- T-04 状态：保持打开，本任务不替代 T-04B

## 一、任务目标

在不影响文字回复和其他功能的前提下，让表情包进入可控灰度：

1. 增加明确的总开关和素材白名单。
2. 在测试群完成一次真实图片发送验证。
3. 通过后只在指定小群、只允许指定素材、低频率运行 24 至 48 小时。
4. 出现异常时通过配置立即关闭，无需回滚代码或删除数据库。

本任务通过后只能宣称“表情包灰度可用”，不能宣称 T-04B 或完整 T-04 已通过。

## 二、当前条件

已具备：

- 本地白名单表情库。
- `approved: true` 校验。
- 文件大小、像素和格式校验。
- 禁止绝对路径、`..` 和远程下载。
- SafetyPipeline、限流、每日发送上限、成员数护栏和审计。
- mock 与 60 分钟稳定性验收通过。

尚未具备：

- 真实 NapCat 表情发送结果，P10 仍为 `未执行`。
- 明确的全局表情开关和单素材白名单。
- 真实账号当前为 `online=false`。

## 三、上线前必须解决的工程问题

### 3.1 配置结构

在 `app/config.py` 增加：

```python
class StickerConfig(BaseModel):
    enabled: bool = False
    allowed_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_allowed_ids(self) -> "StickerConfig":
        if self.enabled and not self.allowed_ids:
            raise ValueError("sticker.enabled=true requires sticker.allowedIds")
        return self
```

接入 `AppConfig`：

```python
sticker: StickerConfig = Field(default_factory=StickerConfig)
```

保留现有 `stickerDir`，避免破坏已有配置。

配置示例：

```json
{
  "stickerDir": "stickers",
  "sticker": {
    "enabled": false,
    "allowedIds": ["pilot-01"]
  }
}
```

默认必须为关闭。只有显式设置为 `true` 且提供非空素材白名单时才允许发送。

### 3.2 StickerLibrary

涉及 `app/agent/sticker_library.py`：

- 构造函数增加 `enabled: bool` 和 `allowed_ids: Sequence[str]`。
- `available` 在关闭、清单无效或白名单为空时为 `False`。
- `list_ids()` 只返回白名单内且可用的素材。
- `resolve()` 拒绝白名单外 id。
- `approved`、路径和 MediaGuard 规则保持不可绕过。

建议接口：

```python
StickerLibrary(
    library_dir,
    enabled=config.sticker.enabled,
    allowed_ids=config.sticker.allowed_ids,
    media_guard=...,
    store=store,
)
```

### 3.3 工具与自动追加

涉及：

- `app/agent/tool_schemas.py`
- `app/runtime/orchestrator.py`
- `app/agent/tools.py`

要求：

- 开关关闭时，不向模型暴露 `sticker_send` 工具。
- 开关关闭时，`_maybe_attach_sticker()` 直接返回。
- 即使模型伪造 `sticker_send` 调用，也必须返回“表情功能未启用”。
- 白名单外 id 必须拒绝并写审计。

### 3.4 回退方式

紧急关闭动作：

```json
{
  "sticker": {
    "enabled": false,
    "allowedIds": ["pilot-01"]
  }
}
```

重启 QQF 后立即停止所有表情发送。不得要求回滚数据库或删除素材库。

## 四、真实发送验证

### 4.1 前置条件

- 测试 QQ 在线。
- NapCat `get_status` 返回可用。
- NapCat 仅绑定回环地址，或 T-21 已完成修复。
- 测试群为专用小群，成员不超过 20。
- 只开启一个测试素材。

如果 NapCat 不满足回环或鉴权条件，必须停止试点，不能先上线。

### 4.2 P07 基础文本

先验证普通文本可以向测试群发送成功。P07 失败时停止，不继续表情测试。

### 4.3 P10 表情

只发送一张 `approved: true` 的本地测试图片，要求：

- 真实客户端能看到图片。
- NapCat 返回 `status=ok`、`retcode=0` 和 message ID。
- 审计记录 `sticker_send` 为 allowed。
- `sticker_usage_counts` 增加 1。
- 每日发送计数增加 1。
- 没有远程下载请求。
- 发送失败不自动重试。

若 NapCat 不接受本地绝对路径，只记录响应并另开缺陷；不得擅自改成 URL 或随便上传文件。

## 五、灰度参数

首轮只允许：

- 1 个测试群。
- 1 个素材。
- `sticker.enabled=true`。
- `allowedIds` 只包含该素材。
- 观察时间 24 至 48 小时。

建议暂时覆盖 persona 概率：

```yaml
schedule:
  sticker_only_probability: 0.02
  sticker_with_text_probability: 0.05
```

文字回复继续正常运行，表情不是每条回复都追加。

## 六、试点停止条件

出现任一项立即关闭 `sticker.enabled`：

- 连续 2 次表情发送失败。
- 表情发送到非白名单群或用户。
- 发现路径逃逸、远程抓取或未授权素材。
- 1 小时内同一群发送超过 2 个表情。
- 24 小时内同一群发送超过 8 个表情。
- 出现重复发送或同一素材短时间连续发送。
- 群成员反馈刷屏、打扰或内容不合适。

关闭后保留日志、审计和数据库证据，另行分析，不直接重新开启。

## 七、测试与验收

新增或修改测试至少覆盖：

1. `sticker.enabled=false` 时工具不暴露、不自动追加。
2. `sticker.enabled=true` 但没有白名单时配置校验失败。
3. 白名单只包含一个 id 时，其他 id 全部拒绝。
4. 伪造工具调用在关闭状态返回明确失败。
5. 开启状态仍受 SafetyPipeline、限流、每日上限和成员数护栏约束。
6. 配置关闭后无需回滚数据库即可停止发送。

必须运行：

```text
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
node .pytest-tmp\package\index.js --pythonpath .venv\Scripts\python.exe
```

## 八、交付物

1. 代码提交：
   - `feat(T-23): add sticker pilot kill switch`
   - `feat(T-23): gate sticker library by explicit allowlist`
   - 如有真实验收文档，单独提交。
2. 配置说明：
   - `config.example.json`
   - `README.md`
   - `docs/design-contracts/sticker-media.md`
3. 验收记录：
   - `docs/acceptance/T-23-sticker-pilot-20260914.md`
   - 写明 P07、P10 结果和真实客户端观察。
4. 原始证据：
   - `.pytest-tmp/t23-sticker-pilot-20260914/`
   - 脱敏后的 OneBot 响应、审计、wake_log 和数据库摘要。

## 九、完成定义

- 表情总开关和单素材白名单已实现。
- 关闭状态无法通过工具或自动逻辑发送表情。
- P07 文本真实发送通过。
- P10 一张表情真实发送通过。
- 灰度期间没有绕过安全、限流、审计或每日上限。
- 支持配置级紧急关闭。
- 文档明确说明灰度范围、风险和回退方式。
- T-04B 仍需独立完成，T-04 保持打开。

## 十、当前需要项目方配合

工程师可以先完成代码、测试和配置开关。真实 P07/P10 验证开始前，项目方需要让测试 QQ 上线，并确认测试时段可用。

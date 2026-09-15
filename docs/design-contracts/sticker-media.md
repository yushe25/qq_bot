# 设计契约：表情包与入站媒体

- 状态：implemented
- 目标：机器人可以像真群友一样发表情包，并能理解群里的图片、表情和语音语境。

## 目标

模型能够：

- 基于上下文选择白名单内的本地表情包并发送。
- 在语境合适时“只发表情包不说话”或“表情包加短句”。
- 看到群里的图片、QQ 表情、语音时得到结构化摘要，而不是空文本。

## 非目标

- 不做图片生成、任意 URL 发送、网络下载表情。
- 不发送白名单外的路径或文件。
- 不做跨 QQ 平台的表情市场。

## 触发条件

- 入站：所有包含非文本 segment 的消息，在归一化阶段生成媒体摘要。
- 出站总开关：`sticker.enabled` 默认 `false`；关闭时不暴露工具、不自动追加，显式伪造调用也返回失败。
- 手工与旧素材白名单：`sticker.allowedIds` 为空时功能失败关闭；开启时手工或旧格式素材只允许清单内明确列出的 id。
- 自动抓取素材：`source=group_capture` 且 `approved=true` 时，不要求写入静态 `allowedIds`，
  但必须满足 `scopeGroups` 来源群约束；`sameGroupOnly=true` 时只能在来源群使用。
- 出站：模型查看受限语义目录后主动调用 `sticker_send`；每轮最多一张，可只发表情或文字加表情。
- 没有合适素材时必须发送纯文字，不允许运行时随机追加兜底表情。
- 每会话每日表情发送数计入发送频率和每日上限。

## 数据契约

### 表情库

本地目录 + YAML 清单：

```text
stickers/
  manifest.yaml
  images/
    mock-01.gif
mock-01.gif
```

manifest 条目：

```yaml
- id: mock-01
  name: 无语猫
  file: images/mock-01.gif
  description: 一只白猫翻白眼，表达无语和敷衍
  tags: [无语, 敷衍, 猫]
  moods: [无语, 无奈]
  scenes: [被吐槽时, 冷场接梗时]
  avoid_scenes: [严肃道歉时, 安慰别人时]
  approved: true
  usage_count: 0
  tag_source: manual
```

约束：

- `file` 必须是库内相对路径，禁止绝对路径和 `..`。
- 只发送 `approved: true` 的条目。
- 自动抓取素材必须有非空 `sourceGroupIds` 和 `scopeGroups`；来源群不匹配时不可见、不可发送。
- 文件大小和像素通过 MediaGuard 校验。
- 发送后 `usage_count + 1`，供频率统计和人格偏好排序。
- 语义目录只包含启用、白名单、已审核且通过 MediaGuard 的素材；不向模型暴露文件路径。
- 自动标注不得修改 `approved`，失败时保留原元数据；自动结果立即写入，重启后生效。

### 入站媒体摘要

归一化后的 `text` 加入以下格式：

```text
[图片]
[表情:178:抠鼻]
[语音]
[文件:文件名]
[分享卡片]
```

摘要进入最近消息和 batch context，不携带原始文件内容。视觉理解能力可选，默认先摘要后按模型能力扩展。

## 安全边界

- `sticker.enabled=false` 时所有表情发送失败关闭，不依赖删除数据库或素材文件。
- `sticker.enabled=true` 时必须提供非空 `sticker.allowedIds`，白名单外素材全部拒绝。
- `sticker_send` 只接受 manifest 中的 id，其余请求返回失败。
- 同一轮已有出站表情时，第二次 `sticker_send` 必须失败；不同轮次可重复同一 id。
- 出站图片必须经过 SafetyPipeline：白名单、频率限制、内容过滤、审计。
- 禁止通过 sticker 发送 CQ 码、构造路径或绕过文本过滤。
- 群成员数超过上限时表情同样禁止发送。

## 验收指标

- 入站图片消息在 prompt 中显示 `[图片]`，不再为空文本。
- `sticker_send` 对白名单 id 成功排队并发送，对未知 id 返回明确失败。
- 注入 `../../etc/passwd` 等路径 100% 拒绝。
- mock 链路可以验证“只发表情包”和“表情包加短句”两种行为。
- 表情发送计入审计和限流，控制台可查 usage_count。
- 模型只能看到目录中的 id、名称、说明、标签、情绪和场景，不能看到路径、hash、OCR 原文或密钥。

## 失败与回滚

- 紧急回退：将 `sticker.enabled` 改为 `false` 并重启 QQF，即可停止全部表情发送。
- 文件不存在或校验失败：工具返回失败，不重试，不影响文本消息。
- 发送失败：写审计，不自动重发。
- manifest 解析失败：整个表情库降级为不可用，并在控制台显示错误。
- 自动标注失败：不覆盖旧条目，命令返回失败 id；修改 manifest 后需重启 QQF。

## 自动标注

- 使用独立的 `sticker.autoTag` Provider 配置，不读取聊天模型密钥，也不进入聊天模型 fallback。
- 仅限 rate limit、timeout、provider unavailable 三类错误允许 fallback；鉴权、请求和响应错误不得切换。
- 密钥只允许通过 `apiKeyEnv` 指向的环境变量提供，禁止明文 `apiKey`。
- 图片发送前再次经过 MediaGuard；请求体、base64 和密钥不得写日志。
- 模型输出必须按严格 JSON 解析，并经过控制字符、CQ 码、敏感信息、数量和长度清洗。
- 命令行支持 `--id`、`--all`、`--force` 和 `--dry-run`；单张失败不写该条目并返回非零，输出保持脱敏。

## 开放问题

- 是否需要在控制台提供 manifest 可视化编辑页；当前先使用 YAML 人工编辑和命令行自动标注。

## 实现记录

- 当前 OneBot 发送走本地文件路径的 `image` segment（`file=<本地绝对路径>`），NapCat/Lagrange 实测结果仍待真实环境确认。
- 纯表情回复由模型单独调用 `sticker_send` 完成；`sticker_only_probability` 与
  `sticker_with_text_probability` 仅保留解析兼容并已 deprecated，不再参与选图。
- 未知 id、非 approved、路径逃逸、文件校验失败均拒绝并审计。
- 表情发送计入发送频率、每日上限、成员数护栏和审计；`usage_count` 持久化到 SQLite，控制台可通过审计与统计接口观察。
- `StickerLibrary` 统一通过 `sticker_manifest` 读取和校验语义清单，只向提示词暴露可发送素材的 id、名称、说明、标签、情绪与场景。
- 自动标注通过独立视觉 Provider 完成，严格 JSON 清洗后原子写入 manifest；失败保留旧元数据，人工 `manual` 默认跳过，`--force` 可覆盖。
- 自动标注命令处理完成后提示重启 QQF；dry-run 不写 manifest，输出不包含密钥、base64 或绝对路径。
- 入站自动抓取沿用启动时加载 manifest 的口径；新批准素材重启后进入对应来源群目录，不热加载。
- 自动标注支持 JPEG、PNG、GIF、WebP；超过标签、情绪、场景数量上限时按配置上限截断，不再整张失败。

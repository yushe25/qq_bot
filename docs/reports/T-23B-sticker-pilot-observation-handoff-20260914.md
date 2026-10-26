# T-23B 表情包 24–48 小时灰度观察交办单

- 发布日期：2026-09-14
- 当前基线：`5db43f1`
- 输入验收：`docs/acceptance/T-23-sticker-pilot-20260914.md`
- 输入报告：`docs/reports/T-23-sticker-pilot-chief-designer-report-20260914.md`
- 任务性质：运行配置、长期素材固定、低频率观察
- T-04 状态：保持打开，本任务不替代 T-04B

## 一、总设计师决定

1. T-23 第一阶段验收关闭，真实文本和可见图片发送均已通过。
2. 进入 T-23B：先做 24 小时安全观察，无异常后继续到 48 小时。
3. 灰度期间只允许一个测试群、一个素材。
4. 素材只保存在本机运行目录，不提交到仓库，避免版权和仓库体积问题。
5. 不改生产代码结构，不扩大素材范围，不提高默认概率。
6. T-21、T-22、T-04B 和完整 T-04 状态不变。

## 二、当前已确认事实

- `sticker.enabled` 默认关闭。
- 开启时必须配置非空 `allowedIds`。
- 关闭时模型看不到 `sticker_send`，自动追加逻辑也不会发送。
- 白名单外素材、未审核素材、路径逃逸和媒体校验失败均被拒绝。
- 一张 `504×480` JPEG 已通过 MediaGuard。
- 真实 NapCat 已成功发送 image segment，客户端确认可见。
- 真实 `config.json` 当前仍未开启表情。

## 三、长期运行素材

项目方确认可见的 JPEG 当前位于验收临时目录：

```text
.pytest-tmp/t23-sticker-pilot-20260914/stickers/images/6B159721F96B973EBF5311856D0AE1D0.jpg
```

本任务把它固定为长期运行素材：

```text
stickers/
  manifest.yaml
  images/
    pilot-01.jpg
```

操作要求：

- 复制原 JPEG 为 `stickers/images/pilot-01.jpg`。
- 不修改图片内容，不重新编码。
- 在 `stickers/manifest.yaml` 中只登记这一条：

```yaml
- id: pilot-01
  name: 灰度测试表情
  file: images/pilot-01.jpg
  tags: [测试]
  approved: true
  usage_count: 0
```

- 将 `/stickers/` 加入 `.gitignore`，本地素材和 manifest 不提交。
- 设计契约中的素材示例继续保留，不把真实素材写入文档或测试快照。

## 四、试点人格

不修改默认 persona。新增本地试点配置：

```text
personas/sticker-pilot.yaml
```

复制默认 persona 的全部内容，只将概率改为：

```yaml
schedule:
  sticker_only_probability: 0.02
  sticker_with_text_probability: 0.05
```

真实 `config.json` 的 `personaPath` 临时指向该文件。该本地试点文件同样不得提交。

## 五、真实配置

只在未跟踪的 `config.json` 中修改：

```json
{
  "stickerDir": "stickers",
  "sticker": {
    "enabled": true,
    "allowedIds": ["pilot-01"]
  },
  "personaPath": "personas/sticker-pilot.yaml"
}
```

不得修改：

- 白名单群。
- 模型 Provider。
- 每日和每分钟发送上限。
- 群成员数上限。
- 其他素材或概率。

## 六、启动前检查

1. 确认测试 QQ 在线。
2. 确认 NapCat 仍只监听 `127.0.0.1`，或 T-21 已修复。
3. 确认 `sticker.enabled=true`。
4. 确认 `allowedIds` 只有 `pilot-01`。
5. 确认 `pilot-01.jpg` 能通过 MediaGuard。
6. 确认控制台健康检查通过。
7. 确认只有一个 QQF 实例。
8. 记录开始时间、代码提交和配置摘要，不记录 Token 或真实群号。

## 七、观察流程

### 7.1 前 24 小时

- 正常聊天，不人为刷触发。
- 允许自然触发表情。
- 每 6 小时检查一次：
  - `sticker_send` allowed 数量。
  - `sticker_send` failed 或 blocked 数量。
  - `sticker_usage_counts`。
  - `daily_send_counts`。
  - 重复发送和 `event_dedupe` 命中。
  - 控制台 ERROR 和 Traceback。
- 24 小时结束时做一次安全检查。

### 7.2 后 24 小时

只有 24 小时检查全部正常才继续：

- 保持相同配置，不增加素材。
- 每 6 小时继续采集指标。
- 48 小时或达到停止条件时结束。

如果 48 小时内真实表情成功发送少于 3 次，结论标记为“样本不足”，不能直接扩大上线；可以延长观察或安排一次人工触发。

## 八、通过指标

- 表情发送成功率达到 90% 以上。
- 无未授权素材、路径逃逸、远程下载或错误目标发送。
- 无重复发送。
- 单群每小时不超过 2 个表情。
- 单群每日不超过 8 个表情。
- `sticker_usage_counts` 与成功发送次数一致。
- 表情发送仍计入每日发送上限。
- 没有 ERROR、Traceback 或会话永久 ACTIVE。
- 群成员没有明确反馈刷屏或打扰。

## 九、立即停止条件

出现任一项，立即将 `sticker.enabled` 改为 `false` 并重启：

- 连续 2 次表情发送失败。
- 表情发往非白名单目标。
- 绕过 SafetyPipeline、限流或每日上限。
- 出现重复发送。
- 单群每小时超过 2 个表情。
- 单群每日超过 8 个表情。
- 素材文件损坏、被替换或 manifest 出现额外条目。
- 群成员明确表示打扰或刷屏。

停止后不直接恢复，先保留审计和数据库证据，再另行决定。

## 十、交付物

1. 长期素材文件只在本地准备，不提交。
2. 真实配置和 persona 只在本地准备，不提交。
3. 验收记录：

```text
docs/acceptance/T-23B-sticker-observation-20260914.md
```

内容必须包含：

- 开始和结束时间。
- 24 小时检查结果。
- 48 小时最终结果。
- 成功、失败、拒绝和重复次数。
- 每小时和每日峰值。
- 是否触发停止条件。
- 是否建议继续灰度、保持现状或关闭。
4. 证据放在：

```text
.pytest-tmp/t23b-sticker-observation-20260914/
```

报告禁止包含真实群号、QQ 号、私聊内容或 Token。

## 十一、完成定义

- 一个素材完成 24 小时安全观察。
- 无停止条件后继续到 48 小时。
- 至少 3 次真实成功发送，或明确记录样本不足。
- 所有指标、日志和数据库证据可复查。
- 没有提交素材、真实配置、运行日志或 `state/`。
- 明确给出继续灰度、保持现状或关闭的建议。

## 十二、需要项目方操作

开始观察前，项目方需要确认：

1. 可以将项目方提供的 JPEG 仅作为本机长期素材保存。
2. 测试群和测试时段可用于 24–48 小时自然聊天观察。
3. 出现停止条件时允许工程师立即关闭表情并重启 QQF。

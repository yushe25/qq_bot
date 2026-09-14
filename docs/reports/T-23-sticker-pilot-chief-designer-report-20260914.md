# T-23 表情包灰度试点总设计报告

- 报告日期：2026-09-14
- 代码基线：`ebbb508`
- 正式验收：
  [T-23-sticker-pilot-20260914.md](../acceptance/T-23-sticker-pilot-20260914.md)

## 结论

T-23 的工程开关、单素材白名单、P07 基础文本和 P10 可见图片均已通过。项目方已在真实
客户端确认收到测试图片。当前可以判定“表情包真实发送链路和灰度开关可用”，但 24–48
小时低频率灰度尚未启动，不能宣称完整试点周期已完成。

## 已完成内容

### 安全开关与白名单

- 已提交 `913a9bc feat(T-23): add sticker pilot kill switch`。
- 已提交 `ebbb508 feat(T-23): gate sticker library by explicit allowlist`。
- `sticker.enabled` 默认关闭。
- 开启时必须提供非空 `sticker.allowedIds`。
- 关闭时工具不暴露、自动追加不执行、伪造调用失败。
- `pilot-01` 是本次唯一素材，其他素材拒绝。
- 配置关闭即可停止发送，不依赖数据库回滚。

### 真实 P07

- 普通文本通过产品 `SafetyPipeline` 发往测试群。
- NapCat 返回 message ID。
- 审计结果为 `allowed`。
- 无自动重试。

### 真实 P10

- 第一次使用 1×1 透明 GIF，证明协议链路，但客户端只能看到空白小图。
- 发现并修复 T-24：JPEG 宽高使用 4 字节解析，导致所有 JPEG 被误判。
- 修复后，项目方提供的 `504×480` JPEG 通过本地媒体校验。
- 复验只补发 P10，NapCat 群历史确认存在 image segment。
- 审计动作为 `sticker_send`，结果为 `allowed`。
- 素材使用计数为 1，每日发送计数为 1。
- 使用本地文件路径，没有 URL 下载，没有失败重试。
- 项目方确认真实客户端可见。

## 新发现问题

### T-24：有效 JPEG 被 MediaGuard 拒绝

- 严重度：高，会阻止合法 JPEG 素材进入试点。
- 根因：JPEG SOF 宽高是 2 字节大端，代码按 4 字节读取。
- 状态：已修复，新增带 EXIF 的 JPEG 回归测试。
- 影响：修复前只有 GIF/PNG 能稳定解析，所有 JPEG 都会失败关闭。

## 风险与边界

- NapCat 错误 Token 仍可连接，T-21 未解决；本次仅因 NapCat 只监听回环才允许继续。
- T-22 出站 @ 缺口未改变。
- 真实 QQ 账号离线时发送仍不可用。
- 当前真实 `config.json` 仍保持 `sticker.enabled=false`，没有直接进入长期灰度。
- 本次只验证一个测试群、一个素材，不能外推到大群或未审核素材。
- T-04B 和完整 T-04 没有通过，本报告不能替代 T-04B。

## 门禁

```text
pytest -q：192 passed, 1 warning
ruff check .：All checks passed!
pyright：0 errors, 0 warnings, 0 informations
```

## 建议决策

第一阶段验收可以关闭。下一阶段是否进入 24–48 小时低频率灰度，需要项目方单独确认：

1. 将项目方确认可见的唯一 JPEG 固定到长期素材目录。
2. 在真实配置中开启 `sticker.enabled=true`，保持 `allowedIds=["pilot-01"]`。
3. 将 persona 灰度概率设为 `sticker_only_probability=0.02`、
   `sticker_with_text_probability=0.05`。
4. 重启 QQF，开始 24–48 小时观察并保留审计、usage 和发送计数证据。
5. 达到任一停止条件时立即将 `sticker.enabled` 设回 `false` 并重启。

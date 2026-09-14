# T-23 表情包灰度试点验收记录

- 验收日期：2026-09-14
- 代码基线：`ebbb508`
- 任务范围：P07 基础文本、P10 单张真实图片、安全开关和证据核对
- 验收结论：P07、P10 通过；24–48 小时低频率灰度尚未启动
- 边界声明：本记录不代表 T-04B 或完整 T-04 已通过

## 一、环境

- OneBot 实现：`NapCat.Onebot 4.18.19`
- OneBot 协议：`v11`
- 真实账号：`online=true`
- NapCat HTTP：`127.0.0.1:3000`
- NapCat WebSocket：`127.0.0.1:3001`
- 监听范围：均为回环地址
- 测试群：1 个，未在仓库中记录真实群号或账号

T-21 的错误 Token 仍可连接问题没有在 NapCat 侧消除，但 NapCat 仅绑定回环地址，符合交办单
“回环或完成 T-21 修复”的条件之一。该风险仍保留，不能视为已修复。

## 二、发送前置检查

只读检查命令：

```text
.venv\Scripts\python.exe scripts\onebot_check.py --report-dir .pytest-tmp\t23-sticker-pilot-20260914\readonly-auth
```

结果：

- HTTP：通过
- WebSocket 连接：通过
- WebSocket 事件：通过
- 证据：`.pytest-tmp/t23-sticker-pilot-20260914/readonly-auth/onebot-check.json`
- SHA-256：`0EA62358A32359F1811869D14CC0B60470C401032187EE86F1F20CC6C26D7800`

## 三、P07 基础文本

发送方式：通过产品 `SafetyPipeline` 向测试群发送普通文本，不直接调用 OneBot 动作接口。

结果：

- 状态：通过
- NapCat 返回 message ID：是
- 审计动作：`send_group_message`
- 审计结果：`allowed`
- 失败自动重试：无
- 证据：`.pytest-tmp/t23-sticker-pilot-20260914/live/p07-p10.json`
- SHA-256：`B9685B853DF8AD968AD3BEFB416FD18068E4EC2F9E891D58C3A13EF4877273EC`

首次完整运行同时完成 P07 和协议级 P10，临时数据库记录每日发送计数为 2。

## 四、P10 图片发送

### 4.1 首次协议验证

首次使用 1×1 透明 GIF，只用于验证 OneBot image 链路。发送结果：

- NapCat 返回成功和 message ID
- 审计动作：`sticker_send`
- 审计结果：`allowed`
- 素材使用计数：1
- 每日发送计数：2
- 真实客户端可见结果为一张空白小图

该结果证明协议发送成功，但不满足清晰可见的视觉验收要求，因此没有据此结束 P10。

### 4.2 T-24：JPEG 尺寸解析缺陷

项目方提供一张有效的 `504×480` JPEG 后进行登记，发现 `MediaGuard` 将其误判为
“不支持的图片格式”。

根因：

- JPEG SOF 的宽高字段为 2 字节大端整数。
- `_unpack_be()` 固定使用 4 字节解析，导致 JPEG 宽高始终为 `None`。

处理：

- 新增 `T-24`。
- 修复为按输入长度支持 2 字节和 4 字节大端整数。
- 新增带 EXIF 的 JPEG 回归测试。
- 项目方提供的 JPEG 现通过 `MediaGuard`，解析尺寸为 `504×480`。

### 4.3 可见图片复验

复验只补发 P10，不重复 P07。素材仍为唯一的 `pilot-01`，白名单仍只包含该 ID。

结果：

- 状态：通过
- NapCat 返回 message ID：是
- 群历史 segment：`image`
- 审计动作：`sticker_send`
- 审计结果：`allowed`
- 每日发送计数：1
- 素材使用计数：1
- 发送方式：本地文件路径
- 远程下载：无
- 发送失败自动重试：无
- 真实客户端观察：项目方确认收到图片
- 证据：`.pytest-tmp/t23-sticker-pilot-20260914/live-visible/p07-p10.json`
- SHA-256：`641AB5855A7441CEE3E8BD56F9A711A69CF2E0DCD8EA7A5B1FC618243A66C498`

## 五、安全开关

代码状态：

- `sticker.enabled` 默认 `false`
- `sticker.enabled=true` 时必须提供非空 `allowedIds`
- 关闭时不暴露 `sticker_send`
- 关闭时不执行自动追加
- 关闭时伪造 `sticker_send` 调用返回“表情功能未启用”
- 白名单外素材拒绝
- 路径逃逸、未审核素材和媒体校验失败拒绝
- 发送仍经过 SafetyPipeline、白名单、限流、每日上限、成员数护栏和审计

紧急关闭方式：

```json
{
  "sticker": {
    "enabled": false,
    "allowedIds": ["pilot-01"]
  }
}
```

关闭后重启 QQF 即可停止表情发送，不需要回滚数据库或删除素材文件。

## 六、门禁

```text
.venv\Scripts\python.exe -m pytest -q
结果：192 passed, 1 warning

.venv\Scripts\python.exe -m ruff check .
结果：All checks passed!

node .pytest-tmp\package\index.js --pythonpath .venv\Scripts\python.exe
结果：0 errors, 0 warnings, 0 informations
```

## 七、结论与后续

- P07：通过。
- P10：协议发送、审计、计数和客户端可见性通过。
- T-24：已修复并通过回归测试。
- T-23 第一阶段验收通过。
- 24–48 小时低频率灰度尚未启动，因此不能宣称试点周期完成。
- 正式灰度前仍需决定是否将项目方提供的唯一素材复制到长期素材目录，并将真实
  `config.json` 的 `sticker.enabled` 开启后重启。
- T-04B、T-21、T-22 和完整 T-04 状态不变。

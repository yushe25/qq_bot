# T-04B 真实协议矩阵复验记录

- 日期：2026-09-15
- NapCat：`NapCat.Onebot 4.18.19`
- OneBot：`v11`
- 结论：P01-P16 全部通过或属于明确回环受控例外；在默认禁止远端 OneBot 的前提下，T-04 可以关闭。

## 协议矩阵

| 编号 | 项目 | 结果 | 说明 |
| --- | --- | --- | --- |
| P01 | HTTP 版本 | 通过 | HTTP 200，status=ok，retcode=0，协议 v11 |
| P02 | WS 鉴权 | 通过 | 正确 Token 可连接；错误 Token 仍可连接，但端点均回环，记录为受控例外 |
| P03 | WS 生命周期 | 通过 | 真实连接存活并收到事件 |
| P04 | 群文本入站 | 通过 | 普通文本进入 batch context |
| P05 | @ 入站 | 通过 | at segment 归一化并提升为 mention |
| P06 | 拍一拍入站 | 通过 | poke 事件进入同一真实批次 |
| P07 | 群文本出站 | 通过 | 产品网关返回 message_id，历史可解析 text segment |
| P08 | 出站 @ | 通过 | 产品 mentionUserIds 生成 at segment；真实群显示蓝色 @ |
| P09 | 引用回复 | 通过 | reply + text segment 顺序正确 |
| P10 | 表情媒体 | 通过 | 白名单本地图片发送成功，历史出现 image segment |
| P11 | 私聊出入站 | 通过 | clean restart 加载 owner 白名单后，私聊入站创建 private 会话，模型回复经审计 allowed，NapCat 出站成功 |
| P12 | 历史接口 | 通过 | 返回可解析消息 |
| P13 | 成员接口 | 通过 | 返回真实成员列表 |
| P14 | 断线重连 | 通过 | NapCat 重启后 QQF 未重启仍恢复接收群事件 |
| P15 | 重连去重 | 通过 | 重连后未出现重复发送；T-20 跨重启事件去重测试覆盖重放口径 |
| P16 | 动作错误 | 通过 | 无效 get_msg 被归一化 |

## 证据摘要

- P07-P10 原始报告：`.pytest-tmp/t04b-first-wave3/summary.json`。
- P11 clean restart 后：好友关系为 true，private 会话创建，入站触发 mention 唤醒，私聊回复审计为 allowed。
- NapCat 重启后 QQF 未重启仍收到新的群事件，证明 WS 自动恢复。
- 排障期间一度出现 `scene not in whitelist`，根因是 QQF 进程启动早于 config.json 更新；重启加载新白名单后消失。
- 报告不记录 Token、真实群号、QQ 号、message ID 或私聊内容。

## 结论

T-04 可以关闭。P02 的 NapCat 错误 WS Token 风险由 QQF 默认回环限制缓解；如需连接远端 NapCat，必须先修复 NapCat 鉴权。

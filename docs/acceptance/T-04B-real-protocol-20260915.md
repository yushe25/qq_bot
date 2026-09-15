# T-04B 真实协议矩阵复验记录

- 日期：2026-09-15
- NapCat：`NapCat.Onebot 4.18.19`
- OneBot：`v11`
- 结论：P01-P10、P12、P13、P16 通过；P02 错误 WS Token 为回环受控例外；P11、P14、P15 仍阻塞，T-04 保持打开。

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
| P11 | 私聊出入站 | 部分通过 | NapCat 恢复后私聊出站返回 message_id；私聊入站仍未进入 QQF |
| P12 | 历史接口 | 通过 | 返回可解析消息 |
| P13 | 成员接口 | 通过 | 返回真实成员列表 |
| P14 | 断线重连 | 未确认 | NapCat 重启后未观察到新的入站事件，无法证明 QQF 自动恢复 |
| P15 | 重连去重 | 未执行 | 依赖 P14 的真实重放条件 |
| P16 | 动作错误 | 通过 | 无效 get_msg 被归一化 |

## 证据摘要

- P07-P10 原始报告：`.pytest-tmp/t04b-first-wave3/summary.json`。
- P11 最终复测：好友关系为 true，`send_private_msg` 返回成功；但 owner 发出的私聊没有创建 QQF private 会话。
- NapCat 重启后 QQF 健康接口仍正常，但没有观察到新的群或私聊入站事件，无法证明 WS 自动恢复。
- 报告不记录 Token、真实群号、QQ 号、message ID 或私聊内容。

## 结论

T-04 不能关闭。P11 仅剩私聊入站未打通；P14/P15 需要进一步确认 QQF WS 是否在 NapCat 重启后真正恢复和去重。

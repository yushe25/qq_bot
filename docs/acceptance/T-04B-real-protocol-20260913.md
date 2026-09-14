# T-04B 真实 NapCat 协议矩阵验收

- 验收日期：2026-09-13
- 冻结基线：`acda24f`
- NapCat 版本：`NapCat.Onebot 4.18.19`
- OneBot 协议：`v11`
- 真实账号状态：`online=false`
- 验收结论：`未通过，存在外部环境阻塞和真实鉴权失败`
- T-04：保持打开

## 一、执行方式

只读检查：

```text
.venv\Scripts\python.exe scripts\onebot_check.py --report-dir .pytest-tmp\t04b-20260913\readonly
```

真实协议矩阵：

```text
.venv\Scripts\python.exe scripts\onebot_live_acceptance.py --report-dir .pytest-tmp\t04b-20260913\live-corrected --allow-send
```

发送门禁：

- 显式传入 `--allow-send`。
- 设置 `QQF_ALLOW_REAL_SEND=1`。
- 目标群来自当前验收白名单。
- 因真实 QQ 账号 `online=false`，脚本将发送项标记为 `未执行`，未继续尝试发送。

## 二、协议矩阵

| 编号 | 项目 | 结果 | 说明 |
| --- | --- | --- | --- |
| P01 | HTTP 版本 | 通过 | HTTP 200，`status=ok`，`retcode=0`，协议 v11 |
| P02 | WS 鉴权 | 失败 | 正确 Token 可连接，但错误 Token 也可连接 |
| P03 | WS 生命周期 | 通过 | 连接存活并收到 lifecycle/事件 |
| P04 | 群文本入站 | 未执行 | 需要真实测试账号人工在测试群发送 |
| P05 | @ 入站 | 未执行 | 需要真实测试账号人工 @ bot |
| P06 | 拍一拍入站 | 未执行 | 需要真实测试账号人工拍 bot |
| P07 | 群文本出站 | 未执行 | QQ 账号 offline |
| P08 | 出站 @ | 未执行 | QQ 账号 offline；另存在 QQF 无正式 at 接口缺陷 |
| P09 | 引用回复 | 未执行 | 依赖 P07 |
| P10 | 表情媒体 | 未执行 | QQ 账号 offline |
| P11 | 私聊出入站 | 未执行 | QQ 账号 offline，且入站需 owner 人工发送 |
| P12 | 历史接口 | 通过 | 返回 5 条可解析消息 |
| P13 | 成员接口 | 通过 | 返回 2 名成员 |
| P14 | 断线重连 | 未执行 | 需要受控切断真实 WS 并观察 QQF 自动恢复 |
| P15 | 重连去重 | 未执行 | 依赖 P14 |
| P16 | 动作错误 | 通过 | 无效 `get_msg` 被归一化 |

## 三、发现的问题

### T-21：NapCat WS 错误 Token 未被拒绝

- 现象：使用合法 Token 的连接成功。
- 现象：使用 `Bearer invalid-token` 的连接同样成功。
- 影响：本地其他客户端可能绕过 WS 鉴权并订阅真实事件。
- 预期：错误 Token 连接应被拒绝，或 NapCat 明确禁用鉴权并仅绑定回环。
- 初步判断：属于 NapCat 端 Token 配置或鉴权行为问题，不是 QQF 客户端代码可单独修复。
- 建议：检查 NapCat 正向 WS 的 Token 配置；若使用回环地址，需在验收环境中显式接受该风险。

### T-22：QQF 缺少正式出站 @ 接口

- 现象：当前 `OneBotV11Gateway` 没有发送真实 `at` segment 的正式接口。
- 现有能力：群文本和 reply 支持；@ 仍只能作为普通文本或绕过产品路径手工构造。
- 影响：P08 无法通过产品链路满足。
- 建议：新增正式群消息 segment 构造能力，保留安全管线、白名单和限流。

## 四、外部环境阻塞

真实账号 `get_status` 返回：

```text
online=false
```

因此以下项目本轮未执行：

- P04 至 P06 入站事件。
- P07 群文本出站。
- P08 出站 @。
- P09 引用回复。
- P10 表情媒体。
- P11 私聊出入站。
- P14 断线重连。
- P15 重连去重。

首次发送尝试记录了 NapCat `sendMsg` 超时，但该现象与账号离线一致，不单独作为产品缺陷。

## 五、脱敏与证据

报告不包含 Token、真实群号、QQ 号、message ID 或私聊内容。

原始证据：

- `.pytest-tmp/t04b-20260913/readonly/onebot-check.json`
- `.pytest-tmp/t04b-20260913/readonly/onebot-check.md`
- `.pytest-tmp/t04b-20260913/live-corrected/summary.json`
- `.pytest-tmp/t04b-20260913/live-corrected/summary.md`
- `.pytest-tmp/t04b-20260913/live/summary.json`

SHA-256：

- 只读检查：`19F4BCD53DE5B6ADDDCFCAB2C410C64004ADF187BCA4762C5EB728EB46A44DC2`
- 修正矩阵：`CAAC489973DF137F6AAF8F177EDE082B5C14CB7D0C40ABBC65A0CE6DD51F5B10`
- 首次矩阵：`EC5AEF5B9DCB8BB7957922B60062608223B097B37E8C97ACAD48D70B273F351E`

## 六、门禁

```text
.venv\Scripts\python.exe -m pytest -q
结果：185 passed, 1 warning

.venv\Scripts\python.exe -m ruff check .
结果：All checks passed!

node .pytest-tmp\package\index.js --pythonpath .venv\Scripts\python.exe
结果：0 errors, 0 warnings, 0 informations
```

## 七、结论

- T-04B：未通过。
- T-04：保持打开。
- P02 真实鉴权失败必须先处理。
- 发送、入站、私聊和断线重连必须在账号在线且具备人工观察条件后重新执行。
- T-21、T-22 修复或明确接受后，需要重新执行完整真实协议矩阵。

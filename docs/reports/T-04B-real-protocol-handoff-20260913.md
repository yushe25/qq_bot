# T-04B 真实 NapCat 协议矩阵验收交办单

- 发布日期：2026-09-13
- 验收基线：`acda24f`
- 前置报告：`docs/reports/T-04C-chief-designer-report-20260913.md`
- 前置验收：`docs/acceptance/T-04C-stage6-60m-final-20260913.md`
- 任务性质：真实协议验收、证据收集、兼容性判定
- 任务目标：决定完整 T-04 是否可以关闭
- 远端策略：继续不 push，不触发远端 CI

## 一、总设计师决定

1. T-04C 通过，T-04A 正式关闭。
2. 下一步主任务为 T-04B：完成真实 NapCat 协议矩阵。
3. T-04 只有在本任务全部必测项通过后才能关闭。
4. 真实发送默认关闭；必须显式授权后才允许向测试群或 owner 发送。
5. 验收期间只允许修改验收脚本和测试。发现生产代码缺陷时登记 T-21 或后续任务。
6. T-05 暂不启动，等待 T-04B 结果。若 T-04B 因外部环境等待，可另评是否并行。

## 二、任务目标

在真实 NapCat 上验证 OneBot v11 双通道链路：

```text
NapCat 正向 WebSocket -> QQF 接收和归一化
QQF HTTP Action -> NapCat 执行 -> 测试 QQ 群或 owner
```

重点回答：

1. 真实事件能否被正确接收、过滤、归一化和去重。
2. 文本、@、引用、表情、私聊能否真实发送。
3. WS 断开后能否自动恢复，恢复后会不会重复发送。
4. NapCat 实际返回结构是否与当前代码和 mock 假设一致。

## 三、安全边界

- 只允许使用专用测试群和测试 owner。
- 测试群成员数不得超过 20。
- 所有发送必须设置明显测试标识，例如 `[QQF-T04B 测试]`。
- 默认 dry-run，不发送任何消息。
- 只有同时满足以下条件才允许真实发送：
  - 命令行显式传入 `--allow-send`。
  - 环境变量 `QQF_ALLOW_REAL_SEND=1`。
  - 目标群在验收配置白名单中。
- 不向非白名单群、陌生人或生产群发送。
- Token、真实群号、QQ 号、message ID 和私聊内容不得进入仓库。
- 验收报告只使用 `<test_group>`、`<test_user>` 等脱敏占位符。
- 不抓取或上传任何真实媒体内容。

## 四、执行前条件

1. 当前 HEAD 为 `acda24f`。
2. 确认没有未提交的 tracked 源码或配置改动。
3. QQF 使用隔离状态目录，不得复用真实 `state/`：

```text
.pytest-tmp/t04b-state/
```

4. 验收产物写入：

```text
.pytest-tmp/t04b-20260913/
```

5. NapCat 已开启正向 WS 和 HTTP Action，并支持 Bearer Token。
6. 测试群和 owner 已由项目方授权。
7. 测试期间确认只有一个 QQF 实例运行。
8. 测试前保存 NapCat 版本和协议版本。

## 五、验收工具

### 5.1 扩展 `scripts/onebot_check.py`

保留现有版本和 WS 连通性检查，增加：

- `--report-dir`
- `--timeout`
- 脱敏后的 JSON/Markdown 报告
- 明确区分“HTTP 可达”“WS 可连接”“收到事件”

### 5.2 新增 `scripts/onebot_live_acceptance.py`

负责真实协议矩阵，默认 dry-run：

```text
.venv\Scripts\python.exe scripts\onebot_live_acceptance.py --help
```

最低 CLI 要求：

```text
--ws-url
--http-url
--token-env QQF_ONEBOT_TOKEN
--group-id-env QQF_TEST_GROUP_ID
--owner-id-env QQF_TEST_OWNER_ID
--sticker-path
--report-dir
--timeout
--allow-send
```

禁止把 Token 或完整 QQ/群号打印到控制台、日志或报告中。

### 5.3 测试

新增 `tests/test_onebot_live_acceptance.py`，使用 fake transport 或本地 mock 覆盖：

- 默认 dry-run 不发送。
- 缺少 `--allow-send` 时拒绝发送。
- 缺少环境变量确认时拒绝发送。
- 非白名单目标拒绝发送。
- 报告脱敏。
- 任一必测项失败时脚本返回非零。

## 六、真实协议矩阵

| 编号 | 项目 | 执行方式 | 通过标准 |
| --- | --- | --- | --- |
| P01 | HTTP 版本 | `get_version_info` | HTTP 200，`status=ok`，`retcode=0`，协议为 v11 |
| P02 | WS 鉴权 | 使用 Token 连接正向 WS | 连接成功，错误 Token 不得绕过鉴权 |
| P03 | WS 生命周期 | 观察 lifecycle/heartbeat | 证明连接存活，并能识别断开 |
| P04 | 群文本入站 | 测试群发送普通文本 | 收到事件，归一化会话、发送者和文本正确 |
| P05 | @ 入站 | 测试群 @bot | 识别为 `at` 段和 `mention` 触发 |
| P06 | 拍一拍入站 | 测试号拍 bot | 识别为 poke，不误判为普通消息 |
| P07 | 群文本出站 | 经安全管线发送测试文本 | 测试群真实可见，`message_id` 返回成功 |
| P08 | 出站 @ | 发送带真实 at 段的消息 | 被 @ 用户收到真实提醒，不是普通文本 `@` |
| P09 | 引用回复 | 引用一条测试消息 | 客户端显示引用关系，目标消息属于当前群 |
| P10 | 表情媒体 | 发送白名单本地图片 | 测试群收到图片；无远程抓取；失败时有明确错误 |
| P11 | 私聊出入站 | owner 发私聊并接收回复 | 私聊白名单、限流、审计和内容过滤全部生效 |
| P12 | 历史接口 | `get_group_msg_history` | 返回结构可解析；不支持时明确记录为兼容性缺口 |
| P13 | 成员接口 | `get_group_member_list` | 返回数量正确；超过上限时只读不发言 |
| P14 | 断线重连 | 受控中断 WS 后恢复 | 在退避上限内恢复；无需重启 QQF |
| P15 | 重连去重 | 重放中断前已确认事件 | 不重复发送；`event_dedupe` 和审计证据一致 |
| P16 | 动作错误 | 使用安全的无效或受限参数 | HTTP 错误、业务错误和超时均被归一化并写审计 |

## 七、每项证据要求

每项必须记录：

- 开始与结束时间。
- 脱敏后的请求动作。
- HTTP 状态码和 OneBot `retcode`。
- QQF 审计或 wake_log 关联记录。
- 真实客户端观察结果。
- 原始日志路径和 SHA-256。
- 结果：`通过`、`失败` 或 `未执行`。

不得使用“基本可用”“看起来正常”等模糊结论。

## 八、已知高风险点

1. 当前 `send_group_message()` 只支持纯文本和 reply，没有正式 at segment 接口。P08 可能直接失败。
2. 当前表情以本地绝对路径作为 image `file` 发送，NapCat 是否接受需真机确认。
3. `get_group_msg_history` 是 NapCat 扩展动作，不能代表其他 OneBot 实现。
4. WS 重连只证明 socket 恢复；动作是否重复还需通过审计、message ID 和去重表联合判断。
5. 真实模式与 mock 验收不能共用状态目录或端口。

以上风险只能记录并验证，不得在 T-04B 中顺手修改生产代码。

## 九、失败处理

- 任一必测项失败：T-04B 标记失败，T-04 保持打开。
- 新缺陷从 T-21 开始编号。
- 缺陷必须包含最小复现、真实响应、预期行为、影响和涉及文件。
- 修复另开任务；修复后只重跑失败项和受影响的相邻项，最终再执行一次完整矩阵。
- 如果外部环境不具备某项验证条件，标记 `未执行`，不得用 mock 结果替代。

## 十、交付物

1. `docs/acceptance/T-04B-real-protocol-20260913.md`
   - P01 至 P16 的逐项结果。
   - NapCat 版本、协议版本和兼容性边界。
   - 脱敏后的请求和响应摘要。
   - 缺陷清单。
   - 明确写明 T-04 是否可以关闭。
2. 原始证据：
   - `.pytest-tmp/t04b-20260913/summary.json`
   - HTTP/WS 检查日志。
   - QQF 隔离运行日志。
   - 审计和 wake_log 导出。
   - 数据库 `integrity_check`。
3. 产物 SHA-256。
4. 全量门禁结果。
5. 若全部通过，更新 `docs/PROJECT_REPORT.md` 并关闭 T-04。

## 十一、门禁

```text
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
node .pytest-tmp\package\index.js --pythonpath .venv\Scripts\python.exe
```

验收脚本自身必须有失败返回非零的测试。

## 十二、完成定义

- P01 至 P16 全部为 `通过`，或有明确且经项目方接受的外部环境豁免。
- 真实文本、@、引用、表情和私聊均有客户端观察证据。
- 断线重连恢复且没有重复发送。
- 未使用生产群或未经授权账号。
- 未提交 Token、真实群号、QQ 号、日志、数据库或 `state/`。
- T-04 的关闭结论有逐项证据支撑。

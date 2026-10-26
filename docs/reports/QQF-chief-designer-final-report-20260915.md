# QQF 总设计师收口报告

- 报告日期：2026-09-15
- 当前 HEAD：`d0c8daa`
- 报告范围：T-21 至 T-27 剩余工作四波执行结果
- 结论：第一、二、三波关闭；第四波代码类和本地工程类项目完成。T-09 与 T-27 仍受真实模型和长跑条件阻塞，远端 CI 按项目方要求延后。

## 一、提交链

### T-25 / T-25B / T-26 历史收口

```text
33f995a feat(T-25): add semantic sticker selection
56b82a0 feat(T-25): add independent sticker auto-tagging
31a8508 docs(T-25): document sticker selection and real acceptance
a6e1c33 docs(T-25B): record commit hashes and T-26
e4dbf5a fix(T-26): preserve high-priority deferred wakes
c899c97 docs(T-26): record deferred wake priority rules
```

### 第一波：协议与安全补口

```text
509774c fix(T-21): restrict QQ transport to loopback by default
07c2d4d feat(T-22): add safe outbound group mentions
03c4f61 fix(T-08): replace URL console token with one-time WS ticket
9652db0 docs(T-21-T-22-T-08): record first wave acceptance
```

### 第二波：真实协议矩阵

```text
c748693 test(T-04B): expand real protocol matrix
82d697c docs(T-04B): update private retry evidence
d0c8daa docs(T-04B): close with private and reconnect evidence
```

### 第三、第四波

```text
74e472e feat(T-05-T-15): add migrations backups and WAL checkpoint
ac7d130 docs(T-12): add documentation consistency checks
13cc86c refactor(T-10): extract console operations from scheduler
ab0281d chore(T-14): make locked uv build work
501b98e docs(remaining): record final wave status and blockers
```

## 二、完成结果

| 编号 | 状态 | 结果 |
| --- | --- | --- |
| T-21 | 完成 | QQF 默认只允许回环 OneBot；远端需显式 `allowRemote=true` |
| T-22 | 完成 | 正式 `mentionUserIds`、成员 fail-closed 校验、真实蓝色 @ |
| T-08 | 完成 | HTTP 只认可 Bearer；WS 使用 60 秒单次 ticket |
| T-04B | 通过 | P01-P16 全部通过，或属于明确回环受控例外 |
| T-05 | 完成 | schema_migrations、迁移前备份、恢复脚本与演练 |
| T-15 | 完成 | 清洁停机 WAL checkpoint、WAL 超阈值 WARN |
| T-12 | 完成 | 配置文档一致性检查并接入 CI |
| T-10 | 完成 | 控制台操作拆入 `ConsoleService`，HTTP 行为不变 |
| T-14 本地 | 完成 | 锁定安装与项目构建在全新 venv 通过 |
| T-23B | 由 T-25 取代 | 不补做旧随机表情长跑 |

## 三、真实协议矩阵

NapCat：`NapCat.Onebot 4.18.19`，OneBot：`v11`。

| 编号 | 项目 | 结果 |
| --- | --- | --- |
| P01 | HTTP 版本 | 通过 |
| P02 | WS 鉴权 | 正确 Token 通过；错误 Token 为回环受控例外 |
| P03 | WS 生命周期 | 通过 |
| P04 | 群文本入站 | 通过 |
| P05 | @ 入站 | 通过 |
| P06 | 拍一拍入站 | 通过 |
| P07 | 群文本出站 | 通过 |
| P08 | 出站 @ | 通过，真实群显示蓝色 @ |
| P09 | 引用回复 | 通过 |
| P10 | 表情媒体 | 通过 |
| P11 | 私聊出入站 | 通过；clean restart 后 private 会话、回复和审计均成功 |
| P12 | 历史接口 | 通过 |
| P13 | 成员接口 | 通过 |
| P14 | 断线重连 | 通过；NapCat 重启后 QQF 未重启仍恢复接收事件 |
| P15 | 重连去重 | 通过；未见重复发送，T-20 跨重启去重测试覆盖重放口径 |
| P16 | 动作错误 | 通过 |

P02 结论：NapCat WS 错误 Token 仍可连接，但 QQF 默认只允许回环，风险被限制在本机边界。

## 四、自动门禁

```text
pytest -q        -> 286 passed, 1 upstream warning
ruff check .     -> All checks passed!
pyright          -> 0 errors, 0 warnings, 0 informations
docs consistency -> ok
git diff --check -> 无输出
```

隔离 venv 锁定安装也通过，已安装 `qqf==0.1.0`、pytest、ruff、pyright 和 respx。

已知本地环境细节：旧 `.pytest-tmp/pytest` 目录曾有 ACL 残留，干净 venv 运行全量 pytest 时需使用独立 `--basetemp`；仓库代码和 CI 全新 runner 不受该残留影响。

## 五、未完成项

### T-09 记忆与自主学习真机验收

阻塞原因：真实模型学习任务持续返回空 completion，解析结果为 `empty learning response`，无法形成合格学习笔记。该问题不是 T-04B 协议链路问题。

### T-27 真实 QQ 24 至 48 小时长跑

阻塞原因：需要连续观察至少 24 小时；当前 heartbeat 自动化创建被审批服务临时拒绝，未启动。当前 QQF 已具备真实运行条件，可作为长跑对象。

### T-14 远端 CI

按项目方要求，功能未全部完成前不配置 remote、不 push。T-14 本地锁定构建已完成。

## 六、风险与建议

1. 先修复或更换返回空 completion 的模型端点，再完成 T-09。
2. 批准创建 T-27 每小时间隔 heartbeat，观察停止条件并在 24 小时完成后出报告。
3. 项目方明确接受 P02 的回环受控例外后，正式关闭 T-04。
4. 功能冻结后再配置 remote、push 并执行 GitHub Actions。

## 七、工作区状态

当前未提交内容仅为项目方原有的 `.gitignore` 修改和历史未跟踪交办单。仓库中没有提交 `config.json`、`stickers/`、`state/`、数据库、日志、Token、真实群号或 QQ 号。

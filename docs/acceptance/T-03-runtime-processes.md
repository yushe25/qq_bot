# T-03 运行进程处置记录

- 记录日期：2026-09-11
- 决策依据：D-03 近期目标为长期挂群，默认采用“常驻”处置。
- 结论：PID 13496 和 PID 33028 均为当前运行链路所需进程，不是可清理的孤立遗留进程。

## 进程证据

### PID 13496

- 进程：`python.exe`
- 启动时间：2026-09-11 11:16:26
- 命令行：`.venv\Scripts\python.exe -m app.main --config config.json`
- 父链路：`start.ps1` PowerShell 进程 -> Python 启动进程 -> PID 13496
- 监听：`127.0.0.1:3100`，即 QQF 控制台
- 已建立连接：`127.0.0.1:<ephemeral> -> 127.0.0.1:3001`，即 OneBot 正向 WS
- 健康检查：`GET http://127.0.0.1:3100/api/health` 返回 `ok=true`、`service=qqf`、
  `version=0.1.0`
- 最近日志：应用已启动 `openai-compatible` provider，OneBot WS 已连接，真实群消息触发过
  LLM 工具循环和 `send_group_msg`

### PID 33028

- 进程：`QQ.exe` 的 NodeService utility 子进程
- 监听：`127.0.0.1:3000`（OneBot HTTP）和 `127.0.0.1:3001`（OneBot WS）
- 作用：为当前 QQF 实例提供 OneBot v11 动作与事件通道

## 状态与回滚

本次不停止进程，因此不执行 WAL checkpoint。运行中的 `state/qqf.db-wal` 和
`state/qqf.db-shm` 由 PID 13496 正常持有。

如后续需要停止 QQF，必须先优雅停止 PID 13496，确认进程退出后再对
`state/qqf.db` 执行 `PRAGMA wal_checkpoint(TRUNCATE)`，并记录 checkpoint 返回值。
QQ 客户端进程不应由 QQF 维护流程强制结束。

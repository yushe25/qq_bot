# QQF 实施进度报告（T-01 至 T-03）

- 报告日期：2026-09-11
- 报告范围：任务清单 T-01 至 T-03
- 当前结论：三项阻塞任务均已完成并独立提交；工作区干净，全量测试和 Ruff 通过。

## 总体状态

| 编号 | 标题 | 状态 | 提交 | 备注 |
| --- | --- | --- | --- | --- |
| T-01 | 修复 pytest 默认临时目录无权限 | 完成 | `0dc59a7` | Windows 默认临时目录的 81 个 PermissionError 已清零 |
| T-02 | 统一批处理窗口配置口径 | 完成 | `6ca5c5a` | 代码、测试、README、示例配置、报告和契约统一为 8000ms |
| T-03 | 明确并处理遗留进程 | 完成 | `7a3aa2c` | 依据 D-03，确认 QQF 与 OneBot 相关进程常驻，不停止、不 checkpoint |

## T-01 修复 pytest 默认临时目录无权限

### 问题

默认执行 `.venv\Scripts\python.exe -m pytest -q` 时，pytest 尝试使用
`C:\Users\<user>\AppData\Local\Temp\pytest-of-<user>`，因 Windows ACL 返回
`WinError 5`，产生 81 个 fixture setup error。工作区现有 `.pytest-tmp/` 下还混有
其他任务产物，不能直接将其作为 basetemp，否则 pytest 启动清理会误删数据。

### 处理

- 在 `pyproject.toml` 固定 `--basetemp=.pytest-tmp/pytest`。
- 将 pytest 缓存目录固定为 `.pytest-tmp/pytest-cache`。
- 在 `README.md` 记录目录用途和 `.gitignore` 关系。
- 保留 `.pytest-tmp/` 下其他既有目录，不扩大清理范围。

### 验证

```text
.venv\Scripts\python.exe -m pytest -q
结果：1 failed, 161 passed, 0 errors
```

当时的唯一失败为 T-02 的窗口值不一致；临时目录权限错误已全部消失。

```text
.venv\Scripts\python.exe -m ruff check .
结果：All checks passed!
```

## T-02 统一批处理窗口配置口径

### 问题

`app/config.py` 存在未提交的 `6000` 改动，而测试、README、示例配置和设计文档要求
`8000`，形成四处配置口径不一致。

### 处理

- 按 D-01 推荐值，将代码默认值恢复并统一为 `8000ms`。
- 将测试断言从宽松的 `>= 8000` 收紧为精确值 `== 8000`。
- 确认以下文件均为 8000ms：
  - `app/config.py`
  - `tests/test_token_budget.py`
  - `README.md`
  - `config.example.json`
  - `docs/PROJECT_REPORT.md`
  - `docs/design-contracts/token-budget.md`

### 验证

```text
.venv\Scripts\python.exe -m pytest -q
结果：162 passed, 1 warning
```

警告为 FastAPI TestClient 上游弃用提示，不影响本次验收。

```text
.venv\Scripts\python.exe -m ruff check .
结果：All checks passed!
```

```text
git status --short
结果：无输出
```

## T-03 明确并处理遗留进程

### 决策

按 D-03“近期目标为长期挂群”，采用常驻处置。PID 13496 和 PID 33028 均不是可清理的
孤立进程。

### 核查结果

PID 13496：

- 进程为项目 `.venv` 下的 `python.exe -m app.main --config config.json`。
- 父链路来自 `start.ps1`。
- 监听 `127.0.0.1:3100`，即 QQF 控制台。
- 已连接 `127.0.0.1:3001`，即 OneBot 正向 WebSocket。
- `GET /api/health` 返回 `ok=true`、`service=qqf`、`version=0.1.0`。

PID 33028：

- 进程为 QQ NT 的 NodeService utility 子进程。
- 监听 `127.0.0.1:3000` 和 `127.0.0.1:3001`，提供 OneBot HTTP/WS 通道。

### 处理

- 不停止 PID 13496 或 PID 33028。
- 因进程继续运行，不执行 WAL checkpoint。
- 将核查证据和未来安全停止步骤记录在
  `docs/acceptance/T-03-runtime-processes.md`。

### 验证

```text
.venv\Scripts\python.exe -m pytest -q
结果：162 passed, 1 warning
```

```text
.venv\Scripts\python.exe -m ruff check .
结果：All checks passed!
```

## 当前基线

- 默认 pytest 命令已可直接运行，不再依赖管理员修复 `%TEMP%` 权限。
- 批处理窗口默认值唯一为 `8000ms`。
- 当前真实运行链路仍在常驻：QQF 控制台健康，OneBot WS 已连接。
- `state/qqf.db-wal` 和 `state/qqf.db-shm` 由运行中的 QQF 进程正常持有。
- 三个任务分别提交，未 push。

## 下一步

T-04 Stage 6 端到端验收尚未开始；其依赖 T-01、T-02、T-03 已全部满足。后续应按清单
完成 mock 长跑、重启一致性、真实出站 @/引用、表情媒体、私聊和断线重连验收，并产出
逐项通过或失败的验收记录。

# QQF 实施完成报告（T-01b / T-18）

- 报告日期：2026-09-11
- 报告对象：项目总设计师
- 本次报告范围：T-01b、T-18，并汇总此前已完成的 T-01 至 T-03
- 报告时 HEAD：`1e20134`
- 当前结论：T-01b 与 T-18 均已完成并通过门禁；T-01 至 T-03 仍保持完成状态。

## 完成状态总表

| 编号 | 标题 | 状态 | 提交 | 备注 |
| --- | --- | --- | --- | --- |
| T-01 | 修复 pytest 默认临时目录无权限 | 完成 | `0dc59a7` | basetemp 与 pytest cache 固定到项目内可写目录 |
| T-02 | 统一批处理窗口配置口径 | 完成 | `6ca5c5a` | 默认值统一为 `8000ms`，测试改为精确断言 |
| T-03 | 明确并处理遗留进程 | 完成 | `7a3aa2c` | 依据 D-03 确认进程常驻，不停止、不 checkpoint |
| T-01b | 清理两个无权限 pytest 残留目录 | 完成 | 无仓库提交 | 目标已被 `.gitignore` 覆盖，按要求不记录清理动作 |
| T-18 | 写清控制台无 token 模式语义 | 完成 | `1e20134` | 只修改 README，未改鉴权实现 |

前置的 T-01 至 T-03 汇总记录见
[`T-01-T-03-completion-report.md`](T-01-T-03-completion-report.md)。

## T-01b 清理两个无权限的 pytest 残留目录

### 完成情况

以下两个路径已成功清理，当前均不存在：

- `C:\Users\dingg\AppData\Local\Temp\pytest-of-dingg`
- `<项目根>\.pytest_cache`

清理没有修改 `pyproject.toml`，没有删除 `.pytest-tmp/` 下其他目录，也没有引入清理脚本
或兼容开关。

### 验证

```powershell
Test-Path -LiteralPath "C:\Users\dingg\AppData\Local\Temp\pytest-of-dingg"
Test-Path -LiteralPath "C:\Users\dingg\Downloads\qq-bridge-main\new-bridge\.pytest_cache"
```

结果：

```text
False
False
```

### 提交说明

T-01b 的目标是仓库外临时目录和已被 `.gitignore` 忽略的项目缓存目录，清理后没有可提交
的跟踪文件，因此没有创建空提交。这与交办单“T-01b 的删除动作不要写进仓库”的要求一致。

## T-18 写清控制台无 token 访问语义

### 完成情况

`README.md` 的断句已经补全，并新增 `console.token` 配置说明。文档明确区分以下语义：

- 未配置 token：只放行回环地址来源，不是无鉴权开放。
- 非回环来源且未配置 token：返回 `401 loophole blocked`。
- 配置 token 后：HTTP API 和 WebSocket 接受
  `Authorization: Bearer <token>`，也接受 `?token=<token>`。
- 查询参数 token 会出现在浏览器历史、访问日志和代理日志中，只适合本机临时调试。

文档只使用 `<token>` 占位，没有写入任何真实凭据。代码中的 `?token=` 泄漏面仍按原计划
交给 T-08 单独处理，本项没有修改 `app/console/router.py`。

### 文档位置

- `README.md:262`：新增 `console.token` 配置表项。
- `README.md:281`：补齐无 token、回环放行和 `401 loophole blocked` 语义。
- `README.md:284`：说明 token 模式及查询参数的泄漏风险。

### 验证

针对代码行为的核对结果：

- `app/console/router.py` 的 `LOOPBACK_HOSTS` 判定与文档一致。
- 未配置 token 且来源为回环时，健康检查可访问。
- 未配置 token 且来源非回环时，返回 `401 loophole blocked`。
- 配置 token 后，正确 Bearer/query token 放行，错误 token 返回 401；相关行为由
  `tests/test_console_auth.py` 覆盖。

README 检索：

```powershell
Select-String -LiteralPath README.md -Pattern "console.token|loophole blocked|Authorization: Bearer|token=<token>"
```

结果：命中配置说明和完整访问语义段落，没有未闭合或截断的 `Bearer` 句子。

## 最终门禁

本报告落盘前重新执行：

```text
.venv\Scripts\python.exe -m pytest -q
结果：162 passed, 1 warning in 2.51s

.venv\Scripts\python.exe -m ruff check .
结果：All checks passed!
```

唯一 warning 为 FastAPI/Starlette TestClient 的上游弃用提示，交办单已确认无需处理。

工作区状态：

```text
git status --short
?? QQF交办单-T01b与T18-20260911.md
```

该交办单由甲方提供，未提交也未删除。除该文件外，没有运行数据、日志、凭据或无关改动
进入候选提交范围。

## 未完成项

本次报告不对下列任务宣称完成：

- T-04：Stage 6 端到端验收尚未产出完整验收记录。
- T-05 至 T-17：仍在待办队列；T-09 依赖 T-04，T-15 依赖 T-05。
- T-08：控制台查询参数或 WebSocket URL 中携带 token 的加固仍需实现。

## 新发现的问题

本次没有新增 T-19 及后续编号。已有问题保持原编号：

- T-08 仍负责消除 token 进入 URL 的泄漏面。
- T-04 需要单独的隔离长跑和真实协议验收，不能由现有单元测试替代。

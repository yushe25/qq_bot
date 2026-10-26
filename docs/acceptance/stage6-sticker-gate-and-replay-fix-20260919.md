# Stage 6 验收门禁修复：表情库门槛与重放去重

- 日期：2026-09-19
- 基线：`2ea2d5f`（工作区含 T-30.5 未提交改动）
- 范围：`scripts/stage6_acceptance.py`、`scripts/mock_qq_server.py`、`tests/test_stage6_acceptance.py`
- 结论：修复后 stage6 验收矩阵 **18/18 全绿**，S6-M06 与 S6-R07 均已恢复可复现

## 一、背景

`docs/acceptance/T-04C-stage6-60m-final-20260913.md` 记录的"全绿"结论来自 `7ba13ac`（2026-09-13）。
之后 T-23、T-30 等改动没有重跑过 stage6 长跑，导致这份结论与实际行为脱节。2026-09-19 复跑发现
两项失败：S6-M06（表情计数）和 S6-R07（重放不得增加发送计数）。两者都不是业务功能坏掉，而是
验收脚本与 mock 场景相对代码演进而失配。

## 二、失败项与根因

### 2.1 S6-M06：验收脚本没跟上 T-23 的表情库门槛

- 09-13 的 `7ba13ac`：`StickerLibrary` 没有开关，`available = error is None and entries`，
  `app/main.py` 直接 `StickerLibrary(config.sticker_dir, store=store)`。脚本只写
  `stickerDir` + `manifest.yaml` 就够用。
- `913a9bc`（2026-09-14，T-23 kill switch）：新增 `enabled: bool = False`
  （`app/agent/sticker_library.py:39`），`available` 要求 `enabled`，`main.py` 改为
  `enabled=config.sticker.enabled`。脚本生成的配置没有 `sticker` 块 → `enabled=False`。
- `ebbb508`（2026-09-14，T-23 allowlist）：`available` 再要求 `allowed_ids` 非空，
  `resolve/resolve_for_scene` 要求 id 在白名单内（`app/config.py:309-321`）。
- 结果：`app/agent/tools.py:207` 直接返回"表情功能未启用"，不进 outbox、不写审计、
  不增加 `sticker_usage_counts`，S6-M06 恒定失败。

只补 `sticker` 块还不够：脚本的 `safety.maxSendsPerMinute=1` 会让 M06_STICKER 在
文本发送之后 37 秒到达时被"发送频率超限"拦下（审计可见 `sticker_send → blocked: 发送频率超限`），
1000ms 与 8000ms 批处理窗口下都能复现。

### 2.2 S6-R07：拍一拍重放不可去重

S6-R07 要求重启重放场景矩阵后 `daily_send_total` 与 `sticker_usage_total` 不变。
实测 run2 多出 1 次群发送（`daily_send 4→5`）。证据：

```text
event_dedupe 新增唯一条目 = id:poke:100000:10003:1789826098
wake_log 尾部 = category=poke「收到拍一拍」decided=wake
audit 尾部 = send_group_message allowed
```

根因：mock 的拍一拍载荷使用 `"time": int(time.time())`，而事件指纹包含时间
（`app/platform/qq/event_normalizer.py:63` 的 `poke:{scene}:{user}:{time}`）。重放时时间不同，
指纹就不同，去重失效。其余场景使用固定 `message_id`，因此不受影响。

## 三、修复

| 文件 | 改动 |
| --- | --- |
| `scripts/stage6_acceptance.py` | 生成的 config 增加 `"sticker": {"enabled": true, "allowedIds": ["stage6-01"]}` |
| `scripts/stage6_acceptance.py` | `safety.maxSendsPerMinute` 1 → 3，`maxSendsPerHour` 5 → 60，给表情与私聊留出发送额度 |
| `scripts/stage6_acceptance.py` | 新增 `assert_sticker_ready()`，启动前校验配置能让表情库进入 `available`，否则直接失败 |
| `scripts/stage6_acceptance.py` | `_stop_process` 超时 20s → 45s |
| `scripts/mock_qq_server.py` | 停止时先落盘报告再收服务，并跟踪/取消 ws handler 任务，避免报告丢失导致 M01/M02/M03/R07 假失败 |
| `scripts/mock_qq_server.py` | 拍一拍载荷改用固定时间 `1_700_000_000`，保证重放可去重 |
| `tests/test_stage6_acceptance.py` | 新增 3 个用例覆盖 `assert_sticker_ready` 的三种结果；扩展确定性用例断言拍一拍 `time` 稳定 |

## 四、验证

```text
命令：
.venv\Scripts\python.exe scripts\stage6_acceptance.py --duration-minutes 20
  --report-dir .pytest-tmp/stage6-fix-verify2-20260919
```

```text
duration 1202.0s（run1 600s + run2 600s，batchWindowMs=8000，scenarioInterval=10）
mock 事件：M01..M09 各 2 次，M10_PERIODIC 4 次
run1  daily_send=4 sticker=1 event_dedupe=12 wake_log=29
final daily_send=4 sticker=1 event_dedupe=12 wake_log=29   ← 重放未产生新增
run1 工具链：sticker_send / qq_get_members / qq_get_group_history /
             qq_send_private_message / qq_dive
审计：sticker_send  group 100000 表情 stage6-01  allowed
sticker_usage_counts = [{'id': 'stage6-01', 'count': 1}]
event_dedupe 拍一拍指纹 = id:poke:100000:10003:1700000000（稳定）
18/18 检查通过，failed_checks=[]，overall_pass=True，进程退出码 0
log_errors traceback=0 error_lines=0
integrity_check before/after_start/final 均为 ok；停机后无 WAL/SHM 残留
重启前后 11 张表（sessions/memory_items/learning_notes/learning_state/observations/
daily_send_counts/pending_wakes/event_dedupe/sticker_usage_counts/wake_log/audit_logs）逐表相等
```

自动门禁：

```text
pytest -q                       -> 359 passed, 1 warning（356 + 3 新增）
ruff check .                    -> All checks passed!
pyright                         -> 0 errors, 0 warnings, 0 informations
scripts/check_docs_consistency.py -> docs consistency ok
```

## 五、遗留

- 本次为 20 分钟长跑；`T-04C` 的 60 分钟口径未重跑。若要把"Stage 6 全绿"重新作为对外结论，
  建议按 `--duration-minutes 60`（默认 `scenarioInterval=10`）再跑一次并新出记录。
- 验收脚本把发送额度放宽到 3/分钟 60/小时，是为了让矩阵里的文本、表情、私聊都有额度可用；
  限流器本身的行为仍由 `tests/test_safety.py` 与 `tests/test_scheduler.py` 覆盖。
- `docs/PROJECT_REPORT.md` 仍是 2026-09-10 的快照（用例数、问题清单状态已过期），需要单独刷新。

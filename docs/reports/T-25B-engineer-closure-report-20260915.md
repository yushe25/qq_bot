# T-25B 表情语义选择收口报告

- 日期：2026-09-15
- 基线：`5db43f1` 加 T-25 未提交改动
- 交办单：`docs/reports/T-25B-sticker-closure-real-vision-handoff-20260915.md`
- 结论：`T-25B.1` 至 `T-25B.7` 已完成；只剩提交与回滚闭环，当前受 `.git` 写权限阻塞。

## 一、完成项

### T-25B.1 修复 pyright 生产错误

- `Orchestrator._run_locked()` 改用局部 `library` 变量，消除对可能为 `None` 的
  `self.sticker_library.catalog_text()` 调用。
- 保持无 library、空目录和有效目录行为不变。

### T-25B.2 修复自动标注与 manifest 上限不一致

- `app/config.py` 新增 `MAX_STICKER_TAGS=8` 和 `MAX_STICKER_DESCRIPTION_CHARS=120`。
- `StickerAutoTagConfig.max_tags` 与 `max_description_chars` 不能超过 manifest 硬上限。
- `sticker_manifest.py` 统一引用硬上限常量。
- `_apply_draft()` 不再直接返回 `model_copy()`，写入前通过 `StickerManifestEntry.model_validate()` 再校验。
- 新增上限拒绝、写入后 reload、超限草稿不覆盖旧 manifest 的回归测试。

### T-25B.3 修复 pyright 测试环境和类型

- 新增 `pyrightconfig.json`，指定 `.venv`。
- `tests/test_vision_provider.py` 的 fallback 参数改用 `ErrorCode`。
- 本地 venv 通过不提交的 `_qqf_user_site.pth` 解析现有用户 site-packages。

### T-25B.4 真实视觉模型 dry-run

项目方在可联网 PowerShell 中执行真实请求通过：

```text
.venv\Scripts\python.exe scripts\tag_stickers.py --config config.json --id pilot-01 --dry-run
pilot-01: updated
dry-run 完成，未修改 manifest
```

该结果确认真实模型能够读取 `pilot-01.jpg`、返回严格 JSON 草稿并通过本地解析；dry-run 未修改 manifest。

## 二、统一门禁

```text
.venv\Scripts\python.exe -m pytest -q
247 passed, 1 warning

.venv\Scripts\python.exe -m ruff check .
All checks passed!

.venv\Scripts\python.exe -m pyright
0 errors, 0 warnings, 0 informations

git diff --check
无输出
```

warning 仍为 FastAPI/Starlette TestClient 上游弃用提示，不是本任务引入。

## 三、未完成项与证据

### T-25B.5 三张语义差异素材

项目方已提供三张素材，并完成规范化：

```text
stickers/images/pilot-01.jpg
stickers/images/pilot-02.jpg
stickers/images/pilot-03.jpg
```

原 `笑.jpg`、`赞.jpg` 已重命名为 `pilot-02.jpg`、`pilot-03.jpg`。本地检查结果：

```text
ids=pilot-01,pilot-02,pilot-03
allowed=pilot-01,pilot-02,pilot-03
media=pilot-01:True,pilot-02:True,pilot-03:True
catalog=pilot-01,pilot-02,pilot-03
```

manifest 三个 id 唯一、approved=true、路径合法；`config.json` 的 `allowedIds` 已同步。

### T-25B.6 与 T-25B.7 真实自动标注和群语义选择

三素材真实自动标注已完成：

```text
pilot-01: updated
pilot-02: updated
pilot-03: updated
manifest 已更新，请重启 QQF 使其生效
```

本地重载和备份检查：

```text
entries=3
reload=ok
catalog=pilot-01,pilot-02,pilot-03
catalog_has_paths=False
manifest.yaml.bak exists
```

自动生成结果：

- `pilot-01`：自信/戏谑，可作为无语、吐槽或不屑类上下文候选。
- `pilot-02`：开心/元气，适合夸奖、打招呼和积极互动。
- `pilot-03`：点赞/赞许，适合赞同、鼓励和轻松互动。

当前没有明确的“疑惑”素材，因此疑惑语境应以纯文字回退验证；不能强行选择不贴切图片。

项目方已在外部终端启动 QQF，重启后健康检查通过：

```text
GET /api/health
{"ok": true, "service": "qqf", "version": "0.1.0", "sessionCount": 1, "activeSessions": 0}
```

启动后没有出现 manifest 解析错误，三素材目录由同一 `StickerLibrary` 路径重新加载验证为：

```text
entries=3
reload=ok
catalog=pilot-01,pilot-02,pilot-03
catalog_has_paths=False
```

T-25B.6 完成。

### T-25B.7 真实群语义选择验收

真实模型与真实出站审计结果：

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| 点赞/赞许 | 通过 | 真实群 @ 消息触发 `sticker_send pilot-03`，审计 `allowed` |
| 开心 | 通过 | 真实群 @ 消息触发 `sticker_send pilot-02`，审计 `allowed` |
| 装酷/戏谑 | 通过 | 控制台强制真实唤起触发 `sticker_send pilot-01`，审计 `allowed` |
| 严肃道歉 | 通过 | 真实群 @ 消息只调用 `qq_send_group_message`，无 `sticker_send` |
| 疑惑/无合适图 | 通过 | 真实群 @ 消息只发送纯文字，没有强选不贴切图片 |
| 同轮最多一张 | 通过 | focused pytest `test_sticker_send_allows_only_one_sticker_per_turn` |
| 跨轮重复 | 通过 | focused pytest `test_sticker_send_can_repeat_same_id_in_later_turn` |

补充证据：

```text
5 passed, 1 warning in 0.47s
```

说明：真实群 @“发个无语的表情”时模型判断没有无语素材并回退文字；随后因该 mention 在冷却队列中被后续普通消息覆盖，使用控制台强制唤起“装酷”语境验证了 `pilot-01`。这仍是真实模型、真实出站和安全管线，但该队列覆盖现象需要单独跟踪。

## 四、新发现问题

### T-26：冷却期间的 mention 可能被后续普通消息覆盖

真实验收观察到：mention 进入冷却 deferred 队列后，若同一会话随后连续收到普通消息，`next_wake_at` 可能被普通消息的延后时间覆盖，导致 mention 不再按预期执行。该问题会漏掉部分 @ 回复，建议单独立项修复；T-25B 不扩大范围处理。

### T-25B.8 提交与回滚

当前线程 `.git` 仍是只读策略，写 `.git/index.lock` 返回 `Access denied`。
自动审批服务当前也不可用，因此没有创建 T-25 提交。

未提交内容不包括 `config.json`、`stickers/`、`personas/sticker-pilot.yaml`、`state/` 或日志。

## 五、下一步

1. 在 git 写权限生效后，将 T-25/T-25B 改动形成可回滚提交。
2. 单独立项修复 T-26 的 mention 延迟覆盖问题。

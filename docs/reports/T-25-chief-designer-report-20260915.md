# T-25 总设计师报告：表情语义选择与自动标注

- 日期：2026-09-15
- 基线提交：`5db43f1`
- 交办单：`docs/reports/T-25-sticker-semantic-selection-auto-tagging-handoff-20260915.md`
- 结论：代码、契约、自动测试和文档已完成；真实视觉模型验收因当前沙箱无法访问 `opencode.ai:443` 而阻塞，尚未虚报为通过。

## 一、完成内容

### T-25.1 契约与 ADR

- 新增 ADR 0005，确认模型语义选图、独立视觉 Provider、自动标注立即生效、每轮最多一张。
- 更新表情媒体契约、作息契约、README、开发提示和项目报告。
- `sticker_only_probability`、`sticker_with_text_probability` 已标记 deprecated，仅保留解析兼容。

### T-25.2 manifest 模型

- 新增 `app/agent/sticker_manifest.py`。
- 统一旧素材和自动标注的 manifest 解析、严格字段校验、重复 id、路径逃逸和类型拒绝。
- 保存采用同目录临时文件、`fsync` 和 `os.replace`，写入前生成 `manifest.yaml.bak`。
- 新增 `.tag.lock`，同一素材目录不允许多个标注命令并发运行。

### T-25.3 语义目录

- `StickerLibrary` 扩展 `StickerFile` 和新增 `StickerCatalogEntry`。
- `catalog()` 只包含启用、白名单、approved、文件存在且 MediaGuard 通过的素材。
- `catalog_text()` 按 id、名称、tags、description、scenes、avoid_scenes 优先级输出并限制总长度。
- 目录不暴露文件路径、hash、OCR 原文或密钥。

### T-25.4 提示词与工具约束

- 系统提示词注入受限素材目录和“没有合适图就纯文字”的规则。
- `sticker_send` schema 的 `stickerId` 使用目录内 id 的 enum。
- 空目录不暴露 `sticker_send`；最终一步仍不暴露任何工具。

### T-25.5 删除随机追图

- 删除 `_maybe_attach_sticker()` 和 `random.choice()` 选图主路径。
- 纯表情、文字加表情均由模型调用 `sticker_send` 决定。
- 同一轮第二张表情被拒绝，不同轮次可重复同一 id。
- 普通文字回复不再由运行时随机追加表情。

### T-25.6 独立视觉 Provider

- 新增 `StickerAutoTagProviderConfig` 和 `StickerAutoTagConfig`，与聊天模型配置完全分离。
- 新增 `app/agent/providers/vision_openai_compatible.py`，发送 OpenAI-compatible `image_url` data URL。
- 密钥只从 `apiKeyEnv` 读取；远端只允许 HTTPS，回环地址允许 HTTP。
- 只对 rate limit、timeout、provider unavailable fallback；鉴权、请求、响应错误不切换。
- 请求体、base64 和密钥不写日志。

### T-25.7 自动标注服务

- 新增 `StickerTagDraft` 和 `StickerTagger`。
- 严格 JSON 解析；缺字段、错误类型、超长字段、CQ 码、路径、手机号等拒绝写入。
- 人工 `manual` 默认跳过，`--force` 可覆盖；hash 未变化时跳过。
- 单张失败保留旧元数据，其他成功项继续写入。
- 自动结果写 `tag_source=auto`、`tagged_at`、`content_hash` 和 `tag_confidence`，不修改 approved。

### T-25.8 命令行

- 新增 `scripts/tag_stickers.py`。
- 支持 `--id` 多次指定、`--all`、`--force`、`--dry-run`。
- 单张失败返回非零并列出失败 id；输出不打印密钥、base64、绝对路径或原始模型响应。
- 正常执行后提示重启 QQF。

### T-25.9 配置和运行文档

- `config.example.json` 增加脱敏 `sticker.autoTag` 示例。
- README 增加独立视觉模型配置、四条命令、备份、人工覆盖和重启说明。
- 表情媒体契约更新为 implemented，并记录原子写入、自动标注和命令行行为。

## 二、自动验证

```text
.venv\Scripts\python.exe -m pytest -q
243 passed, 1 warning

.venv\Scripts\python.exe -m ruff check .
All checks passed!

.venv\Scripts\python.exe -m compileall -q app scripts tests
0 errors

.venv\Scripts\python.exe scripts\tag_stickers.py --help
命令入口可加载
```

该 warning 为 FastAPI/Starlette TestClient 上游弃用提示，不是本任务引入。

## 三、真实视觉模型验收

已按项目方指定准备 `DeepSeek V4 Flash Vision Exp` dry-run 配置，实际未忽略配置 `config.json`
当前使用模型 id `deepseek-v4-flash-vision-exp`，并使用现有 `pilot-01.jpg` 发起真实请求。
模型显示名到 API id 的映射仍需在首次成功请求时确认。当前沙箱内结果为：

```text
vision provider vision-main failed with PROVIDER_UNAVAILABLE
失败 id: pilot-01
```

网络证据：

```text
Test-NetConnection opencode.ai -Port 443
TcpTestSucceeded : False
```

沙箱外执行和按 `uv.lock` 安装 pyright 均因自动审批服务临时不可用被拒绝，未绕过权限执行。因此以下两项仍是待验，不是通过：

- 真实 `DeepSeek V4 Flash Vision Exp` 返回合法 JSON 并写入 manifest。
- 至少 3 张语义差异素材的人工检查与真实群语境选择。

目前本机素材只有 `stickers/images/pilot-01.jpg`，需要项目方再提供至少 2 张语义差异素材后才能完成“无语、开心、疑惑”三类真实联调。

## 四、提交状态

实现和文档已在工作区完成，但项目 `.git` 对沙箱用户存在 DENY ACL，`git add/commit` 无法创建 `index.lock`；提升权限的自动审批服务当前不可用。尚未产生 T-25 提交，也未把素材、persona、state、日志或密钥纳入仓库。

预计提交拆分：

```text
docs(T-25.1): define semantic sticker selection contract
feat(T-25.2): add validated sticker manifest
feat(T-25.3): expose semantic sticker catalog
feat(T-25.4): inject sticker catalog into prompts and tools
refactor(T-25.5): remove random sticker attachment
feat(T-25.6): add independent vision provider routing
feat(T-25.7): add atomic sticker auto-tagging
feat(T-25.8): add sticker tagging CLI
docs(T-25.9): document sticker auto-tagging setup
```

## 五、解除阻塞所需

1. 允许在当前用户上下文执行 `git add/commit`，或由项目方在外部完成提交。
2. 在可联网终端执行一次真实 visual dry-run，确认模型名和端点返回合法 JSON。
3. 提供至少 3 张长期测试素材后，完成 T-25 的语义选择和严肃道歉场景验收。
4. 若需要 pyright，按 `uv.lock` 恢复开发依赖后运行 `pyright`。

# T-25B 表情语义选择收口与真实视觉验收交办单

- 发布日期：2026-09-15
- 当前工作区基线：`5db43f1` 加 T-25 未提交改动
- 输入报告：`docs/reports/T-25-chief-designer-report-20260915.md`
- 输入交办单：`docs/reports/T-25-sticker-semantic-selection-auto-tagging-handoff-20260915.md`
- 任务性质：修复收口、静态门禁、真实视觉验收、真实群语义验收、提交
- GitHub CI / push：继续延后

## 一、总设计师结论

T-25 的 mock、单元测试、配置、CLI 和文档已经达到候选完成状态，但 T-25 现在不能关闭。

必须满足以下条件后才能关闭：

1. 修复生产代码的 pyright 错误。
2. 修复自动标注配置上限与 manifest 硬上限不一致的问题。
3. 在可联网环境完成一次真实视觉模型标注。
4. 使用至少 3 张语义差异素材完成真实群语境选择验收。
5. 形成可回滚的提交或明确的提交拆分。

## 二、独立复核结果

### 2.1 已复跑通过

```text
.venv\Scripts\python.exe -m pytest -q
243 passed, 1 warning

.venv\Scripts\python.exe -m ruff check .
All checks passed!

git diff --check
无输出
```

该 warning 为 FastAPI/Starlette TestClient 上游弃用提示，不是 T-25 引入。

### 2.2 pyright 未通过

复核结果：

```text
14 errors, 0 warnings, 0 informations
```

分类：

- 1 个生产代码错误：
  `app/runtime/orchestrator.py:121`
  对可能为 `None` 的 `sticker_library` 调用 `catalog_text()`。
- 12 个测试环境解析错误：
  `tests/*` 无法解析 `pytest`。
- 1 个测试类型错误：
  `tests/test_vision_provider.py:135`
  参数化 `code` 为普通 `str`，不能赋给 `ProviderRoutingError.code`。

报告中的“pyright 未运行”是准确记录；现在已有可复现结果，需要进入修复任务。

### 2.3 配置上限不一致

- `StickerAutoTagConfig.max_tags` 允许 `1..20`。
- `StickerAutoTagConfig.max_description_chars` 允许 `1..500`。
- `StickerManifestEntry.tags` 最多 8 项。
- `StickerManifestEntry.description` 最多 120 字符。
- `_apply_draft()` 使用 `model_copy(update=...)`，不会重新执行 manifest 校验。

后果：

如果配置 `maxTags=20` 或 `maxDescriptionChars=500`，自动标注可能写入超出 manifest
硬上限的数据。当前进程内看似成功，但 QQF 下一次启动时 `load_manifest()` 会拒绝整个清单，
导致表情功能不可用。

严重度：高。必须在真实标注前修复。

### 2.4 其他残留风险

- 手动编辑 manifest 时，说明和标签没有统一拒绝换行、控制字符或 CQ 码。
- `.tag.lock` 没有过期处理；进程异常退出后可能残留并阻止下一次标注。
- 视觉模型是否接受当前 `opencode.ai` 端点、模型名和返回格式仍未经真实成功请求确认。

## 三、任务清单

### T-25B.1 修复 pyright 生产错误

- 目标：消除 orchestrator 对可能为 `None` 的 sticker library 调用。
- 涉及文件：
  - `app/runtime/orchestrator.py`
  - `tests/test_orchestrator.py`
- 实现要点：
  - 在 `_run_locked()` 内保存局部变量：

```python
library = self.sticker_library
sticker_ids = (
    [entry.sticker_id for entry in library.catalog()]
    if library is not None and library.available
    else []
)
sticker_catalog = library.catalog_text() if library is not None and sticker_ids else ""
```

  - 保持行为不变，不引入新的运行时分支。
- 验收：
  - `.venv\Scripts\python.exe -m pyright` 不再出现该生产错误。
  - 无 sticker library、空目录、有效目录三种测试仍通过。
- 依赖：无。
- 风险：低。

### T-25B.2 修复自动标注与 manifest 上限不一致

- 目标：保证自动标注写入的数据一定能够重新加载。
- 涉及文件：
  - `app/config.py`
  - `app/agent/sticker_tagger.py`
  - `tests/test_config.py`
  - `tests/test_sticker_tagger.py`
- 实现要点：
  - 将 `max_tags` 硬上限改为 8，或统一引用 manifest 的硬上限常量。
  - 将 `max_description_chars` 硬上限改为 120，或统一引用 manifest 的硬上限常量。
  - `_apply_draft()` 构造更新后，必须用 `StickerManifestEntry.model_validate()` 再校验，
    不能只依赖 `model_copy()`。
  - 写入前校验失败时，保留旧 manifest 并返回失败。
- 验收：
  - 配置 `maxTags=20` 时启动配置校验失败，或最终写入仍严格遵守 8 项。
  - 配置 `maxDescriptionChars=500` 时不能写出超过 120 字符的 description。
  - 新增“写入后 reload 成功”的回归测试。
- 依赖：T-25B.1。
- 风险：中，必须避免改变已有合法配置的行为。

### T-25B.3 修复 pyright 测试环境和测试类型问题

- 目标：让完整 pyright 门禁可重复执行。
- 涉及文件：
  - `pyproject.toml`
  - 新增或修改 `pyrightconfig.json`，只在需要时使用
  - `tests/test_vision_provider.py`
- 实现要点：
  - 配置 pyright 使用 `.venv`，解决 `pytest` 无法解析。
  - 将参数化 `code` 标注为 `ErrorCode` 或使用合法 `Literal`。
  - 不通过忽略整个 tests 目录来消除错误。
- 验收：

```text
.venv\Scripts\python.exe -m pyright
0 errors, 0 warnings, 0 informations
```

- 依赖：T-25B.1、T-25B.2。
- 风险：低。

### T-25B.4 真实视觉模型 dry-run

- 目标：确认独立视觉 Provider 在真实网络、真实密钥、真实模型下返回合法 JSON。
- 前置：
  - 测试 QQ 不需要在线。
  - `QQF_VISION_API_KEY` 已设置。
  - `config.json` 的 `sticker.autoTag` 指向真实 Provider。
  - 当前唯一素材 `pilot-01.jpg` 可读取。
- 执行：

```text
.venv\Scripts\python.exe scripts\tag_stickers.py --config config.json --id pilot-01 --dry-run
```

- 验收：
  - 退出码为 `0`。
  - 输出显示 `pilot-01: updated`。
  - 模型返回的 name、description、tags、moods、scenes、avoid_scenes、confidence 均为合法字段。
  - 不修改 manifest。
  - 日志不包含 API key、base64、绝对路径或完整模型响应。
- 失败处理：
  - `AUTH`：检查密钥、模型名和端点，不切换 backup。
  - `TIMEOUT`、`RATE_LIMIT`、`PROVIDER_UNAVAILABLE`：允许 fallback。
  - 非法 JSON：保留原始脱敏响应摘要，不写 manifest，另行补解析兼容或提示词。
- 依赖：T-25B.2、T-25B.3。
- 风险：外部网络和模型可用性。

### T-25B.5 准备三张语义差异测试素材

- 目标：具备“无语、开心、疑惑”三类真实选择验证条件。
- 涉及文件：
  - `stickers/images/pilot-01.jpg`
  - 新增 `stickers/images/pilot-02.jpg`
  - 新增 `stickers/images/pilot-03.jpg`
  - `stickers/manifest.yaml`
- 项目方提供素材，工程师只做本地入库。
- 实现要点：
  - 三张图内容差异明显，不能都是同一情绪。
  - 不提交图片和本地 manifest。
  - `allowedIds` 只包含 `pilot-01`、`pilot-02`、`pilot-03`。
  - 先人工确认 `approved=true`，再执行自动标注。
- 验收：
  - 三张图 MediaGuard 全部通过。
  - manifest 中三个 id 唯一，文件路径合法。
- 依赖：项目方提供两张新素材。
- 风险：素材版权和隐私由项目方确认。

### T-25B.6 真实自动标注与重启生效

- 目标：确认自动生成标签立即写入，并在重启后进入语义目录。
- 执行：

```text
.venv\Scripts\python.exe scripts\tag_stickers.py --config config.json --all
```

然后重启 QQF。

- 验收：
  - 三个素材全部 `updated`，或明确记录跳过原因。
  - manifest 中的三份元数据能够重新加载。
  - QQF 启动无 manifest 错误。
  - 系统提示构建时能看到三个 id，但日志不输出原始提示词。
  - 出站仍只允许三个 id。
- 依赖：T-25B.4、T-25B.5。
- 风险：视觉模型可能误判梗图，需人工抽查并修改。

### T-25B.7 真实群语义选择验收

- 目标：验证模型按语境选图、纯表情和无合适图回退。
- 前置：
  - 测试 QQ 在线。
  - NapCat 可用。
  - 测试群为专用小群。
  - 三张素材已进入白名单。
- 测试场景：

1. “无语”语境：
   - 群聊内容明显吐槽、冷场或敷衍。
   - 允许模型选择纯表情或文字加表情。
   - 期望优先选无语类素材。
2. “开心/赞同”语境：
   - 被夸、达成约定或群内积极互动。
   - 期望优先选开心类素材。
3. “疑惑”语境：
   - 出现错误信息、听不懂或前后矛盾。
   - 期望优先选疑惑类素材。
4. 严肃道歉语境：
   - 机器人或群友明确道歉、安慰或严肃讨论。
   - 期望不发表情，直接文字。
5. 同轮双表情：
   - 使用 deterministic provider 或 mock 强制两次 `sticker_send`。
   - 期望第二次失败，同一轮只发送一张。
6. 跨轮重复：
   - 两个相邻轮次强制选择同一 id。
   - 期望两次都能成功。
- 验收：
  - 三张素材至少各被正确选择一次，或明确记录模型未选中的原因。
  - 严肃场景无表情。
  - 同一轮绝不出现两张。
  - 跨轮重复不被限制。
  - 所有发送通过 SafetyPipeline、限流和审计。
- 依赖：T-25B.6。
- 风险：模型输出存在随机性，单次错误不能直接判定系统失败；至少重复两轮。

### T-25B.8 提交与回滚

- 目标：把 T-25 代码和文档形成可回滚提交。
- 提交拆分沿用报告中的 9 个提交，或合并为不超过 4 个逻辑提交：

```text
feat(T-25): add semantic sticker selection
feat(T-25): add independent sticker auto-tagging
test(T-25): cover sticker selection and auto-tagging
docs(T-25): document sticker catalog and vision provider
```

- 禁止提交：
  - `config.json`
  - `stickers/` 本地素材和 manifest
  - `personas/sticker-pilot.yaml`
  - `state/`
  - 日志、Token、API key、真实群号
- 回滚方式：
  - 代码回滚到 `5db43f1`。
  - 恢复 `stickers/manifest.yaml.bak`。
  - 将 `sticker.enabled` 设回 `false` 并重启。
- 验收：
  - `git status` 不包含密钥、本地素材或运行状态。
  - 提交后测试和 ruff 仍通过。
  - 如真实视觉验收未通过，不得把 T-25 报告改写为全部完成。
- 依赖：T-25B.4 至 T-25B.7。
- 风险：提交前可能存在其他历史未跟踪报告，按文件逐个加入，禁止 `git add .`。

## 四、执行顺序

```text
T-25B.1 -> T-25B.2 -> T-25B.3
  -> T-25B.4
  -> T-25B.5 -> T-25B.6 -> T-25B.7
  -> T-25B.8
```

T-25B.5 需要项目方提供两张新图片，可与 T-25B.1 至 T-25B.4 并行准备，但不能替代真实视觉验证。

## 五、统一门禁

```text
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pyright
git diff --check
```

通过标准：

- pytest 全部通过。
- ruff 全部通过。
- pyright `0 errors`。
- 真实视觉 dry-run 通过。
- 三张素材真实自动标注和重启加载通过。
- 真实群语义选择通过，严肃场景不发表情。
- 没有向仓库提交本地素材、配置或密钥。

## 六、待项目方配合

1. 在可联网环境授权一次真实视觉模型调用。
2. 提供两张新的语义差异测试表情。
3. 确认测试群、测试 QQ 和测试时段。
4. 确认 T-25 完成前允许保持代码为未提交状态，或明确指定提交方式。

在项目方明确说“开始执行”前，本交办单不修改源码。

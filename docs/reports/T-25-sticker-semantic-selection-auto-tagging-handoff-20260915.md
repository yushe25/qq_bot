# T-25 表情语义选择与自动识别交办单

- 发布日期：2026-09-15
- 当前基线：`5db43f1`
- 输入依据：
  - `docs/design-contracts/sticker-media.md`
  - `docs/acceptance/T-23-sticker-pilot-20260914.md`
  - `docs/reports/T-23-sticker-pilot-chief-designer-report-20260914.md`
- 任务性质：表情选择契约、模型选择、纯表情、素材自动标注
- GitHub CI / push：继续延后，不在本任务范围

## 一、总设计师决定

已由项目方确认：

1. 自动识别使用独立的额外模型配置，不依赖聊天模型。聊天模型不可用时，自动标注仍可单独工作。
2. 自动生成的名称、说明和标签立即写入素材清单，立即生效；允许事后人工修改。
3. 所有触发场景都允许纯表情；安全门禁、白名单、频率限制仍不可绕过。
4. 模型查看“带说明的素材目录”后自行选图。没有合适的图时必须使用纯文字，不退化成随机兜底图。
5. 每轮最多发送一张表情；不同轮次可以重复使用同一张，不增加重复冷却。

## 二、需求确认结果

### 2.1 已确认

- 从“随机概率追加”改为“模型根据语义目录主动选择”。
- 删除程序随机追加表情的主路径。
- `sticker_send` 可单独发送，也可以和文字同轮发送。
- 每轮只允许一张表情，包含模型显式调用和任何兼容逻辑。
- 允许同一素材跨轮重复出现。
- 素材元数据支持人工维护，也支持独立看图模型自动生成。
- 自动标注失败时保持原数据不变，不能写入半成品。

### 2.2 尚未确认

- 项目方是否提供至少 3 张可长期使用的测试素材。
- 独立看图模型的供应商、接口和模型名。
- 是否需要在控制台提供可视化编辑页。本交办单默认先支持 YAML 人工编辑和命令行自动标注。

## 三、现状与根因

### 3.1 现状证据

- `StickerLibrary` 只暴露 id、名称、路径和 tags，见
  `app/agent/sticker_library.py`。
- 系统提示词没有表情目录；工具 schema 只有
  `sticker_send(stickerId: string)`，没有可选 id 列表和场景说明，见
  `app/agent/tool_schemas.py` 与 `app/agent/prompt_builder.py`。
- `_maybe_attach_sticker()` 按“有无文字”选择概率，再用
  `random.choice()` 追加素材，见 `app/runtime/orchestrator.py`。
- `tags` 和 `usage_count` 没有参与选图。
- 当前聊天模型适配层只构造文本消息，不能提交图片内容，见
  `app/agent/providers/openai_compatible.py`。
- 当前 `sticker_only_probability` / `sticker_with_text_probability`
  是随机追加参数，不适用于模型语义选图。

### 3.2 根因

系统没有“可读的素材语义目录”，也没有“图片语义标注”入口。模型既不知道有哪些 id，
也不知道每张图片的含义，因此只能用程序随机选择。修复必须同时补齐：

1. 统一的素材元数据模型。
2. 模型可见的目录文本和受限 id 列表。
3. 独立的看图模型客户端。
4. 自动标注的原子写入和人工覆盖规则。
5. 删除随机追加主路径。

## 四、目标数据流

```text
新增图片
  -> MediaGuard 校验
  -> 独立看图模型生成元数据草稿
  -> 清洗与长度限制
  -> 原子写入 manifest.yaml
  -> 重启 QQF
  -> StickerLibrary 读取白名单目录
  -> system prompt 注入目录
  -> 模型按语境选择 stickerId
  -> sticker_send 入 outbox
  -> SafetyPipeline 校验并发送
```

模型选择失败、目录为空或没有合适素材时：

```text
模型正常输出文字
  -> 不追加随机表情
  -> 发送纯文字
```

## 五、配置设计

### 5.1 StickerConfig

涉及 `app/config.py`：

```python
class StickerConfig(BaseModel):
    enabled: bool = False
    allowed_ids: list[str] = Field(default_factory=list)
    auto_tag: StickerAutoTagConfig = Field(default_factory=StickerAutoTagConfig)
```

保留现有校验：

```python
if self.enabled and not self.allowed_ids:
    raise ValueError("sticker.enabled=true requires sticker.allowedIds")
```

新增校验：

```python
if self.auto_tag.enabled:
    if not self.auto_tag.provider:
        raise ValueError("sticker.autoTag.enabled=true requires provider")
    if self.auto_tag.provider not in self.auto_tag.providers:
        raise ValueError("sticker.autoTag.provider must exist in providers")
    if len(self.auto_tag.fallbacks) != len(set(self.auto_tag.fallbacks)):
        raise ValueError("sticker.autoTag.fallbacks must not contain duplicates")
```

### 5.2 独立看图模型配置

自动标注配置和 `llm.providers` 完全分离。它不读取聊天模型 key，也不进入聊天模型 fallback：

```python
class StickerAutoTagProviderConfig(BaseModel):
    type: Literal["openai-compatible"] = "openai-compatible"
    base_url: str
    api_key_env: str
    model: str
    timeout_seconds: float = 120.0
    max_retries: int = 1
    extra_headers: dict[str, str] = Field(default_factory=dict)
    max_image_bytes: int = 8 * 1024 * 1024


class StickerAutoTagConfig(BaseModel):
    enabled: bool = False
    provider: str = ""
    fallbacks: list[str] = Field(default_factory=list)
    providers: dict[str, StickerAutoTagProviderConfig] = Field(default_factory=dict)
    max_tags: int = 8
    max_description_chars: int = 120
```

配置示例：

```json
{
  "sticker": {
    "enabled": true,
    "allowedIds": ["pilot-01"],
    "autoTag": {
      "enabled": true,
      "provider": "vision-main",
      "fallbacks": ["vision-backup"],
      "providers": {
        "vision-main": {
          "type": "openai-compatible",
          "baseUrl": "https://example.invalid/v1",
          "apiKeyEnv": "QQF_VISION_API_KEY",
          "model": "vision-model",
          "timeoutSeconds": 120,
          "maxRetries": 1
        },
        "vision-backup": {
          "type": "openai-compatible",
          "baseUrl": "https://backup.invalid/v1",
          "apiKeyEnv": "QQF_VISION_BACKUP_API_KEY",
          "model": "vision-backup-model"
        }
      }
    }
  }
}
```

安全要求：

- 只允许环境变量密钥，不允许明文 `apiKey`。
- 远端地址必须为 HTTPS；仅回环地址允许 HTTP。
- `extraHeaders` 不允许覆盖 `authorization` 和 `content-type`。
- 只对 `RATE_LIMIT`、`TIMEOUT`、`PROVIDER_UNAVAILABLE` 做 fallback。
- 鉴权失败、无效请求、解析失败不得自动切换 provider。

## 六、素材清单设计

### 6.1 manifest.yaml 条目

保留现有字段并扩展：

```yaml
- id: pilot-01
  name: 无语猫
  file: images/pilot-01.jpg
  approved: true
  usage_count: 0
  description: 一只白猫躺着翻白眼，表达无语和敷衍
  tags: [无语, 敷衍, 猫]
  moods: [无语, 无奈]
  scenes: [被吐槽时, 冷场接梗时]
  avoid_scenes: [严肃道歉时, 安慰别人时]
  tag_source: auto
  tagged_at: "2026-09-15T12:00:00+08:00"
  content_hash: "sha256:..."
  tag_confidence: 0.84
```

约束：

- `approved` 仍由项目方控制，自动标注不得修改。
- `file` 仍必须为库内相对路径，禁止绝对路径和 `..`。
- 自动标注不写图片原文 OCR，防止把图片中的指令注入 prompt。
- 说明、标签、场景必须经过控制字符清理、CQ 码过滤、敏感内容过滤和长度限制。
- `tag_source` 只允许 `manual`、`auto`、`legacy`。
- `legacy` 表示旧素材，没有语义元数据。

### 6.2 新增素材清单模块

新增 `app/agent/sticker_manifest.py`：

```python
class StickerManifestEntry(BaseModel):
    id: str
    name: str
    file: str
    approved: bool = False
    usage_count: int = 0
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    moods: list[str] = Field(default_factory=list)
    scenes: list[str] = Field(default_factory=list)
    avoid_scenes: list[str] = Field(default_factory=list)
    tag_source: Literal["manual", "auto", "legacy"] = "legacy"
    tagged_at: str | None = None
    content_hash: str | None = None
    tag_confidence: float | None = None
```

模块接口：

```python
def load_manifest(path: Path) -> list[StickerManifestEntry]: ...
def save_manifest_atomic(path: Path, entries: list[StickerManifestEntry]) -> None: ...
def file_content_hash(path: Path) -> str: ...
```

要求：

- `StickerLibrary` 改用该模块读取 manifest，避免两套解析规则。
- 保存时使用临时文件加原子替换。
- 写入前备份为 `stickers/manifest.yaml.bak`。
- 同一目录只允许一个标注命令运行，建议写入 `stickers/.tag.lock`。
- YAML 注释不保证保留，文档必须明确说明。

## 七、运行时接口设计

### 7.1 StickerLibrary

扩展 `StickerFile`：

```python
@dataclass(frozen=True)
class StickerFile:
    sticker_id: str
    name: str
    path: Path
    tags: tuple[str, ...]
    description: str = ""
    moods: tuple[str, ...] = ()
    scenes: tuple[str, ...] = ()
    avoid_scenes: tuple[str, ...] = ()
```

新增目录接口：

```python
@dataclass(frozen=True)
class StickerCatalogEntry:
    sticker_id: str
    name: str
    description: str
    tags: tuple[str, ...]
    moods: tuple[str, ...]
    scenes: tuple[str, ...]
    avoid_scenes: tuple[str, ...]


def catalog(self) -> tuple[StickerCatalogEntry, ...]: ...
def catalog_text(self, max_chars: int = 4000) -> str: ...
```

目录只包含：

- `enabled=true`
- id 在 `allowedIds`
- `approved=true`
- 文件存在
- MediaGuard 校验通过

目录不得包含：

- 绝对路径或相对文件路径
- API key
- 未审核素材
- 原始 OCR 文本

### 7.2 提示词

涉及 `app/agent/prompt_builder.py`：

```python
def build_system_prompt(
    session: Session,
    persona_text: str,
    learning_notes: str = "",
    *,
    final_step: bool = False,
    wake_category: TriggerCategory | None = None,
    sticker_catalog: str = "",
) -> str: ...
```

目录存在时增加：

```text
可用表情目录：
- pilot-01 | 无语猫 | 无语、敷衍、猫 | 一只白猫翻白眼 | 适合：被吐槽、冷场接梗 | 不适合：严肃道歉

表情选择规则：
- 只能选择上方列出的 stickerId。
- 明显贴合语境时才发，不要每轮都发。
- 可以只发表情，也可以文字加表情。
- 每轮最多一张。
- 没有明显合适的图时直接使用文字。
```

### 7.3 工具 schema

涉及 `app/agent/tool_schemas.py`：

```python
def build_tool_schemas(
    *,
    allow_tools: bool = True,
    sticker_enabled: bool = False,
    sticker_ids: Sequence[str] = (),
) -> list[dict[str, Any]]: ...
```

要求：

- `sticker_enabled=false` 时不暴露 `sticker_send`。
- 提供 id 时，`stickerId` 增加 `enum`。
- 工具描述明确指出只能从目录选择，没有合适素材就不要调用。
- 不将文件路径传给模型。

### 7.4 工具执行

涉及 `app/agent/tools.py`：

- `sticker_send` 继续调用 `library.resolve()`。
- `resolve()` 每次仍执行 approved、路径和 MediaGuard 校验。
- 如果 `ctx.outbox` 已存在 `send_sticker`，返回失败：

```text
本轮已发送表情，每轮最多一张
```

- 不设置跨轮冷却，不阻止下一轮选择同一 id。
- 工具失败不重试，不自动替换为另一张图。

### 7.5 删除随机追加

涉及 `app/runtime/orchestrator.py`：

- 删除 `_maybe_attach_sticker()` 的调用。
- 删除基于 `random.choice()` 的表情追加。
- 调用 `build_tool_schemas()` 时传入 `sticker_ids=library.list_ids()`。
- 调用 `build_system_prompt()` 时传入 `sticker_catalog=library.catalog_text()`。

兼容策略：

- `sticker_only_probability` 和 `sticker_with_text_probability` 暂时保留解析，
  但运行时不再使用。
- README 和设计契约标记为 deprecated，后续版本单独删除。
- 纯表情已由模型通过 `sticker_send` 单独实现，不再依赖概率字段。

## 八、自动标注设计

### 8.1 看图模型客户端

新增 `app/agent/providers/vision_openai_compatible.py`：

```python
class OpenAICompatibleVisionProvider:
    async def describe(self, image_path: Path, *, prompt: str) -> str: ...
```

请求格式：

```json
{
  "model": "vision-model",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "请输出严格 JSON..."},
        {
          "type": "image_url",
          "image_url": {"url": "data:image/jpeg;base64,..."}
        }
      ]
    }
  ],
  "temperature": 0.1
}
```

要求：

- 发送图片前再次经过 MediaGuard。
- 只允许本地素材路径。
- 请求体和 base64 内容不得写日志。
- 错误码归一化沿用现有 provider router 的分类。
- 超时、限流、服务不可用才使用 fallback。

### 8.2 自动标注服务

新增 `app/agent/sticker_tagger.py`：

```python
class StickerTagDraft(BaseModel):
    name: str
    description: str
    tags: list[str]
    moods: list[str]
    scenes: list[str]
    avoid_scenes: list[str]
    confidence: float


class StickerTagger:
    async def tag_one(self, sticker_id: str, *, force: bool = False) -> StickerManifestEntry: ...
    async def tag_many(
        self,
        sticker_ids: Sequence[str],
        *,
        force: bool = False,
    ) -> list[StickerManifestEntry]: ...
```

行为：

- `tag_source=manual` 时跳过，除非显式 `force=true`。
- `content_hash` 未变化时跳过，除非显式 `force=true`。
- 自动生成后立即写 manifest，不等待人工确认。
- 一次命令中任一素材失败时，不写该素材；其他成功项可以写入，但最终必须给出失败清单。
- 写入前清理输出并重新校验长度；失败时保留旧元数据。
- 自动标注完成后提示重启 QQF，运行中的实例不热加载 manifest。

### 8.3 命令行入口

新增 `scripts/tag_stickers.py`：

```text
.venv\Scripts\python.exe scripts\tag_stickers.py --config config.json --id pilot-01
.venv\Scripts\python.exe scripts\tag_stickers.py --config config.json --all
.venv\Scripts\python.exe scripts\tag_stickers.py --config config.json --id pilot-01 --force
.venv\Scripts\python.exe scripts\tag_stickers.py --config config.json --id pilot-01 --dry-run
```

参数：

- `--config`：配置文件，默认 `config.json`
- `--id`：可重复指定素材 id
- `--all`：标注所有 allowedIds 中 `approved=true` 的素材
- `--force`：覆盖人工标注或相同 hash
- `--dry-run`：只调用模型并显示结果，不写 manifest

输出必须是脱敏摘要，不打印 API key、图片 base64、绝对路径或完整请求体。

## 九、工程任务清单

### T-25.1 记录 ADR 和更新契约

- 目标：固化“模型语义选图、独立看图模型、自动标注立即生效、每轮最多一张”的决策。
- 涉及文件：
  - `docs/decisions/0005-sticker-semantic-selection-and-auto-tagging.md`
  - `docs/design-contracts/sticker-media.md`
  - `README.md`
- 实现要点：
  - 记录旧随机追加方案被替换的原因和回滚方式。
  - 标注概率字段 deprecated。
  - 写清自动标注风险、人工修改方式和重启要求。
- 验收：
  - 文档中的触发条件、配置、失败回退、纯表情规则与代码一致。
  - `rg` 不再把概率字段描述为当前选图机制。
- 依赖：无。
- 风险：只改代码不改契约会导致后续再次偏离。

### T-25.2 建立素材 manifest 模型

- 目标：统一 runtime 与自动标注的 manifest 读写和校验。
- 涉及文件：
  - `app/agent/sticker_manifest.py`
  - `app/agent/sticker_library.py`
  - `tests/test_sticker_library.py`
  - 新增 `tests/test_sticker_manifest.py`
- 实现要点：
  - 实现 `StickerManifestEntry`、`load_manifest()`、`save_manifest_atomic()`。
  - 保留旧素材兼容，缺失语义字段时使用空值和 `legacy`。
  - 原子写入、备份、文件锁。
  - 拒绝重复 id、路径逃逸和非法字段类型。
- 验收：
  - 旧 manifest 无改动可加载。
  - 自动写入失败时原文件保持不变。
  - 备注、路径和 approved 校验有拒绝用例。
- 依赖：T-25.1。
- 风险：YAML 注释会被重写，需在文档和命令输出中明确。

### T-25.3 扩展 StickerLibrary 语义目录

- 目标：只向模型暴露可发送素材的语义信息。
- 涉及文件：
  - `app/agent/sticker_library.py`
  - `tests/test_sticker_library.py`
- 实现要点：
  - 扩展 `StickerFile` 和新增 `StickerCatalogEntry`。
  - 实现 `catalog()`、`catalog_text()`。
  - 目录截断优先保留 id、名称、tags、description、scenes。
  - 不输出路径、hash、OCR 或密钥。
- 验收：
  - 白名单外、未 approved、MediaGuard 失败素材不进入目录。
  - 目录文本不超过限制。
  - 无路径泄漏测试通过。
- 依赖：T-25.2。
- 风险：目录过长增加 token 成本，必须限制长度。

### T-25.4 注入目录并限制工具 id

- 目标：让模型看见素材说明，并只能提交合法 id。
- 涉及文件：
  - `app/agent/prompt_builder.py`
  - `app/agent/tool_schemas.py`
  - `tests/test_prompt_style.py`
  - `tests/test_token_budget.py`
- 实现要点：
  - 增加 `sticker_catalog` 参数。
  - 增加 `sticker_ids` 参数并生成 `enum`。
  - 加入“没有合适素材就纯文字”的明确规则。
  - 保留最终一步不暴露工具的行为。
- 验收：
  - 目录为空时不暴露 `sticker_send`。
  - 目录存在时 schema 包含正确 enum。
  - 提示词不包含文件路径。
- 依赖：T-25.3。
- 风险：部分 OpenAI 兼容服务不接受 `enum`，需保留运行时二次校验。

### T-25.5 删除随机追加并实现单轮一张

- 目标：贴图完全由模型语义决定。
- 涉及文件：
  - `app/runtime/orchestrator.py`
  - `app/agent/tools.py`
  - `tests/test_orchestrator.py`
  - `tests/test_tools.py`
- 实现要点：
  - 删除 `_maybe_attach_sticker()` 调用和 `random.choice()`。
  - 传入目录和 id 列表。
  - `ctx.outbox` 已有表情时，第二次 `sticker_send` 返回失败。
  - 不限制跨轮重复。
- 验收：
  - 普通文字回复不再随机附表情。
  - 模型可纯表情，也可文字加表情。
  - 同一轮第二次表情调用被拒绝。
  - 不同轮次重复同一 id 可通过。
  - 没有目录或没有合适图时只发文字。
- 依赖：T-25.4。
- 风险：模型可能少发或不发；先观察真实数据，不用随机逻辑强行补偿。

### T-25.6 独立看图 provider

- 目标：建立不依赖聊天模型配置的图片识别通道。
- 涉及文件：
  - `app/agent/providers/vision_openai_compatible.py`
  - `app/config.py`
  - 新增 `tests/test_vision_provider.py`
  - `tests/test_config.py`
- 实现要点：
  - 实现 `StickerAutoTagProviderConfig` 和 `StickerAutoTagConfig`。
  - 发送 OpenAI 兼容的 `image_url` data URL。
  - 实现超时、重试、错误归一化和受限 fallback。
  - 禁止请求体、key、base64 写日志。
- 验收：
  - 使用 mock transport 校验请求包含图片。
  - 鉴权失败不 fallback，限流和超时可 fallback。
  - 明文 key 配置被拒绝。
  - 响应解析失败返回明确错误。
- 依赖：T-25.1。
- 风险：第三方视觉模型成本和隐私边界需项目方接受。

### T-25.7 自动标注服务

- 目标：自动生成名称、说明、情绪、适合场景和避免场景。
- 涉及文件：
  - `app/agent/sticker_tagger.py`
  - `app/agent/sticker_manifest.py`
  - 新增 `tests/test_sticker_tagger.py`
- 实现要点：
  - 定义 `StickerTagDraft`。
  - 要求模型输出严格 JSON；代码解析不依赖自由文本。
  - 清理控制字符、CQ 码、敏感信息，并限制数量与长度。
  - 自动生成后立即写入，使用 `tag_source=auto` 和文件 hash。
  - 人工修改时将 `tag_source` 设为 `manual`。
- 验收：
  - 合法 JSON 正确写入。
  - 非法 JSON、缺字段、超长字段均拒绝且不覆盖旧数据。
  - hash 不变时跳过。
  - `manual` 和 `--force` 行为正确。
- 依赖：T-25.6。
- 风险：模型对中文梗图语义理解可能错误，立即生效意味着素材质量必须靠人工事后纠正。

### T-25.8 自动标注命令

- 目标：提供本地、可重复执行的素材标注入口。
- 涉及文件：
  - `scripts/tag_stickers.py`
  - `tests/test_tag_stickers_cli.py`
  - `README.md`
- 实现要点：
  - 实现 `--id`、`--all`、`--force`、`--dry-run`。
  - 支持多个 id。
  - 单张失败不伪装成功，最终返回非零并列出失败 id。
  - 命令结束提示重启 QQF。
- 验收：
  - dry-run 不写文件。
  - 正常执行读取本地图片并写 manifest。
  - 输出不包含 key、base64、绝对路径。
- 依赖：T-25.7。
- 风险：并发执行可能覆盖，必须依赖 manifest 文件锁。

### T-25.9 更新配置示例与运行文档

- 目标：项目方可以按文档配置独立视觉模型并添加素材。
- 涉及文件：
  - `config.example.json`
  - `README.md`
  - `docs/design-contracts/sticker-media.md`
- 实现要点：
  - 增加 `sticker.autoTag` 示例。
  - 说明视觉模型密钥只放环境变量。
  - 说明人工编辑 manifest 后需要重启。
  - 说明没有合适图时模型应发纯文字。
  - 删除“概率追加是主选图方式”的表述。
- 验收：
  - 文档示例可被配置解析。
  - 不包含真实模型、真实群号、key 或个人素材。
- 依赖：T-25.4、T-25.8。
- 风险：文档和默认配置不一致会造成误配。

## 十、测试与验收

### 10.1 自动测试

至少覆盖：

1. 无目录时工具不可用。
2. 有目录时模型只能选 enum 内 id。
3. 模型可单独调用 `sticker_send`。
4. 模型可同轮发送文字和一张表情。
5. 同轮第二次表情调用失败。
6. 不同轮次重复同一 id 成功。
7. 普通文字回复不再被程序随机追加表情。
8. 没有合适图时纯文字发送。
9. 自动标注合法写入、失败不覆盖、manual 跳过、force 覆盖。
10. 视觉 provider 请求含图片且日志不含 base64。
11. 白名单、approved、MediaGuard 和 SafetyPipeline 拒绝路径保持有效。

必须运行：

```text
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pyright
```

### 10.2 真实联调

准备至少 3 张语义差异明显的本地测试图：

- 无语/敷衍
- 开心/赞同
- 疑惑/不解

流程：

1. 使用独立视觉模型执行 `scripts/tag_stickers.py --all`。
2. 人工检查 manifest 中名称、说明、tags、scenes、avoid_scenes。
3. 重启 QQF。
4. 在测试群分别制造三类语境，确认模型选择符合语义。
5. 制造“严肃道歉”语境，确认没有不合适的图被发出。
6. 连续两轮相同语境，确认允许重复但不出现同轮两张。
7. 模型说没有合适图时，确认退化为纯文字。

### 10.3 通过标准

- 随机追加逻辑不再存在。
- 模型可以看见完整语义目录并正确提交 id。
- 自动标注可在独立模型配置下完成。
- 自动标注结果立即写入并在重启后生效。
- 每轮最多一张，跨轮重复不被错误阻止。
- 所有出站动作仍经过 SafetyPipeline、限流、审计和 MediaGuard。
- 没有合适图时不会强行发表情。

## 十一、路线图与风险

### 11.1 建议顺序

1. 先完成 T-25.1 至 T-25.5，关闭随机选图并打通语义选择。
2. 用人工填写的 3 张测试素材完成 mock 和真实选择验收。
3. 再完成 T-25.6 至 T-25.8，接入独立视觉模型。
4. 最后更新文档并进行真实自动标注验收。

理由：先确认模型选择链路正确，再引入自动标注，可以把“选得不对”和“标签生成不对”
两个问题分开定位。

### 11.2 主要风险

- 视觉模型可能不认识中文网络梗，错误标签会立即生效。人工修改是必须保留的能力。
- 本地素材会发送给独立视觉服务，存在隐私、版权和费用风险。
- 图片里的文字可能包含 prompt injection，自动标注禁止把原文 OCR 直接写入 prompt。
- 模型可能少发或过度发表情。项目方已选择不由程序随机补偿，先观察真实数据。
- 不限制跨轮重复可能导致短时间重复。现有频率限制仍生效，若群成员反馈打扰，
  后续再单独决定是否增加冷却。

## 十二、待确认假设

- 自动标注命令按“一次处理指定 id 或全部 allowedIds”实现，不新增控制台编辑页。
- 人工修改仍直接编辑 `stickers/manifest.yaml`。
- manifest 修改后需要重启 QQF，本任务不做热加载。
- 自动标注不修改 `approved`，新增素材仍需项目方显式审核。
- 独立视觉模型的默认超时为 120 秒，可配置。

## 十三、需要项目方配合

真实联调前需要：

1. 提供至少 3 张可长期保存在本机的测试表情包。
2. 提供独立视觉模型的接口地址、环境变量名和模型名。
3. 确认允许将本地测试素材发送给该视觉服务。
4. 确认人工修改 manifest 和重启 QQF 的工作流可接受。

在项目方明确说“开始执行”前，本交办单只作为施工依据，不修改任何源码。

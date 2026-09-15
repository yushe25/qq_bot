# T-28 群表情自动入库与语境学习交办单

- 发布日期：2026-09-15
- 当前基线：`d0c8daa`
- 当前状态：T-04B 已完成，T-09、T-27、远端 CI 仍有独立阻塞
- 任务性质：入站媒体抓取、素材审核、自动标注、语境观察、偏好学习
- 需求状态：项目方已确认，可以进入施工；尚未执行代码修改
- 默认执行原则：本单只完成设计确认；项目方明确说“开始执行”后才修改代码

## 一、项目方已确认

1. 只保存白名单群里的图片和表情图片。
2. 高置信度素材自动进入可发送库，低置信度进入审核。
3. 学习群友发表情时的前后语境，并结合项目方纠正更新偏好。
4. 图片只有在对应群单独授权后才允许发送给外部看图服务。
5. 每个群最多 200 张或 2 GB，按图片指纹去重。
6. 自动抓取素材默认只在来源群使用，不跨群共享。
7. 达到配额后停止导入新素材，不自动删除已有素材。
8. 自动放行置信度阈值先固定为 `0.85`，允许后续按群调整。
9. 低置信度素材通过控制台审核页面处理。

## 二、目标

1. 群友在白名单群发送图片或表情图片时，QQF 自动抓取并保存到本机隔离区。
2. 使用 SHA-256 去重，同一图片只保存一份文件，但保留不同群的来源和使用记录。
3. 自动调用已配置的独立视觉 Provider 生成名称、说明、标签、情绪、适合场景和避免场景。
4. 高置信度素材自动进入该群的可用素材目录；低置信度素材等待项目方审核。
5. 记录表情出现时的前后聊天语境，形成可解释的群内使用偏好。
6. 模型选图时只看到当前群可用且经过批准的素材语义目录。
7. 达到配额后停止继续自动入库并写审计，不静默删除人工或已批准素材。

## 三、非目标

- 不保存私聊图片。
- 不保存语音、视频、普通文件、转发卡片和 QQ 系统数字表情。
- 不做远程图片抓取服务，不建立通用爬虫。
- 不做跨群共享和跨群推荐；默认只允许素材在来源群使用。
- 不做模型权重微调，只做本地标签、语境关联和偏好评分。
- 不自动删除人工维护、已批准或已固定素材。
- 不允许未批准素材绕过 `StickerLibrary` 和白名单。

## 四、当前可复用能力

### 4.1 入站事件

- `NormalizedEvent.segments` 已保留图片 segment 的原始 `data`，见
  `app/platform/qq/event_normalizer.py`。
- 当前只把图片归一化为 `[图片]`，没有下载和持久化。

### 4.2 素材库

- `StickerLibrary` 已支持 manifest、白名单、approved、MediaGuard 和语义目录。
- `StickerManifestEntry` 已支持 description、tags、moods、scenes、avoid_scenes、
  tag_source、content_hash 和 tag_confidence。
- `StickerTagger` 已支持独立视觉 Provider、严格 JSON、敏感过滤和原子写入。

### 4.3 审核与运行

- 控制台已有 Bearer 鉴权和本机访问约束。
- 数据库已有版本化 migration、备份恢复和 WAL 管理。
- `ConsoleService` 已从 Scheduler 拆出，可增加素材审核操作。

### 4.4 当前缺口

- 没有 OneBot 图片下载接口。
- 没有入站素材隔离区、图片 hash 和来源群关联。
- 没有按群限制的语义目录。
- 没有语境观察、反馈评分和审核状态机。
- 没有群级 200 张或 2 GB 配额。

## 五、配置设计

### 5.1 StickerIngestConfig

建议在 `sticker` 下新增：

```python
class StickerIngestGroupConfig(BaseModel):
    enabled: bool = True
    vision_enabled: bool = False
    auto_approve_confidence: float = 0.85
    max_items: int = 200
    max_bytes: int = 2 * 1024 * 1024 * 1024


class StickerIngestConfig(BaseModel):
    enabled: bool = False
    same_group_only: bool = True
    observation_before_messages: int = 8
    observation_after_messages: int = 8
    groups: dict[str, StickerIngestGroupConfig] = Field(default_factory=dict)
```

配置示例：

```json
{
  "sticker": {
    "ingest": {
      "enabled": true,
      "sameGroupOnly": true,
      "observationBeforeMessages": 8,
      "observationAfterMessages": 8,
      "groups": {
        "100000": {
          "enabled": true,
          "visionEnabled": true,
          "autoApproveConfidence": 0.85,
          "maxItems": 200,
          "maxBytes": 2147483648
        }
      }
    }
  }
}
```

校验规则：

- ingest 的群必须同时出现在 `safety.allowGroups`。
- `visionEnabled=true` 时，`sticker.autoTag.enabled` 必须为 true。
- `autoApproveConfidence` 范围为 0 到 1，默认 0.85。
- `maxItems` 范围 1 到 200。
- `maxBytes` 不超过 2 GB。
- 默认 `enabled=false`。
- 默认 `visionEnabled=false`，即不向外部看图服务发送图片。
- 默认 `sameGroupOnly=true`。

## 六、数据存储

### 6.1 文件目录

```text
stickers/
  manifest.yaml
  images/
  inbox/
    <sha256>.jpg
  rejected/
    <sha256>.jpg
  .tag.lock
  .ingest.lock
```

规则：

- `inbox` 只保存待审核或低置信度素材。
- 自动批准或人工批准后，文件移入 `images/`，并写 manifest。
- 拒绝文件移入 `rejected/`，默认保留 7 天后由维护命令清理。
- 文件名固定使用 SHA-256，不使用发送者昵称或消息文本。
- 素材目录仍不提交到仓库。

### 6.2 新增 SQLite 表

通过 `app/storage/migrations.py` 新增 migration：

```sql
CREATE TABLE inbound_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    width INTEGER,
    height INTEGER,
    local_path TEXT NOT NULL,
    status TEXT NOT NULL,
    vision_provider TEXT,
    tag_confidence REAL,
    auto_approved INTEGER NOT NULL DEFAULT 0,
    first_seen_at REAL NOT NULL,
    reviewed_at REAL,
    reviewed_by TEXT,
    reject_reason TEXT
);

CREATE TABLE inbound_media_groups (
    media_id INTEGER NOT NULL,
    group_id TEXT NOT NULL,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (media_id, group_id)
);

CREATE TABLE sticker_context_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL,
    group_id TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    observed_at REAL NOT NULL,
    before_context_json TEXT NOT NULL,
    after_context_json TEXT NOT NULL DEFAULT '[]',
    situation_tags_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL,
    UNIQUE (source_message_id, media_id)
);

CREATE TABLE sticker_preferences (
    media_id INTEGER NOT NULL,
    group_id TEXT NOT NULL,
    situation_tag TEXT NOT NULL,
    positive_score REAL NOT NULL DEFAULT 0,
    negative_score REAL NOT NULL DEFAULT 0,
    usage_count INTEGER NOT NULL DEFAULT 0,
    last_updated_at REAL NOT NULL,
    PRIMARY KEY (media_id, group_id, situation_tag)
);
```

索引：

```sql
CREATE INDEX idx_inbound_media_status ON inbound_media(status, first_seen_at);
CREATE INDEX idx_media_groups_group ON inbound_media_groups(group_id, last_seen_at);
CREATE INDEX idx_context_group_time ON sticker_context_observations(group_id, observed_at);
CREATE INDEX idx_preferences_group_score ON sticker_preferences(group_id, positive_score);
```

状态枚举：

```text
quarantine
pending_review
approved
rejected
```

## 七、入站图片抓取

### 7.1 支持范围

只处理：

- `image`
- 明确属于图片表情的 NapCat image-like segment，例如兼容实现返回的 `mface`

不处理：

- `face`
- `record`
- `file`
- `forward`
- 私聊图片

### 7.2 Gateway 接口

在 `QQGateway` 增加：

```python
async def fetch_image_segment(
    self,
    segment: MessageSegment,
    *,
    max_bytes: int,
) -> bytes:
    ...
```

OneBot 实现顺序：

1. 优先使用 NapCat 的 `get_image` 动作，发送 `file` 或 `file_id`。
2. 只有 NapCat 不支持时才尝试 segment 中的 `url`。
3. URL 只允许 HTTP/HTTPS。
4. 禁止跳转到私网地址。
5. 超时 10 秒，最多 2 次重定向。
6. 超过 `max_bytes` 立即中止。
7. 不写 URL、Token、base64 到日志。

### 7.3 MediaGuard

下载完成后必须执行：

- 最大 8 MB。
- 最大 40960000 像素。
- 只允许 JPEG、PNG、GIF、WebP。
- 动画 GIF 只保存原文件，不展开帧。
- 通过 Pillow 校验真实格式，不能只信扩展名。
- 失败时写审计并将状态标记为 `rejected`，不进入审核。

### 7.4 去重

- 使用图片原始字节的 SHA-256。
- 同一 hash 已存在时不再保存新文件。
- 增加或更新 `inbound_media_groups` 的 `seen_count` 和 `last_seen_at`。
- 不同群使用同一图片时只共享文件，不共享批准状态和使用偏好。

## 八、自动标注与自动放行

### 8.1 外部视觉授权

只有：

- 群在 `safety.allowGroups`
- 群在 `sticker.ingest.groups`
- 该群 `enabled=true`
- 该群 `vision_enabled=true`

四个条件同时满足，图片才允许发送给外部视觉 Provider。

未授权群的行为：

- 图片仍可保存到隔离区。
- 不调用外部服务。
- 素材进入 `pending_review`。
- 元数据由项目方人工填写。

### 8.2 标注复用

复用 `StickerTagger` 的严格 JSON 输出：

```json
{
  "name": "无语猫",
  "description": "白猫翻白眼，表达无语和敷衍",
  "tags": ["无语", "敷衍", "猫"],
  "moods": ["无语", "无奈"],
  "scenes": ["被吐槽时", "冷场接梗时"],
  "avoid_scenes": ["严肃道歉时", "安慰别人时"],
  "confidence": 0.88
}
```

### 8.3 自动放行规则

只有全部满足才自动批准：

- `confidence >= auto_approve_confidence`
- ContentFilter 全部通过
- MediaGuard 通过
- 图片源群与目标群一致
- 群配额未超
- 没有 OCR 指令、敏感内容或违规风险
- 未命中拒绝列表

否则状态为 `pending_review`。

自动批准后：

- 写入 manifest。
- `approved=true`。
- `tag_source=auto`。
- `source` 标记为 `group_capture`。
- `scopeGroups=[来源群]`。

## 九、按群素材目录

扩展 manifest：

```yaml
- id: auto-6b159721
  name: 无语猫
  file: images/6b159721.jpg
  approved: true
  source: group_capture
  source_group_ids: ["100000"]
  scope_groups: ["100000"]
  content_hash: "sha256:..."
  description: 白猫翻白眼，表达无语和敷衍
  tags: [无语, 敷衍, 猫]
  moods: [无语, 无奈]
  scenes: [被吐槽时, 冷场接梗时]
  avoid_scenes: [严肃道歉时, 安慰别人时]
  tag_source: auto
  tag_confidence: 0.88
```

`StickerLibrary` 新增：

```python
def catalog_for_scene(
    self,
    scene_id: str,
    *,
    max_chars: int = 4000,
) -> tuple[StickerCatalogEntry, ...]:
    ...
```

规则：

- 旧素材没有 `scope_groups` 时按现有 `allowedIds` 行为处理。
- 自动抓取素材必须有 `scope_groups`。
- `same_group_only=true` 时，目标群不在 scope 中则不可见、不可发送。
- 发送时 `resolve_for_scene()` 继续执行 approved、路径、MediaGuard 二次校验。

## 十、语境观察与学习

### 10.1 语境窗口

每个群维护最近消息环形缓冲：

- 表情消息前 8 条。
- 表情消息后 8 条，到达后异步补齐。
- 每条只保留 sender_id、显示名、纯文本摘要和时间。
- 不保存图片原文、语音、文件或私聊内容。

### 10.2 情境标签

第一阶段使用受控标签集，避免引入向量数据库：

```text
吐槽
无语
冷场
接梗
开心
庆祝
赞同
鼓励
疑惑
生气
安慰
认真
道歉
```

生成方式：

- 规则关键词作为基础。
- 群已授权视觉模型时，可以使用低温度模型辅助分类。
- 分类失败时保留原始短上下文，不写偏好分。

### 10.3 学习信号

正向信号：

- 群友在相同情境中使用了该素材。
- 项目方批准或修正后确认适用场景。
- 模型在相同情境中选择后，随后出现正向互动。

负向信号：

- 项目方标记“不适合该场景”。
- 项目方拒绝素材。
- 明确误发后人工纠正。

不作为信号：

- 群里沉默。
- 单次没有回应。
- 模型自己的使用次数。

### 10.4 评分

```python
score = (
    1.0 * member_usage
    + 1.5 * owner_positive
    + 0.5 * follow_up_positive
    - 2.0 * owner_negative
    - 1.0 * explicit_misuse
)
```

运行时不直接把原始分数塞进 prompt。改为生成可读提示：

```text
群内经验：吐槽 +3；冷场 +1；不适合安慰
```

### 10.5 更新时机

- 入站素材观察：立即写入观察记录。
- 后文窗口补齐：写入 `after_context_json` 后更新情境标签。
- 项目方审核：立即更新偏好分。
- 发送结果：只记录使用次数，不自动加分。

## 十一、审核入口

### 11.1 HTTP API

新增：

```text
GET  /api/stickers/review?groupId=<id>
GET  /api/stickers/{media_id}/preview
POST /api/stickers/{media_id}/approve
POST /api/stickers/{media_id}/reject
PATCH /api/stickers/{media_id}/metadata
GET  /api/stickers/{media_id}/context
```

要求：

- 全部沿用控制台 Bearer 鉴权。
- 预览图片只能读取数据库中的本地路径，防止路径穿越。
- API 不返回绝对路径、base64、Token 或真实 QQ 号。
- 审核动作写审计。

### 11.2 控制台

增加“素材审核”区：

- 待审核列表。
- 图片预览。
- 来源群和捕获时间。
- 自动标签、置信度。
- 前后语境样本。
- 批准、拒绝、修改标签。
- 配额使用量。

## 十二、配额与增长

每群独立：

- 最多 200 个不同 hash 素材。
- 最多 2 GB 关联文件总量。

规则：

- 达到任一上限后停止自动导入新素材。
- 已有关联素材不删除。
- 写 `sticker_ingest_quota` 审计并推送控制台事件。
- 项目方清理或提高配额后恢复导入。

全局文件去重：

- 文件按 SHA-256 只存一份。
- 群配额按 `inbound_media_groups` 关联统计。
- 删除某群关联不影响其他群。

## 十三、工程任务清单

### T-28.1 配置和数据库 migration

- 目标：建立 ingest 配置、表结构和状态机。
- 涉及文件：
  - `app/config.py`
  - `config.example.json`
  - `app/storage/migrations.py`
  - `tests/test_config.py`
  - `tests/test_storage_migrations.py`
- 验收：
  - 默认关闭。
  - 非白名单群配置被拒绝。
  - 未开启 autoTag 时 visionEnabled 被拒绝。
  - migration 可在旧库上执行并支持恢复。
- 依赖：无。
- 风险：配置涉及群号，只放本地 `config.json`，不得提交。

### T-28.2 图片抓取与隔离存储

- 目标：从 image segment 获取字节并存隔离区。
- 涉及文件：
  - `app/platform/gateway.py`
  - `app/platform/qq/onebot_client.py`
  - 新增 `app/media/inbound_ingest.py`
  - `tests/test_onebot_client.py`
  - 新增 `tests/test_inbound_ingest.py`
- 验收：
  - `get_image` 优先。
  - URL 回退受限、无私网 SSRF。
  - 大小、格式、像素超限拒绝。
  - hash 去重。
  - 私聊和非白名单群不抓取。
- 依赖：T-28.1。
- 风险：NapCat `get_image` 支持情况需真实协议确认。

### T-28.3 自动标注和自动放行

- 目标：高置信度素材进入可发送库，低置信度进入审核。
- 涉及文件：
  - `app/agent/sticker_tagger.py`
  - 新增 `app/media/sticker_promotion.py`
  - `app/agent/sticker_manifest.py`
  - `tests/test_sticker_tagger.py`
  - 新增 `tests/test_sticker_promotion.py`
- 验收：
  - 置信度阈值边界正确。
  - 敏感、超限、重复、违规素材不放行。
  - 自动批准原子写入 manifest。
  - 低置信度不进入目录。
- 依赖：T-28.2。
- 风险：自动放行误判只能靠人工事后纠正。

### T-28.4 按群素材隔离

- 目标：来源群素材默认只在来源群可用。
- 涉及文件：
  - `app/agent/sticker_manifest.py`
  - `app/agent/sticker_library.py`
  - `app/agent/tools.py`
  - `app/runtime/orchestrator.py`
  - `tests/test_sticker_library.py`
  - `tests/test_tools.py`
  - `tests/test_orchestrator.py`
- 验收：
  - 自动抓取素材在非来源群不可见。
  - 旧手工素材保持兼容。
  - 群级目录和工具 enum 一致。
  - 安全管线仍二次校验。
- 依赖：T-28.3。
- 风险：如果以后需要跨群共享，必须另做明确授权。

### T-28.5 语境观察器

- 目标：记录表情前后上下文并生成受控情境标签。
- 涉及文件：
  - 新增 `app/runtime/sticker_context.py`
  - `app/runtime/scheduler.py`
  - `app/storage/db.py`
  - 新增 `tests/test_sticker_context.py`
- 验收：
  - 前 8 条和后 8 条窗口正确。
  - 只记录摘要，不记录私聊或原文媒体。
  - 重启后观察数据存在。
  - 情境分类失败不影响正常聊天。
- 依赖：T-28.1、T-28.2。
- 风险：关键词分类有误差，第一阶段只做弱信号。

### T-28.6 偏好学习和目录提示

- 目标：用群友使用和项目方纠正更新群内场景偏好。
- 涉及文件：
  - `app/memory/sticker_preferences.py`
  - `app/agent/sticker_library.py`
  - `app/runtime/scheduler.py`
  - `tests/test_sticker_preferences.py`
- 验收：
  - 正向、负向和纠正可更新分数。
  - 沉默不扣分。
  - 目录只出现可读经验，不输出原始场景全文。
  - 模型仍可为不同语境选择其他素材。
- 依赖：T-28.5。
- 风险：早期样本少时容易过拟合，必须设置最小样本数。

### T-28.7 审核 API 和控制台

- 目标：让项目方可以低成本处理低置信度素材和错误标签。
- 涉及文件：
  - `app/console/service.py`
  - `app/console/router.py`
  - `app/console/public/console.html`
  - `tests/test_console_stickers.py`
- 验收：
  - 待审核列表和图片预览可用。
  - 批准、拒绝、修改标签可用。
  - 路径穿越、未授权访问、非白名单群全部拒绝。
  - 所有动作写审计。
- 依赖：T-28.3。
- 风险：预览接口必须严格鉴权。

### T-28.8 配额和维护命令

- 目标：控制每群 200 张或 2 GB，并提供清理工具。
- 涉及文件：
  - `app/media/inbound_ingest.py`
  - 新增 `scripts/sticker_maintenance.py`
  - `tests/test_sticker_quota.py`
- 命令：

```text
.venv\Scripts\python.exe scripts\sticker_maintenance.py --config config.json --stats
.venv\Scripts\python.exe scripts\sticker_maintenance.py --config config.json --clean-rejected
```

- 验收：
  - 配额边界准确。
  - 达到配额后停止导入并写审计。
  - 不自动删除 approved 或 manual 素材。
  - 清理命令必须显式执行。
- 依赖：T-28.2。
- 风险：文件共享时删除文件必须检查所有群关联。

### T-28.9 真实协议和语境验收

- 目标：在真实测试群完成自动保存、标注、自动放行、审核和语境选择。
- 前置：
  - 测试 QQ 在线。
  - 测试群已单独授权视觉服务。
  - 测试素材不包含真实隐私。
- 验收：
  - 普通图片和表情图片可抓取。
  - 重复图片只存一份。
  - 高置信度自动入库。
  - 低置信度进入审核。
  - 来源群隔离有效。
  - 140 张以内和 1.4 GB 以内正常；边界测试 200 张 / 2 GB。
  - 群友使用和人工纠正能改变后续选择倾向。
  - 无跨群泄漏、路径穿越、远程恶意抓取和误发。
- 依赖：T-28.4、T-28.6、T-28.7、T-28.8。
- 风险：需要较长时间积累语境样本。

## 十四、执行顺序

```text
T-28.1
  -> T-28.2 -> T-28.3 -> T-28.4
  -> T-28.5 -> T-28.6
  -> T-28.7
  -> T-28.8
  -> T-28.9
```

建议提交拆分：

```text
feat(T-28): add per-group sticker ingest configuration
feat(T-28): download and quarantine inbound sticker images
feat(T-28): auto-tag and promote high-confidence stickers
feat(T-28): isolate captured stickers by source group
feat(T-28): observe sticker context and learn group preferences
feat(T-28): add sticker review console
feat(T-28): enforce per-group sticker quotas
test(T-28): verify real sticker ingestion and context learning
```

## 十五、统一验收

```text
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pyright
.venv\Scripts\python.exe scripts\check_docs_consistency.py
git diff --check
```

完成标准：

- 默认关闭，未授权群不下载、不外发。
- 私聊、非白名单群、非图片 segment 不保存。
- 高置信度自动放行，低置信度审核。
- 来源群隔离和 200 张 / 2 GB 配额有效。
- 语境学习可以解释、纠正和回滚。
- 所有素材仍经过 StickerLibrary、MediaGuard、SafetyPipeline 和审计。
- 不提交真实素材、群号、配置、数据库、日志或密钥。

## 十六、已确认补充规则

1. 自动抓取素材只在来源群使用，不跨群共享。
2. 达到配额时停止导入，不自动删除已有素材。
3. 自动放行阈值先为 `0.85`，后续可按群调整。
4. 第一阶段使用受控中文情境标签，不引入向量数据库。
5. 低置信度审核使用控制台页面，CLI 只负责统计和清理。
6. T-28 不得混入正在进行的 T-27 长跑基线；开发需另开分支或 worktree。

在项目方明确说“开始执行”前，本单不修改源码。

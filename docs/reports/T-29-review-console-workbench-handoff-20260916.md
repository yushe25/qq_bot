# T-29 Web 审核工作台交办单

- 日期：2026-09-16
- 基线：`bfe45ec`
- 状态：需求已确认，等待执行
- 原则：明确说“开始执行”后再修改代码

## 一、已确认需求

1. 汇总所有白名单群的待审核素材，每条显示来源群。
2. 默认显示 10 条，滚动到底加载下 10 条。
3. 审批后保持已展开数量，例如展开 30 条后仍补足 30 条。
4. 审核卡显示自动标签。
5. 名称、说明、标签、情绪、适合场景、避免场景均可修改。
6. 唤醒日志和审计日志合并到审核区下方。
7. 日志默认 20 条，滚动加载下 20 条。
8. 日志支持“全部、唤醒日志、审计日志”自由切换。

## 二、必须同时修复

- 当前审核必须手工输入群 ID，改为跨群自动汇总。
- 当前 `<img src>` 无法携带 Token，改为带 Bearer header 的 fetch。
- 同一素材来自多个群时，审核状态目前不能按群隔离，必须修正。

## 三、数据结构

在 `inbound_media_groups` 增加群级审核字段：

```sql
ALTER TABLE inbound_media_groups ADD COLUMN review_status TEXT NOT NULL DEFAULT 'pending_review';
ALTER TABLE inbound_media_groups ADD COLUMN reviewed_at REAL;
ALTER TABLE inbound_media_groups ADD COLUMN reviewed_by TEXT;
ALTER TABLE inbound_media_groups ADD COLUMN reject_reason TEXT;

CREATE INDEX idx_media_groups_review
    ON inbound_media_groups(review_status, group_id, media_id);
```

状态：

```text
pending_review
approved
rejected
```

规则：

- 审核条目唯一键为 `(media_id, group_id)`。
- A 群批准不影响 B 群。
- 图片文件仍按 SHA-256 只存一份。
- 元数据按 media 全局保存，批准和拒绝状态按群保存。
- T-28 旧数据从 `inbound_media.status` 回填群级状态。
- migration 必须经过现有自动备份流程。

## 四、审核列表 API

```http
GET /api/stickers/review?limit=10&cursor=<opaque>
```

- `groupId` 可选；不传时汇总全部白名单群。
- `limit` 默认 10，范围 10 到 50。
- 按最新抓取时间倒序，再按 media id 倒序。
- 只返回群级 `pending_review`。

返回：

```json
{
  "items": [
    {
      "mediaId": 41,
      "groupId": "100000",
      "previewUrl": "/api/stickers/41/preview",
      "mimeType": "image/jpeg",
      "byteSize": 183201,
      "seenCount": 3,
      "firstSeenAt": 1789540000000,
      "tagConfidence": 0.88,
      "name": "无语猫",
      "description": "白猫翻白眼，表达无语和敷衍",
      "tags": ["无语", "敷衍", "猫"],
      "moods": ["无语", "无奈"],
      "scenes": ["被吐槽时"],
      "avoidScenes": ["严肃道歉时"]
    }
  ],
  "nextCursor": "opaque-or-null",
  "totalPending": 37
}
```

游标：

- 不向客户端暴露内部格式。
- 编码 `first_seen_at + media_id + group_id`。
- 无法解析时返回 `400 invalid cursor`。

## 五、审核接口

### 修改元数据

```http
PATCH /api/stickers/{media_id}/metadata
```

字段：

```json
{
  "name": "无语猫",
  "description": "白猫翻白眼",
  "tags": ["无语", "猫"],
  "moods": ["无奈"],
  "scenes": ["冷场接梗时"],
  "avoidScenes": ["严肃道歉时"]
}
```

限制：

- 名称 80 字符。
- 说明 120 字符。
- 每个数组最多 8 项。
- 每项最多 40 字符。
- 拒绝 CQ 码、敏感信息、重复项和超限项。

### 批准

```http
POST /api/stickers/{media_id}/approve
```

请求：

```json
{
  "groupId": "100000",
  "metadata": {
    "name": "无语猫",
    "description": "白猫翻白眼",
    "tags": ["无语", "猫"],
    "moods": ["无奈"],
    "scenes": ["冷场接梗时"],
    "avoidScenes": ["严肃道歉时"]
  }
}
```

行为：

- 先校验并保存 metadata。
- 移动文件到 `images/`。
- 更新 manifest。
- 只将当前 `(media_id, group_id)` 设为 `approved`。
- 写审计。

### 拒绝

```http
POST /api/stickers/{media_id}/reject
```

```json
{
  "groupId": "100000",
  "reason": "内容不适合"
}
```

- 只拒绝当前群关联。
- 其他群仍在使用时不移动共享文件。
- 所有群关联都拒绝后才移动文件到 `rejected/`。

## 六、图片预览

前端不能直接使用裸 `<img src>`。

```javascript
const response = await fetch(`/api/stickers/${mediaId}/preview`, {
  headers: authorizationHeader(),
});
const blob = await response.blob();
const objectUrl = URL.createObjectURL(blob);
```

要求：

- 节点销毁时调用 `URL.revokeObjectURL()`。
- 加载失败显示占位状态。
- 长期 Token 不进入图片 URL。
- 不记录 base64。

## 七、审核 Web UI

### 列表

页面加载后自动显示前 10 条，不再输入群 ID。

每条显示：

- 图片预览。
- 来源群。
- 抓取时间。
- 重复出现次数。
- 自动标签。
- 情绪。
- 适合场景。
- 避免场景。
- 置信度。

### 编辑

可展开编辑区：

- 名称。
- 说明。
- 标签、情绪、适合场景、避免场景，使用逗号分隔。
- 保存修改按钮。
- 批准前如有未保存修改，先保存再批准。
- 保存失败时不批准。

### 审批刷新

设当前已加载 `loadedCount`：

1. 审批前记住 `loadedCount`。
2. 请求 `GET /api/stickers/review?limit=loadedCount`。
3. 整体替换列表。
4. 有足够数据时仍显示相同数量。
5. 不足时显示实际剩余数量。
6. 保持当前滚动位置。

### 滚动加载

- 初始 10 条。
- 使用 `IntersectionObserver` 监听底部 sentinel。
- 进入视口后加载下 10 条。
- 加载中显示提示。
- 同一游标禁止并发请求。
- 按 `(mediaId, groupId)` 去重。
- 没有下一页时显示“已全部加载”。
- 不支持 IntersectionObserver 时显示“加载更多”按钮。

## 八、合并日志 API

```http
GET /api/logs?type=all&limit=20&cursor=<opaque>
```

参数：

```python
type: Literal["all", "wake", "audit"] = "all"
limit: int = 20
cursor: str | None = None
```

排序：

```text
timestamp DESC, source_priority DESC, source_id DESC
```

字段映射：

- `wake_log.created_at` -> timestamp。
- `audit_logs.timestamp` -> timestamp。
- wake 的 category、reason、decided。
- audit 的 action、detail、result。

返回：

```json
{
  "items": [
    {
      "id": "audit:318",
      "type": "audit",
      "timestamp": 1789540000000,
      "sessionId": "group:100000",
      "title": "sticker_review_approved",
      "result": "approved",
      "detail": "media=41 group=100000"
    }
  ],
  "nextCursor": "opaque-or-null"
}
```

新增索引：

```sql
CREATE INDEX IF NOT EXISTS idx_wake_log_created_at
    ON wake_log(created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp
    ON audit_logs(timestamp DESC, id DESC);
```

## 九、合并日志 UI

位置：

- 删除现有独立“唤醒日志”和“审计”面板。
- 放在“素材审核”下方。

显示：

- 默认最近 20 条。
- 时间、类型、会话、标题、结果、详情。
- 使用文字和颜色共同区分类型。

筛选：

```text
[全部] [唤醒日志] [审计日志]
```

- 切换筛选时清空列表和游标。
- 立即加载对应类型前 20 条。
- 加载更多时保持当前筛选。
- 旧请求晚返回时不得覆盖新筛选。

滚动加载：

- 使用独立 sentinel。
- 每次追加 20 条。
- 到底后显示“已全部加载”。

## 十、页面顺序

```text
顶部状态栏
统计卡片
会话列表 + 实时事件
素材审核工作台
日志时间线
```

移动端要求：

- 小于 900px 时审核卡片单列。
- 日志改为纵向条目。
- 长标签和说明自动换行。

## 十一、工程任务

### T-29.1 群级审核状态

- 文件：`app/storage/migrations.py`、`app/storage/db.py`、`tests/test_storage_migrations.py`
- 验收：旧库升级成功；A 群批准不影响 B 群。

### T-29.2 跨群分页 API

- 文件：`app/storage/db.py`、`app/console/service.py`、`app/console/router.py`、`tests/test_console_stickers.py`
- 验收：无 groupId 返回跨群列表；游标无重复和遗漏。

### T-29.3 元数据编辑

- 文件：`app/console/router.py`、`app/console/service.py`、`app/console/public/console.html`、`tests/test_console_stickers.py`
- 验收：六个字段可保存；非法值拒绝；批准前先保存。

### T-29.4 审批保位刷新

- 文件：`app/console/public/console.html`
- 验收：10 条起；展开 30 条后审批仍显示 30 条；不足时显示实际数量。

### T-29.5 审核滚动加载

- 文件：`app/console/public/console.html`
- 验收：每次加载 10 条；无重复；有加载中和完成状态。

### T-29.6 Token 图片预览

- 文件：`app/console/public/console.html`、`tests/test_console_stickers.py`
- 验收：Token 模式图片可见；Object URL 正确释放。

### T-29.7 合并日志 API

- 文件：`app/storage/db.py`、`app/console/service.py`、`app/console/router.py`、`tests/test_console_logs.py`
- 验收：三种筛选正确；同一时间顺序稳定；默认和分页大小正确。

### T-29.8 日志 Web UI

- 文件：`app/console/public/console.html`
- 验收：位于审核下方；筛选切换；每次加载 20 条。

### T-29.9 浏览器验收

- 文件：`README.md`、`docs/acceptance/T-29-review-console-20260916.md`
- 验收：桌面和窄屏、Token 模式、审批刷新、滚动加载、日志筛选全部通过。

## 十二、执行顺序

```text
T-29.1
  -> T-29.2
  -> T-29.3
  -> T-29.4
  -> T-29.5
  -> T-29.6

T-29.1
  -> T-29.7
  -> T-29.8

T-29.4 + T-29.5 + T-29.6 + T-29.8
  -> T-29.9
```

## 十三、统一门禁

```text
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pyright
.venv\Scripts\python.exe scripts\check_docs_consistency.py
git diff --check
```

完成标准：

- 跨群自动汇总，默认 10 条。
- 滚动加载 10 条，审批后保位刷新。
- 自动标签可见，六类元数据可编辑。
- 日志合并、筛选、默认 20 条、滚动加载 20 条。
- Token 模式下图片可见。
- 群级审核互不影响。
- 不引入 Node 或前端构建依赖。

## 十四、假设

1. 新待审核素材排在最前。
2. 元数据全局作用于 media，审核状态按群隔离。
3. 默认只显示 pending_review。
4. IntersectionObserver 不可用时保留加载更多按钮。
5. 合并日志沿用现有两张日志表。

在项目方明确说“开始执行”前，本单不修改源码。

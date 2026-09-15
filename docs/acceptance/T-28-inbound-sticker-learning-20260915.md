# T-28 群表情自动入库与语境学习真机验收

- 日期：2026-09-15
- 分支：`codex/T-28-inbound-sticker-learning`
- 测试群：单个白名单测试群，报告中不记录真实群号
- 视觉 Provider：当前配置的 OpenAI-compatible Vision Provider
- 结论：T-28.1 至 T-28.9 通过；大批量配额使用自动化边界测试，未执行 200 张 / 2 GB 真实上传

## 验收矩阵

| 编号 | 项目 | 结果 | 证据 |
| --- | --- | --- | --- |
| T28-01 | 普通图片入站 | 通过 | 真实群图片进入 `inbound_media`，保存原文件并写审计 |
| T28-02 | QQ 表情图片入站 | 通过 | 真实表情图片按 `image/mface` 路径进入同一入站链路 |
| T28-03 | SHA-256 去重 | 通过 | 同一图片重复发送只保留一个 media 行，`seen_count` 递增，不重复保存文件 |
| T28-04 | 高置信度自动放行 | 通过 | 置信度不低于 0.85 的真实素材自动写入 manifest，`auto_approved=1` |
| T28-05 | 低置信度/失败素材审核 | 通过 | 失败素材进入 `pending_review`；低置信度边界由自动化测试覆盖 |
| T28-06 | 控制台审核 | 通过 | 真机批准、拒绝成功；批准后预览返回原图字节 |
| T28-07 | 审核路径一致性 | 通过 | 批准后数据库更新为 `images/...`，拒绝后更新为 `rejected/...` |
| T28-08 | JPEG/PNG/GIF/WebP | 通过 | 四种格式均通过 MediaGuard；补 WebP 视觉 Provider 映射并真机回归 |
| T28-09 | 前后语境窗口 | 通过 | 每条观察达到 `before=8 / after=8`，写入受控中文情境标签 |
| T28-10 | 群内偏好学习 | 通过 | 真实观察生成偏好分数与使用次数，目录只输出可读经验 |
| T28-11 | 来源群隔离 | 通过 | 自动素材只在来源群 catalog/resolve 可见，另一群可见数为 0 |
| T28-12 | 重启一致性 | 通过 | migration v2/v3 在旧库升级；素材、语境和偏好跨重启保留 |
| T28-13 | 字节与数量配额 | 通过 | 数量上限、字节上限、达到上限后的重复素材、显式 rejected 清理均有测试 |
| T28-14 | 安全边界 | 通过 | 私聊、非白名单、路径穿越、私网 URL、超限媒体、未知素材均拒绝 |

## 真机统计

验收期间维护统计读到：

```text
item_count=19
byte_size=6262548
maxItems=200
maxBytes=2147483648
```

说明：

- 这组数字只用于验收，不提交到仓库，也不记录真实群号。
- 同一素材的 `seen_count` 可以重复增长，但 `item_count` 只统计不同 SHA-256。
- 自动放行素材的数据库记录保留名称、说明、标签、情绪、场景、避免场景、置信度和移动后的本地路径。

## 语境与偏好

真机验证了 8 条前文和 8 条后文的完整窗口。观察记录写入：

- `before_context_json`
- `after_context_json`
- `situation_tags_json`
- `sticker_preferences`

偏好记录只在后文窗口满足后写入；沉默、单次无回应和模型自己的使用不会增加正向分数。

## 验收中发现并修复

1. 视觉模型返回超过 8 个标签时，旧实现会把整张素材判为失败。现改为按配置上限截断。
2. 自动放行旧实现只写 manifest，不写 `inbound_media` 元数据，并把路径留在 `inbox/`。现已同步名称、说明、标签和 `images/` 路径。
3. 批准后的旧预览路径会失效；控制台批准、拒绝现在都会更新数据库路径并保持预览可用。
4. 自动抓取 ID 原先必须手工加入静态 `allowedIds` 才能发送。现在 `group_capture` 素材按来源群 `scopeGroups` 放行，不进入静态手工白名单。
5. 入站允许 WebP，但视觉 Provider 原先不支持 WebP。现已补齐 `image/webp`。
6. 达到配额时，重复素材原先也会被误报为配额拒绝。现在先去重，重复素材仍更新 `seen_count`，只有新素材受配额限制。
7. 维护命令原先缺少显式清理测试。现已覆盖 rejected 文件删除和数据库记录删除。

## 自动化门禁

```text
.venv\Scripts\python.exe -m pytest -q
318 passed, 1 warning

.venv\Scripts\python.exe -m ruff check .
All checks passed!

.venv\Scripts\python.exe -m pyright
0 errors, 0 warnings, 0 informations

.venv\Scripts\python.exe scripts\check_docs_consistency.py
docs consistency ok

git diff --check
passed
```

warning 为 FastAPI/Starlette TestClient 上游弃用提示，不是 T-28 引入。

## 遗留风险

- 没有向真实群循环上传 200 张或 2 GB 素材；配额边界、重复命中和维护清理由自动化测试覆盖。
- manifest 按既有 ADR 不热加载。运行中自动批准的新素材在下次重启后进入模型目录。
- 视觉模型标签仍可能出现语义误差，因此保留控制台人工修改、批准和拒绝路径。

## 回滚

1. 将 `sticker.ingest.enabled` 改为 `false`，停止新素材抓取。
2. 将各测试群 `visionEnabled` 改为 `false`，停止向外部视觉 Provider 发送图片。
3. 将 `sticker.enabled` 改为 `false`，关闭全部表情发送。
4. 恢复旧 manifest 和数据库备份；不得自动删除人工维护或已批准素材。

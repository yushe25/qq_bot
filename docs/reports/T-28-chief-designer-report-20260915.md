# T-28 总设计报告：群表情自动入库与语境学习

- 日期：2026-09-15
- 分支：`codex/T-28-inbound-sticker-learning`
- 结论：功能实现和真机验收完成，可以进入总设计复核
- 未执行：push、远端 CI、真实 200 张 / 2 GB 上传

## 一、交付结果

T-28.1 至 T-28.8 已按交办单拆分实现，T-28.9 已在真实白名单测试群完成验证。

主要能力：

- 白名单群图片和表情图片自动抓取。
- SHA-256 全局文件去重，群级来源和使用次数独立。
- JPEG、PNG、GIF、WebP 入站和视觉标注。
- 高置信度自动放行，低置信度或失败素材进入审核。
- 控制台查看、预览、批准、拒绝和修改元数据。
- 每群 200 张 / 2 GB 配额，达到配额停止导入新素材。
- 表情前后 8 条语境观察，受控中文情境标签和偏好分数。
- 自动抓取素材只在来源群 catalog 和发送路径可见。
- 版本化 migration、启动加载和重启一致性。

## 二、真机证据

真实测试群完成了：

- 普通图片和 QQ 表情图片抓取。
- 同一图片多次发送去重。
- 高置信度自动入库。
- 待审核素材人工批准和拒绝。
- 批准、拒绝后的文件移动和数据库路径更新。
- 8/8 前文后文窗口和偏好写库。
- 来源群隔离检查。

验收期间维护统计为 19 个不同素材、6,262,548 字节，配额仍远低于 200 张 / 2 GB。

## 三、真机暴露并收口的问题

- 标签数量超限从整张失败改为按上限截断。
- 自动放行的元数据和本地路径同步落库。
- 控制台批准、拒绝后的预览路径保持有效。
- 自动抓取素材无需静态 `allowedIds`，仍按来源群隔离。
- WebP 补入视觉 Provider MIME 映射。
- 配额判断先做 hash 去重，重复素材不会被误报为新导入。
- rejected 清理增加显式维护测试。

## 四、门禁

```text
pytest -q -> 318 passed, 1 warning
ruff check . -> All checks passed!
pyright -> 0 errors, 0 warnings, 0 informations
check_docs_consistency.py -> docs consistency ok
git diff --check -> passed
```

warning 为 FastAPI/Starlette TestClient 上游弃用提示。

## 五、已有提交

```text
611ffb4 feat(T-28): add per-group sticker ingest configuration and isolation
6e7cd9a feat(T-28): download and quarantine inbound sticker images
1e968b2 feat(T-28): auto-tag and promote high-confidence stickers
f00426a feat(T-28): observe sticker context and situation tags
511226b feat(T-28): learn per-group sticker preferences
1b69aee feat(T-28): add sticker review console and maintenance
```

真机验收中发现的修复、测试、设计契约和验收文档将在最后一个 T-28 本地提交中收口。不 push。

## 六、遗留风险与建议

- 未执行真实 200 张 / 2 GB 上传，建议作为后续独立压力验证，不与功能验收混用。
- manifest 不热加载，自动入库素材重启后生效，符合当前 ADR。
- 视觉标签是自动生成结果，保留人工审核和修正作为最终控制面。
- T-27 长跑基线和远端 CI 仍按各自任务独立推进，不并入 T-28。

## 七、结论

T-28 的实现、真机验收、安全边界、回滚路径和测试门禁均已闭环。总设计复核通过后，可关闭 T-28，并保留“大批量配额压力测试”作为非阻塞后续项。

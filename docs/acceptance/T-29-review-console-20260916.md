# T-29 Web 审核工作台验收记录

- 日期：2026-09-16
- 代码基线：`db24c11`（T-29.1 至 T-29.8），浏览器验收使用当前工作区中的群级审核修复
- 交办单：`docs/reports/T-29-review-console-workbench-handoff-20260916.md`
- 结论：T-29.9 通过；桌面、窄屏、Token 初始化、审核分页与保位、元数据编辑、日志筛选和滚动加载均已验证

## 一、验收环境

- 控制台使用隔离数据库 `.pytest-tmp/t29-browser-20260916-1/qqf.db`，未读取或修改
  `state/qqf.db`。
- 数据包含两个白名单群、跨群素材、同素材多群关联、50 条唤醒/审计日志和超过 30 条待审素材。
- 浏览器使用 Codex 内置浏览器。该浏览器不支持页面调用的 `window.prompt()`；验收通过仅本机
  代理预置与手工输入等价的 `sessionStorage` Token，再访问真实页面和 API。应用代码未修改。
- 预览请求、API 请求和图片请求仍经过真实 Bearer 鉴权；长期 Token 未进入 URL。

## 二、验收矩阵

| 编号 | 项目 | 结果 | 证据 |
| --- | --- | --- | --- |
| T29-01 | 桌面布局 | 通过 | 1280px 视口下页面顺序为状态、统计、会话与实时、素材审核、日志时间线，无横向溢出 |
| T29-02 | 窄屏布局 | 通过 | 390px 视口下审核卡片为单列，日志条目为纵向布局，长说明和标签自动换行 |
| T29-03 | 跨群汇总 | 通过 | 无 `groupId` 时同时显示 `100000`、`200000` 来源群，待审核总数正确 |
| T29-04 | 初始分页 | 通过 | 首屏加载 10 条，按抓取时间倒序，重复键 `(mediaId, groupId)` 不出现 |
| T29-05 | 滚动扩展 | 通过 | 展开后可达 36 条以上，加载完成后显示“已全部加载” |
| T29-06 | 审批保位刷新 | 通过 | 已展开 36 条时批准 1 条，刷新后仍显示 36 条；总数从 38 降为 37 |
| T29-07 | 元数据编辑 | 通过 | 名称、说明、标签、情绪、适合场景、避免场景均可保存，API 回读值与页面一致 |
| T29-08 | Token 图片预览 | 通过 | 图片元素使用 `blob:` Object URL；成功预览与失败占位状态均可显示 |
| T29-09 | 日志默认分页 | 通过 | 默认显示 20 条，时间、类型、会话、标题、结果和详情均可见 |
| T29-10 | 日志筛选 | 通过 | 全部、唤醒日志、审计日志三种筛选均正确；快速切换后旧请求未覆盖新筛选 |
| T29-11 | 日志滚动加载 | 通过 | 从 20 条加载到全部 50 条，末条后显示“已全部加载” |
| T29-12 | 群级审批隔离 | 通过 | 自动化回归覆盖同素材 A 群批准不影响 B 群，B 群仍保持待审核 |

## 三、浏览器实测细节

- 桌面 1280px：页面宽度与滚动宽度一致，日志采用桌面多列布局。
- 窄屏约 390px：审核卡片计算样式为 `grid-template-columns: 1fr`，日志条目同样为单列。
- Token 模式：页面数据、审核素材和预览均通过带 `Authorization: Bearer` 的请求加载。
- 预览释放：页面前端在卡片重建、刷新和 `beforeunload` 时调用 `URL.revokeObjectURL()`；
  自动化 UI 测试同时校验调用存在。
- 审核操作后，日志时间线立即出现 `sticker_review_metadata_updated` 和
  `sticker_review_approved` 审计记录。

## 四、自动化门禁

```text
.venv\Scripts\python.exe -m pytest -q
333 passed, 1 warning

.venv\Scripts\python.exe -m ruff check .
All checks passed!

.venv\Scripts\python.exe -m pyright
0 errors, 0 warnings, 0 informations

.venv\Scripts\python.exe scripts\check_docs_consistency.py
docs consistency ok

git diff --check
passed
```

warning 为 FastAPI/Starlette TestClient 上游弃用提示，与本任务无关。

## 五、遗留风险

- 内置浏览器不支持 `window.prompt()`，因此 Token 按钮的人工弹窗流程通过本地验收代理模拟
  等价的 `sessionStorage` 状态；普通浏览器中的 Token 提示交互仍建议在真实设备上复核。
- 审核卡片图片由浏览器异步解码。验收中多数预览使用有效 `blob:` URL，失败时按设计显示
  “预览不可用”占位，不影响审批和日志功能。
- 本记录不包含真实群号、真实 Token、数据库文件或临时验收文件。

## 六、回滚

1. 回退 README 控制台章节和本验收记录。
2. 页面或 API 出现回归时，保留数据库迁移与素材文件，先回退对应前端发布物，再按 SQLite
   备份恢复数据；不得删除已批准素材或 manifest。

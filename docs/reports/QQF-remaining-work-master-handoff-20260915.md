# QQF 剩余工作总交办单

- 状态：已被 `docs/reports/QQF-chief-designer-final-report-20260915.md` 取代；
  仅保留为历史规划记录，后续不得按本单的未完成状态继续施工。
- 发布日期：2026-09-15
- 当前基线：`c899c97`
- 当前状态：T-25、T-26 已关闭；T-04 保持打开
- 本单性质：剩余工作总清单、依赖排序、分波执行
- 默认执行原则：默认只评估和规划；项目方明确说“开始执行”后才推进代码

## 一、结论

可以把剩余工作一次性整理成一份总交办单，但不能要求工程师在一个批次内全部改完。

原因：

- 真实协议验收依赖测试 QQ 在线和人工观察。
- 存储迁移和恢复演练必须单独控制风险。
- 控制台鉴权和 CI 发布会影响运行方式。
- 长跑观察必须等协议和存储修复稳定后再做。

本单只统一范围和顺序，实际执行必须按波次提交、逐波验收。

## 二、已完成且关闭

- T-01、T-02、T-03、T-01b：测试目录、批处理口径、遗留进程。
- T-04A、T-04C：mock 30/60 分钟稳定性与重启一致性。
- T-13：pyright basic 类型检查。
- T-14 本地部分：CI 锁定安装配置。
- T-16：控制台唤醒日志和结构化实时流。
- T-17：会话小时限流和去重口径。
- T-18：控制台无 token 语义文档。
- T-19、T-20：待唤醒持久化、事件去重跨重启。
- T-23、T-24：表情筛选开关、真实图片发送、JPEG 解析。
- T-25：表情语义目录、独立视觉模型、真实群选图。
- T-26：冷却期高优先级待唤醒保护。

## 三、剩余工作总表

| 编号 | 项目 | 类型 | 依赖 | 优先级 |
| --- | --- | --- | --- | --- |
| T-21 | NapCat 错误 Token 安全边界 | 安全、配置、真实环境 | 可操作 NapCat | P0 |
| T-22 | 正式出站 @ 接口 | 产品代码 | 无 | P0 |
| T-04B | 真实 OneBot/NapCat 协议矩阵 | 真实验收 | T-21、T-22、测试 QQ 在线 | P0 |
| T-05 | SQLite 版本迁移、自动备份、恢复演练 | 存储、运维 | 无 | P1 |
| T-15 | WAL checkpoint 策略 | 存储、运维 | T-05 | P1 |
| T-08 | 控制台 Token 进入 URL 的泄漏面 | 安全、控制台 | 无 | P1 |
| T-09 | 记忆和自主学习真机产出验收 | 真实验收 | T-04B | P1 |
| T-10 | 控制台操作从 Scheduler 拆出 | 维护性重构 | 无 | P2 |
| T-12 | 文档数字、行号和配置一致性校验 | 文档工程 | 功能冻结 | P2 |
| T-14 剩余 | 远端 CI 和首次 push | 发布、运维 | 功能冻结、远端可用 | P1 |
| T-27 | 真实 QQ 24 至 48 小时长跑观察 | 真实稳定性 | T-04B、T-05、T-08 | P1 |
| T-23B | 旧随机表情 24 至 48 小时观察 | 历史任务 | 已被 T-25 替代机制 | 关闭候选 |

## 四、分波执行计划

### 第一波：协议与安全补口

范围：

- T-21
- T-22
- T-08

特点：

- 不依赖真实 QQ 账号在线即可完成代码和自动测试。
- T-22 完成后才能真实执行 P08。
- T-08 完成后控制台不再把长期 Token 放入 URL。

交付后门禁：

```text
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pyright
git diff --check
```

### 第二波：真实协议矩阵

范围：

- T-04B
- T-21 的真实 NapCat 结果
- T-22 的 P08 真实结果

前置条件：

- 测试 QQ 在线。
- NapCat 可用。
- 测试群可人工观察。
- 只有回环地址，或项目方明确接受远端风险。

必须完成：

- P02 鉴权边界明确。
- P04 至 P11 逐项执行。
- P14、P15 断线和重连去重执行。
- P07、P10 将后来真实成功证据回填到矩阵。

### 第三波：存储与恢复

范围：

- T-05
- T-15

必须完成：

- schema 版本表和事务迁移。
- 迁移前自动备份。
- SQLite 在线备份和恢复命令。
- 恢复演练。
- 干净停机时 WAL checkpoint。
- WAL 文件大小监控和异常告警。

注意：

- 此波不能和业务协议修复混在一个提交。
- 任何迁移失败必须保留原库并自动停止。

### 第四波：真实长跑与发布

范围：

- T-09
- T-27
- T-12
- T-14 剩余
- T-10 可选

必须完成：

- 记忆写入、重启恢复、学习笔记真实产出。
- 真实群 24 至 48 小时运行。
- 文档与配置一致性检查。
- 配置远端、首次 push、真实 CI 执行。
- 若时间允许，再拆 Scheduler 控制台操作。

## 五、逐项任务

### T-21 NapCat 错误 Token 安全边界

- 目标：在 NapCat 错误 Token 可连接的情况下，保证 QQF 不默认暴露远端。
- 涉及文件：
  - `app/config.py`
  - `config.example.json`
  - `README.md`
  - `tests/test_config.py`
- 实现：
  - 增加 `qq.allowRemote=false`。
  - `wsUrl`、`httpUrl` 非回环时默认拒绝。
  - 显式 `allowRemote=true` 才能放行并写 WARN。
- 验收：
  - 回环地址通过。
  - 远端默认拒绝。
  - 显式远端放行有审计或日志。
  - 不打印 Token。
- 风险：会影响远端 NapCat 用户，必须配置迁移。

### T-22 正式出站 @

- 目标：模型通过产品工具安全 @ 当前群成员。
- 涉及文件：
  - `app/agent/tools.py`
  - `app/agent/tool_schemas.py`
  - `app/runtime/safety_pipeline.py`
  - `app/platform/gateway.py`
  - `app/platform/qq/onebot_client.py`
  - `tests/test_tools.py`
  - `tests/test_safety.py`
  - `tests/test_onebot_client.py`
- 数据契约：

```json
{
  "groupId": "100000",
  "message": "文件在这里",
  "mentionUserIds": ["10001"]
}
```

- 限制：
  - 最多 5 人。
  - 必须来自当前群成员列表。
  - 空值、重复、非成员、成员查询失败全部拒绝。
  - 禁止手工 CQ 码。
- 协议：
  - `reply`、若干 `at`、`text` 的顺序构造 segment。
  - mention 场景移除 `auto_escape`。
- 验收：
  - payload 顺序正确。
  - 非法成员 fail-closed。
  - 真实群出现蓝色 @。

### T-04B 真实协议矩阵

- 目标：把真实 OneBot/NapCat 覆盖从“部分可用”提升为逐项可审计。
- 涉及文件：
  - `scripts/onebot_live_acceptance.py`
  - `tests/test_onebot_live_acceptance.py`
  - `docs/acceptance/T-04B-real-protocol-20260913.md`
  - 或新增最终复验记录
- 必测：
  - HTTP 版本、WS 鉴权、生命周期。
  - 群文本、@、拍一拍入站。
  - 群文本、@、引用、表情出站。
  - 私聊出入站。
  - 历史和成员接口。
  - 断线重连和重连去重。
  - 错误动作归一化。
- 验收：
  - 逐项为 `通过`、`失败` 或 `未执行`。
  - 不得用 mock 替代真实结果。
  - 有原始脱敏证据。
  - T-04 只有在必测项全部通过或明确接受后才能关闭。

### T-05 SQLite 迁移、备份和恢复

- 目标：数据库结构变化时可安全升级和回退。
- 建议文件：
  - `app/storage/migrations.py`
  - `app/storage/db.py`
  - `scripts/backup_db.py`
  - `scripts/restore_db.py`
  - `tests/test_storage_migrations.py`
- 数据契约：

```sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at REAL NOT NULL
);
```

- 规则：
  - 每个 migration 在单事务中执行。
  - 迁移前使用 SQLite backup API 生成时间戳备份。
  - 校验 checksum，重复执行幂等。
  - 失败时停止启动并保留原库。
  - 备份默认保留最近 7 份。
- 验收：
  - 新库初始化。
  - 旧库升级。
  - 迁移失败回滚。
  - 备份恢复后 integrity_check 为 ok。
  - 恢复演练不覆盖原库，除非显式指定。

### T-15 WAL checkpoint

- 目标：控制 WAL 文件增长和干净停机行为。
- 依赖：T-05。
- 涉及文件：
  - `app/storage/db.py`
  - `app/main.py`
  - `tests/test_storage_wal.py`
- 实现：
  - 启动时读取 WAL 大小。
  - 干净停机执行 `PRAGMA wal_checkpoint(TRUNCATE)`。
  - 异常退出后下次启动校验恢复。
  - WAL 超过阈值写 WARN，不每次写入都 checkpoint。
- 验收：
  - 停机后不存在异常 WAL 残留。
  - 运行中不会频繁阻塞写。
  - 重启后数据一致。

### T-08 控制台 Token 泄漏面

- 目标：长期 Token 不再出现在 WebSocket URL、浏览器历史或访问日志。
- 涉及文件：
  - `app/console/router.py`
  - `app/console/public/console.html`
  - `tests/test_console_auth.py`
  - `README.md`
- 建议方案：
  - HTTP API 继续使用 Bearer header。
  - 新增一次性 WebSocket ticket。
  - ticket 有效期 60 秒、单次使用、绑定当前来源或会话。
  - WS URL 只携带 ticket，不携带长期 `console.token`。
  - 兼容期结束前对旧 `?token=` 记录 deprecation，不长期保留。
- 验收：
  - 长期 Token 不进入 URL。
  - ticket 重放失败。
  - 过期 ticket 失败。
  - 错误来源不能换取 ticket。

### T-09 记忆和自主学习真机验收

- 目标：证明真实模型在真实群中会写入、检索、过期和跨重启保留记忆。
- 依赖：T-04B。
- 涉及文件：
  - `docs/acceptance/T-09-memory-learning-20260915.md`
  - 必要时补充 focused tests
- 验收：
  - 真实模型调用 `memory_update`。
  - 后续 prompt 使用正确会话记忆。
  - 学习任务生成至少一条合格笔记。
  - 重启后记忆和笔记仍存在。
  - 敏感内容、成员数据删除和 TTL 生效。
  - 不把私聊或敏感数据写入群记忆。

### T-10 控制台操作拆分

- 目标：Scheduler 只负责调度，不再承载控制台业务操作。
- 优先级：P2，可在核心功能稳定后做。
- 涉及文件：
  - `app/runtime/scheduler.py`
  - 新增 `app/console/service.py`
  - `app/console/router.py`
  - `tests/test_console_*`
- 验收：
  - pause/resume/reset memory/delete learning 行为不变。
  - Scheduler 公共 API 缩小。
  - 不改变 HTTP 响应。

### T-12 文档一致性校验

- 目标：文档中的配置默认值、行号、测试数量和运行命令可自动核对。
- 涉及文件：
  - `README.md`
  - `docs/PROJECT_REPORT.md`
  - 新增 `scripts/check_docs_consistency.py`
  - `tests/test_docs_consistency.py`
- 验收：
  - 配置表能从 schema/example 校验。
  - 不写死易漂移的测试数字，或自动生成。
  - CI 可执行。

### T-14 远端 CI 和首次 push

- 目标：本地提交链转为远端可验证发布。
- 前置：
  - 第一至第三波代码冻结。
  - 远端仓库已配置。
  - 不包含本地配置、状态库、素材或密钥。
- 验收：
  - 创建 remote。
  - push 分支。
  - GitHub Actions 上 pytest、ruff、pyright 全部通过。
  - 记录真实 CI 链接或运行编号。

### T-27 真实 QQ 24 至 48 小时长跑

- 目标：验证长期真实运行，而不是只验证单次动作。
- 前置：T-04B、T-05、T-08 完成。
- 观察：
  - 连接稳定性、自动重连次数。
  - 消息重复、遗漏 @、错误目标。
  - 记忆和学习数据增长。
  - SQLite 锁、WAL 和备份。
  - 限流、每日发送熔断。
  - 群成员反馈。
- 停止条件：
  - 非白名单发送。
  - 重复发送。
  - 连续发送失败。
  - 数据库损坏或无法恢复。
  - 连接无法自愈。
  - 群成员明确反馈打扰。
- 验收：
  - 保留脱敏日志、审计、数据库快照和恢复结果。
  - 给出上线、继续观察或关闭的建议。

## 六、T-23B 处理

T-23B 的目标是验证旧随机表情追加机制，但 T-25 已删除该机制，改为模型语义选图。

建议：

- 正式关闭 T-23B 为 `superseded by T-25`。
- 不补做旧随机概率的 24 至 48 小时观察。
- 新机制的真实长期观察并入 T-27。

## 七、推荐提交拆分

第一波：

```text
fix(T-21): restrict QQ transport to loopback by default
feat(T-22): add safe outbound group mentions
fix(T-08): replace URL console token with one-time WS ticket
```

第二波：

```text
test(T-04B): complete real protocol acceptance matrix
docs(T-04B): record final protocol results
```

第三波：

```text
feat(T-05): add versioned migrations and database backups
feat(T-15): add explicit WAL checkpoint policy
```

第四波：

```text
test(T-09): record real memory and learning acceptance
test(T-27): record 24-48 hour real QQ soak
chore(T-14): enable remote CI
docs(T-12): add documentation consistency checks
refactor(T-10): extract console operations from scheduler
```

## 八、统一禁止项

- 不提交 `config.json`。
- 不提交 Token、API key、真实群号、真实 QQ 号。
- 不提交 `stickers/`、`personas/sticker-pilot.yaml`。
- 不提交 `state/`、数据库、WAL、日志。
- 不扩大到大群、多账号、集群或 Node 服务。
- 不用 mock 结果替代真实 T-04B。
- 不通过降低安全门槛换取测试通过。

## 九、总完成门槛

项目可以标记“功能完成”的最低条件：

1. 第一波代码和测试全部通过。
2. T-04B 全部必测项通过或项目方明确接受例外。
3. T-05、T-15 迁移和恢复演练通过。
4. T-08 控制台 Token 不再进入 URL。
5. T-09 记忆和自主学习真机验收通过。
6. T-27 至少连续运行 24 小时，无停止条件。
7. 远端 CI 真实执行通过。
8. 工作区没有密钥、真实数据或无关改动。

## 十、需要项目方决定

1. 是否允许远端 NapCat。推荐不允许，保持回环。
2. 是否接受 T-23B 由 T-25 取代并关闭。推荐接受。
3. 是否在功能完成后立即 push 并触发远端 CI。推荐是。
4. T-10 是否必须在首发前完成。推荐不作为首发阻塞项。

在项目方明确说“开始执行”前，本单只作为总施工依据，不修改源码。

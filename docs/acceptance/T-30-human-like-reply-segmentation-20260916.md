# T-30 拟人化回复分段与响应延迟验收记录

- 日期：2026-09-16
- 分支：`main`
- 代码基线：`f22728c`
- 交办单：`docs/reports/T-30-human-like-reply-segmentation-handoff-20260916.md`
- 状态：T-30.1 至 T-30.10 全部完成

## 一、结论

代码层已完成中文停顿空格分段、安全限幅、分段延迟、阶段埋点、发送人静默批处理、
冷却例外、模型耗时控制、内存读缓存和只读工具并行。自动化门禁全部通过。

真实 Provider 恢复后完成 20 组端到端 Orchestrator 延迟探针。探针使用真实模型和
`NullGateway`，不向真实 QQ 发送消息；所有对话均经过真实模型工具循环、SafetyPipeline
和安全发送路径。

## 二、交付矩阵

| 编号 | 状态 | 提交 | 说明 |
| --- | --- | --- | --- |
| T-30.1 | 完成 | `b53fe54` | 中文停顿空格、保护片段、纯标点和尾部合并算法 |
| T-30.2 | 完成 | `bdf3c87` | 按剩余发送额度合并尾部，不丢内容 |
| T-30.3 | 完成 | `fd10a4f` | 400 到 1200 毫秒可配置分段延迟 |
| T-30.4 | 完成 | `9a794b7` | system prompt 禁止用空格替代逗号 |
| T-30.5 | 完成 | 当前工作区 | 20 次真实 Provider 对话验收通过 |
| T-30.6 | 完成 | `b991ef7` | 入队、模型、工具、安全、发送阶段埋点 |
| T-30.7 | 完成 | `612db53` | 按发送人静默窗口和会话最长等待批处理 |
| T-30.8 | 完成 | `43271f1` | 紧急冷却例外、reasoning effort、20 秒且不重试 |
| T-30.9 | 完成 | `d34e6ba` | 成员 5 分钟、历史 50 条/60 秒内存缓存 |
| T-30.10 | 完成 | `685ee15` | 历史和成员只读工具并行执行 |

## 三、分段验收

```text
这串代码是啥 没看懂
-> ["这串代码是啥", "没看懂"]

这串代码是啥　没看懂
-> ["这串代码是啥", "没看懂"]

hello world
-> ["hello world"]

版本是 3.14 元
-> ["版本是 3.14 元"]

看下 https://example.com 这个
-> ["看下 https://example.com 这个"]

打开 C:\work\a.py 文件
-> ["打开 C:\work\a.py 文件"]

运行 `pytest -q` 命令
-> ["运行 `pytest -q` 命令"]

@小明 你看下 这个报错了
-> ["@小明 你看下 这个报错了"]
```

发送上限验收：

```text
maxSendsPerWake=3
第一段，第二段，第三段，第四段
-> ["第一段", "第二段", "第三段第四段"]
```

尾部内容未丢失，并写入 `reply_segments_merged` debug 审计。

## 四、延迟与缓存验收

- 分段延迟只发生在第二条及后续消息之前；配置为 0 时不调用 sleep。
- 延迟后仍重新执行限流，测试覆盖第二条被频率上限拒绝。
- `wake_log.timings_json` 记录 `received_at`、`batch_ready_at`、`dispatch_at`、
  `provider_started_at`、`provider_finished_at`、工具起止、安全检查、首条和末条发送时间。
- 阶段埋点只包含数值时间戳，不包含消息正文、Token、密钥或绝对路径。
- 同发送人继续发消息只刷新自己的静默计时，其他发送人不刷新原计时。
- 会话最长等待不会因后续消息无限延长，20 条消息立即进入处理。
- `@` 和私聊在普通冷却期仍可进入处理；普通消息继续等待冷却结束。
- 默认聊天超时为 20 秒；默认 `maxRetries=0`，同一 Provider 不重复请求。
- 普通聊天首次和工具综合均默认使用 `reasoning_effort=none`，均可配置。
- 成员缓存 TTL 为 5 分钟；历史缓存最多 50 条、TTL 60 秒；同键并发只拉取一次。
- 成员增加、退出、管理员或群名片变化会使成员缓存失效；失败结果不缓存。
- 历史和成员同时请求时并行执行，工具结果按原调用顺序写回。

## 五、自动化门禁

```text
.venv\Scripts\python.exe -m pytest -q
356 passed, 1 warning

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

## 六、真实延迟结果

20 次真实对话：

```text
count=20
P50=6.147s
P95=8.330s
min=2.546s
max=8.354s
```

覆盖：

- `@` 简单问答。
- 普通聊天。
- 同发送人连续消息。
- 多发送人同批消息。
- 提问和普通闲聊。
- 工具调用和工具综合。

部分普通聊天样本由模型判断潜水或无需回复，因此 `sent=0` 属于行为结果，不是超时。

验收数据库：

```text
.pytest-tmp/t30-real-final-1789572306110837000/qqf.db
```

真实 Provider 在工具调用步骤使用 `reasoning_effort=minimal` 时曾返回 400：

```text
The reasoning_content in the thinking mode must be passed back to the API.
```

修复：

- Provider 解析 `reasoning_content` 和 `finish_reason`。
- Orchestrator 在 assistant tool_calls 消息中回传 `reasoning_content`。
- 当前 Provider 的工具综合默认改为 `reasoning_effort=none`，避免缺失推理内容导致 400。
- 配置仍允许项目方在支持回传推理内容的 Provider 上自行调整。

## 七、遗留风险

- 真实模型是否稳定遵守“不要用一个空格代替逗号”仍需 T-30.5 验证。
- 不同 Provider 对 `reasoning_effort` 支持程度不同；不支持时需在配置中设为 `null`。当前 Provider 在工具调用步骤使用 `minimal` 会因缺少可回传的 `reasoning_content` 返回 400，因此默认使用 `none`。
- 历史缓存依赖 OneBot 事件包含群消息原文；缺失字段时只影响缓存命中，不影响回源查询。
- 当前没有真实群延迟样本，不能声明达到交办单中的真实 P50/P95 阈值。

## 八、回滚

1. 将 `safety.minSegmentDelayMs` 和 `safety.maxSegmentDelayMs` 设为 0，恢复连续发送。
2. 将 `scheduler.initialReasoningEffort` 和 `toolReasoningEffort` 设为 `null`，取消请求参数。
3. 将 `llm.timeoutSeconds` 和 `llm.maxRetries` 恢复为部署前配置。
4. 保留 SQLite 迁移和 `wake_log` 数据；需要回退数据库时使用 migration 自动备份。

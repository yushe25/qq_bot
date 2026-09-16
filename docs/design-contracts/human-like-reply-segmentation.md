# 设计契约：拟人化回复分段与响应延迟

- 状态：approved
- 关联交办单：`docs/reports/T-30-human-like-reply-segmentation-handoff-20260916.md`

## 目标

- 模型用空格表达短暂停时，出站层拆成多条 QQ 短消息。
- 分段后每条都经过统一安全管线，且总量不超过单次唤醒上限。
- 通过可配置分段间隔、静默批处理、冷却例外、缓存和并行只读工具降低机器人感，
  同时控制响应时间。

## 非目标

- 不逐字模拟输入状态。
- 不改变 persona 身份、安全规则和已有发送上限。
- 不把英文普通空格、URL、路径、版本号、代码或字面 `@` 误当作停顿。
- 不为缓存新增持久化表，也不拼接超时后的第二次同 Provider 请求。

## 触发条件

- 模型输出经过 `normalize_reply_segments()` 时执行硬标点和中文停顿空格拆分。
- 同一批次存在多条待发送文本时应用 `safety.minSegmentDelayMs` 到
  `safety.maxSegmentDelayMs` 的随机间隔，第一条不延迟。
- 调度器按发送人维护静默窗口；同发送人刷新自己的窗口，其他发送人不刷新。
- `@`、私聊和强制唤醒绕过普通冷却；普通聊天继续遵守现有冷却。

## 数据契约

分段函数：

```python
normalize_reply_segments(
    text: str,
    *,
    max_segments: int | None = None,
) -> list[str]
```

- 硬标点为 `，,。、；：`。
- 中文短句之间只有空白且两侧均至少 2 个有效字符时，可按停顿拆分。
- URL、邮箱、Windows/POSIX 路径、行内代码、版本号、数字和含字面 `@` 的片段保守处理。
- 超过 `max_segments` 时，尾部片段直接拼接进最后一条，不插入空格或标点。
- 最终结果只包含非空有效文本，不包含纯标点消息。

运行配置：

```json
{
  "safety": {
    "minSegmentDelayMs": 400,
    "maxSegmentDelayMs": 1200
  },
  "scheduler": {
    "urgentQuietWindowMs": 2000,
    "urgentMaxWaitMs": 5000,
    "normalQuietWindowMs": 4000,
    "normalMaxWaitMs": 10000,
    "initialReasoningEffort": "none",
    "toolReasoningEffort": "minimal"
  }
}
```

## 安全边界

- 每条出站分段继续经过白名单、内容过滤、频率限制、每日上限、成员数护栏和审计。
- 分段延迟发生在安全检查之前；延迟后的每次发送重新检查限流。
- 超过单次唤醒上限时只合并尾部，不绕过或扩大 `safety.maxSendsPerWake`。
- 缓存只存在于内存，不跨群共享，失败结果不缓存。
- 阶段埋点只记录时间戳，不记录消息正文、Token、密钥或绝对路径。

## 验收指标

- `这串代码是啥 没看懂` 拆成两条。
- `hello world`、`3.14`、URL、路径、行内代码和字面 `@` 不误拆。
- 多段发送不超过 `maxSendsPerWake`，超限尾部内容不丢失。
- 分段延迟可由配置改为 0，且不会绕过限流。
- 同发送人静默计时独立，多发送人到期后可合并一轮。
- 普通聊天首次和工具综合使用配置的 reasoning effort。
- 模型单次请求默认最多 20 秒，同 Provider 不重试；备用 Provider 最多切换一次。
- 成员缓存 TTL 为 5 分钟，历史缓存 TTL 为 60 秒且最多 50 条。

## 失败与回滚

- 分段算法异常时，SafetyPipeline 必须拒绝该次发送并写审计，不能原样发送。
- 分段延迟配置为 0 时恢复连续发送。
- 缓存查询失败时不写入缓存，并按原 fail-closed 规则处理安全相关查询。
- 阶段埋点写库失败只记录错误，不阻断回复。
- 回滚时保留现有消息和数据库迁移，恢复旧配置即可关闭分段延迟和新增行为。

## 开放问题

- 真实模型 P50/P95 指标需要凭据、真实群和网络环境，作为独立真实环境验收项处理。

## 实现记录

- `normalize_reply_segments()` 支持中英文硬标点、中文停顿空格、保护片段和尾部合并。
- SafetyPipeline 在发送前按剩余额度合并超限尾部，并执行可配置分段延迟和逐条安全校验。
- Scheduler 使用发送人静默窗口、会话最长等待和 20 条强制 flush；紧急消息绕过普通冷却。
- Orchestrator 使用可配置 reasoning effort，并在同一轮并行执行历史和成员只读工具。
- OneBot 网关使用内存 LRU/TTL 缓存群成员和历史，消息事件增量更新，成员变化事件失效。
- 阶段耗时写入 `wake_log.timings_json`，失败路径也保留已到达阶段。
- 真实 Provider 回归已尝试但当前环境连接失败，需要可访问的 Provider 环境完成 P50/P95 验收。

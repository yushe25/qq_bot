# 设计契约：Token 预算与调用裁剪

- 状态：implemented
- 目标：在不破坏拟真、安全与工具循环完整性的前提下，降低唤醒频率、上下文体积和无效模型调用。

## 目标

- 普通消息默认少唤醒，批处理窗口更长，发言后的冷却时间更长。
- 单次唤醒只携带最近 10 到 12 条事件，每条事件有长度上限。
- 会话历史保留最近 24 到 30 条消息，截断时不能拆开 assistant tool call 与 tool result。
- 群历史工具默认返回 8 到 10 条，工具结果和出站单条消息都有硬长度上限。
- 连续重复消息和同一唤醒内的重复工具结果要压缩。
- 工具定义按固定顺序构造；最后一次模型调用不再携带工具定义。
- OpenAI 兼容 provider 返回的 prompt/completion token 写入 `wake_log`。

## 非目标

- 不引入本地 tokenizer 依赖，不按 token 数假装精确计算上下文。
- 不删除记忆、学习或安全过滤来换取 token 节省。
- 不改变安全管线的顺序，也不允许工具绕过安全管线。

## 触发条件

以下限制在每次普通唤醒和定时唤醒中生效：

- `scheduler.batchWindowMs` 默认 8000ms。
- `scheduler.wakeProbability` 默认 0.04。
- `scheduler.cooldownSeconds` 默认 90 秒。
- `llm.maxTokens` 默认 200。
- `tokenBudget.wakeMessages` 默认 12。
- `tokenBudget.historyMessages` 默认 28。
- `tokenBudget.eventTextChars` 默认 160。
- `tokenBudget.toolResultChars` 默认 1600。
- `qq_get_group_history.limit` 默认 10，最大仍为 50。

学习任务不占用普通唤醒预算，但学习请求的 `max_tokens` 不超过 256。

## 数据契约

### 唤醒日志

`wake_log` 增加：

```json
{
  "prompt_tokens": 1234,
  "completion_tokens": 67,
  "total_tokens": 1301
}
```

字段为非负整数，缺省为 `0`。provider 不提供 usage 时记录 `0`，不伪造 token 数。mock provider 可以返回基于请求和响应长度的近似值，用于端到端验证。

### 会话历史

- `historyMessages` 计的是 OpenAI message 数量，不是用户轮数。
- 截断只保留尾部。
- 如果尾部起点是 `tool`，向前扩展到对应的 assistant tool call，保证 API 消息配对完整。

### 工具结果

- `qq_get_group_history` 返回的每条历史消息最多 `eventTextChars`，整体工具结果最多 `toolResultChars`。
- 同一唤醒内出现相同工具结果时，后续结果压缩为一条短提示，并保留对应的 `tool_call_id`。
- 未执行工具仍补“已取消”结果，不能为了压缩而破坏 tool call/result 配对。

## 安全边界

- 长度裁剪只影响模型可见文本，不改变原始事件、安全过滤、审计和限流。
- 出站消息仍受 `safety.maxMessageChars` 限制，默认降到 200 字符。
- 敏感内容过滤在裁剪前或裁剪后都必须保持拒绝语义；任何裁剪都不能让敏感模式绕过 ContentFilter。
- 工具 schema 裁剪只移除未实现/unavailable 工具，不隐藏已实现的只读工具或发送工具。

## 验收指标

- 默认配置满足上述数值，且普通消息不会因为概率变化被硬性固定回复。
- 构造 20 条最近事件时，唤醒 prompt 只包含最近 12 条；连续相同刷屏合并为带次数的单行。
- 构造超过 28 条的历史时，保存和发送给 provider 的历史都不超过上限，且 tool call 批次不被从中间切开。
- `qq_get_group_history` 省略 limit 时实际使用 10。
- 工具结果超过上限时会截断，重复结果出现压缩标记。
- 最后一次允许的模型步骤收到空 tools；前面步骤的工具顺序与 `build_tool_schemas()` 一致。
- OpenAI usage 与 mock usage 能写入 `wake_log.prompt_tokens`、`completion_tokens`、`total_tokens`。
- 新增测试覆盖安全路径不受影响，现有全部测试继续通过。

## 失败与回滚

- provider 不返回 usage：保留 0，不影响回复。
- wake_log 写入失败：记录错误但不阻断当前会话；下一次唤醒继续正常执行。
- 配置值非法：使用默认值；实现阶段在配置校验中拒绝非正数历史/消息上限。
- 回滚方式：恢复旧配置默认值即可，不改变持久化数据结构；新增日志列保留，不影响旧代码读取。

## 开放问题

- 是否按模型实际 token 数动态调整 `historyMessages`。
- 是否为不同群配置不同的唤醒概率和冷却时间。

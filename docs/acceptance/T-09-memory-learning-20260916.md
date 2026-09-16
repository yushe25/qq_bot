# T-09 记忆与自主学习真机验收

- 日期：2026-09-16
- 基线：`75baa0d`
- 结论：通过
- 测试环境：当前 `config.json` 的真实 OpenAI-compatible 模型
- 数据环境：隔离 SQLite 数据库，使用合成群聊样本，不包含真实群号或私聊内容

## 一、根因

真实学习请求首次失败时返回：

```text
empty learning response
```

进一步检查原始响应：

```text
finish_reason=length
content_len=0
reasoning_len=873
completion_tokens=256
reasoning_tokens=256
```

根因：

- 当前模型是推理模型。
- 学习请求最大输出为 `256`。
- 全部输出预算被 `reasoning_content` 消耗。
- 最终 JSON 正文没有开始生成，因此结果为空。

单独提高预算后仍可能被更多推理内容占满。当前接口确认支持：

```json
{"reasoning_effort": "none"}
```

设置后模型不再生成推理内容，直接返回 JSON。

## 二、修复

- `LearningConfig` 增加 `max_output_tokens`，默认 `1024`，范围 256 到 4096。
- `LearningConfig` 增加 `reasoning_effort`，默认 `none`。
- 学习请求使用上述两项配置。
- `ChatRequest` 增加可选 `reasoning_effort`。
- OpenAI-compatible Provider 仅在配置非空时发送 `reasoning_effort`。
- `ChatResult` 增加 `finish_reason` 和 `reasoning_tokens`。
- Provider 解析 `finish_reason` 和 `completion_tokens_details.reasoning_tokens`。
- 空学习响应现在会在错误中携带结束原因和推理 Token 数，便于后续诊断。

## 三、真实学习结果

使用 12 条合成群聊消息执行一次完整学习：

```text
result: ok=True
accepted=3
notes_written=3
```

写入类型：

```text
group_norm
speech_style
topic_trend
```

抽查第一条笔记：

```text
kind=topic_trend
confidence=0.8
```

笔记内容只包含群聊表达、互动习惯和话题趋势，没有身份、账号、手机号或私聊信息。

隔离数据库：

```text
.pytest-tmp/t09-real-fixed-1789563264854217200/qqf.db
```

## 四、跨重启与注入

关闭并重新打开 SQLite 后：

```text
reloaded=3
kinds=group_norm,speech_style,topic_trend
injection_has_header=True
injection_len=168
```

确认：

- 学习笔记跨重启保留。
- 三类笔记均可重新加载。
- `learning_notes_text()` 能生成“群内说话习惯”注入文本。
- 学习内容仍受既有置信度、敏感过滤、条数上限和 TTL 约束。

## 五、自动化门禁

```text
pytest -q                       -> 334 passed, 1 warning
ruff check .                    -> All checks passed!
pyright                         -> 0 errors, 0 warnings, 0 informations
check_docs_consistency.py       -> docs consistency ok
git diff --check                -> passed
```

warning 为 FastAPI/Starlette TestClient 上游弃用提示，与本任务无关。

## 六、配置

```json
{
  "learning": {
    "maxOutputTokens": 1024,
    "reasoningEffort": "none"
  }
}
```

说明：

- 推理模型建议保持 `reasoningEffort=none`。
- 不支持 `reasoning_effort` 的兼容服务可设为 `null`。
- 如果模型仍返回空正文，错误日志会包含 `finish_reason` 和 `reasoning_tokens`。

## 七、结论

T-09 的真实模型学习、三类笔记写入、跨重启恢复和 prompt 注入均已验证，可以关闭。

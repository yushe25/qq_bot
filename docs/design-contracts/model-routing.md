# 模型 Provider 路由

- 状态：implemented
- 日期：2026-09-10
- 关联文档：README.md、docs/decisions/0004-domestic-model-providers.md

## 目标

让 QQF 在不改变 `agent/` 与 `runtime/` 调用边界的前提下，支持多个 OpenAI-compatible
模型服务，并能把 DeepSeek、智谱、通义千问、Moonshot/Kimi、硅基流动等国内常见厂商
配置为主 Provider 或备用 Provider。

目标是减少单一模型服务故障导致的长时间沉默，同时保持模型输出仍由现有工具循环、
`SafetyPipeline` 和审计链路处理。

## 非目标

- 不实现流式输出；所有 Provider 仍使用一次完整的 chat completion 响应。
- 不为每个厂商编写独立的业务逻辑；只要厂商提供 OpenAI-compatible
  `/chat/completions`，就复用同一适配器。
- 不把模型调用直接绑定到发送动作；Provider 只返回文本或工具调用，发送仍由
  `SafetyPipeline` 负责。
- 不在本阶段实现 Anthropic Messages、Gemini 或其他非 OpenAI-compatible 协议。
- 不把 API Key 写入 JSON、日志、审计、测试快照或提交记录。

## 触发条件

- 配置中存在 `llm.providers` 时，`llm.provider` 必须指向其中一个 Provider ID。
- 未配置 `llm.providers` 时，继续使用现有扁平 `llm` 配置，保证向后兼容。
- `llm.provider` 为 `mock` 时始终使用 MockProvider，不进入远程路由。
- 主 Provider 成功时，不调用备用 Provider。
- 主 Provider 返回以下归一化错误时，允许按 `llm.fallbacks` 顺序切换：
  `RATE_LIMIT`、`TIMEOUT`、`PROVIDER_UNAVAILABLE`。
- 以下错误直接失败并写审计，不触发自动切换：
  `AUTH`、`CONTEXT_LENGTH`、`INVALID_REQUEST`。
- `llm.allowFallback=false` 或 `fallbacks=[]` 时，只调用主 Provider。

## 数据契约

`llm` 新增以下字段：

| 字段 | 类型 | 默认值 | 约束 |
| --- | --- | --- | --- |
| `provider` | string | `openai-compatible` | `mock`、`openai-compatible`，或 `providers` 中的 ID |
| `providers` | object | `{}` | key 必须是稳定的 ASCII ID；每个值是 Provider 配置 |
| `fallbacks` | string[] | `[]` | 只能引用 `providers` 中的 ID；每个 ID 最多尝试一次 |
| `allowFallback` | boolean | `true` | 关闭后即使配置了 fallback 也不切换 |

每个 Provider 配置：

| 字段 | 类型 | 默认值 | 约束 |
| --- | --- | --- | --- |
| `type` | string | `openai-compatible` | 目前只接受 `openai-compatible` |
| `baseUrl` | string | 无 | 必须是 `http://` 或 `https://`；远程地址优先使用 HTTPS |
| `apiKeyEnv` | string | 无 | 只读取环境变量，不读取 JSON 中的明文密钥 |
| `model` | string | 无 | 厂商模型名，必须非空 |
| `temperature` | number | `0.8` | `0 <= value <= 2` |
| `maxTokens` | integer | `200` | `1 <= value <= 4096` |
| `timeoutSeconds` | number | `60` | `1 <= value <= 300` |
| `maxRetries` | integer | `2` | `0 <= value <= 5` |
| `retryBaseDelaySeconds` | number | `1.0` | `0 <= value <= 30` |
| `extraHeaders` | object | `{}` | 不允许覆盖 `authorization` 或 `content-type` |
| `capabilities` | object | `{"tools": true}` | `tools` 必须为 true；本阶段 `streaming` 固定为 false |

示例：

```json
{
  "llm": {
    "provider": "deepseek",
    "fallbacks": ["zhipu"],
    "allowFallback": true,
    "providers": {
      "deepseek": {
        "type": "openai-compatible",
        "baseUrl": "https://api.deepseek.com/v1",
        "apiKeyEnv": "DEEPSEEK_API_KEY",
        "model": "deepseek-chat",
        "capabilities": {"tools": true}
      },
      "zhipu": {
        "type": "openai-compatible",
        "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
        "apiKeyEnv": "ZHIPU_API_KEY",
        "model": "glm-4-flash",
        "capabilities": {"tools": true}
      }
    }
  }
}
```

建议内置到 `config.example.json` 注释说明中的国内常见配置：

| Provider ID | Base URL | API Key 环境变量 |
| --- | --- | --- |
| `deepseek` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| `zhipu` | `https://open.bigmodel.cn/api/paas/v4` | `ZHIPU_API_KEY` |
| `qwen` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` |
| `moonshot` | `https://api.moonshot.cn/v1` | `MOONSHOT_API_KEY` |
| `siliconflow` | `https://api.siliconflow.cn/v1` | `SILICONFLOW_API_KEY` |

这些是配置示例，不作为代码中的隐式默认值；模型名、地址和可用性由项目方按账号实际情况填写。

## 错误归一化

Router 将 Provider 异常转换为不包含响应正文和密钥的错误码：

| 错误码 | 触发条件 | 是否允许 fallback |
| --- | --- | --- |
| `AUTH` | HTTP 401/403 | 否 |
| `RATE_LIMIT` | HTTP 429 | 是 |
| `TIMEOUT` | 连接或读取超时 | 是 |
| `CONTEXT_LENGTH` | HTTP 400/413 且能识别为上下文超限 | 否 |
| `INVALID_REQUEST` | 其他 HTTP 400/404/422 | 否 |
| `INVALID_RESPONSE` | Provider 返回无法解析或不符合约定的响应结构 | 否 |
| `PROVIDER_UNAVAILABLE` | HTTP 5xx、连接失败、重试耗尽 | 是 |

Provider 内部的有限重试仍只针对 `429`、`5xx` 和传输错误。模型响应成功后才执行工具，
因此路由层重试不会直接重复发送 QQ 消息。

## 安全边界

- API Key 只能从 `apiKeyEnv` 指向的环境变量读取；JSON 中的 `apiKey` 字段不参与新路由。
- 日志、审计、错误消息不得包含请求头、API Key、完整 prompt、群成员隐私或模型响应正文。
- `extraHeaders` 不得覆盖 `authorization` 和 `content-type`。
- 只有 `http://127.0.0.1`、`http://localhost` 允许本地 HTTP；其他远程地址必须使用 HTTPS。
- Provider 返回的工具调用仍由现有工具白名单执行，不能被 Provider 类型绕过。
- 所有发送动作继续经过 `SafetyPipeline`；Router 只负责模型调用，不负责发送。
- 配置校验失败时启动失败，不静默回退到其他 Provider。

## 实现位置

- `app/config.py`：Provider 注册表、字段约束、环境变量 Key 解析和启动前校验。
- `app/agent/providers/router.py`：错误归一化、主备切换和统一 Provider 构建。
- `app/main.py`：`build_runtime()` 通过统一构建入口选择 mock、旧扁平配置或命名 Provider。
- `app/runtime/orchestrator.py`：命名 Provider 使用各自默认采样参数；显式请求参数（例如学习任务）仍可覆盖。

## 验收指标

- 旧版单 Provider 配置保持可用，现有模型 provider 测试全部通过。
- DeepSeek 与智谱配置能生成正确的 `/chat/completions` 地址、模型名和 Bearer 请求头。
- 主 Provider 成功时不调用备用 Provider。
- 主 Provider 遇到 `RATE_LIMIT`、`TIMEOUT` 或 `PROVIDER_UNAVAILABLE` 时最多切换一次。
- 主 Provider 遇到 `AUTH`、`CONTEXT_LENGTH` 或 `INVALID_REQUEST` 时不切换并写审计。
- fallback 配置不存在、引用自身或包含重复 ID 时，配置加载失败。
- API Key 缺失时启动或首次调用明确失败，错误信息和审计中不出现密钥。
- `pytest -q`、`ruff check .` 全绿；pyright 可用时同样通过。

## 失败与回滚

- 配置错误在启动阶段拒绝，避免请求进入半配置状态。
- 没有配置可用备用 Provider 时保留归一化错误码；主备链全部失败时抛出统一
  `ProviderUnavailableError`。两种情况都由 Orchestrator 写审计并结束本轮，不发送任何模型文本。
- 回滚方式是将 `llm.provider` 改回单一 `openai-compatible` 配置并清空 `providers`/`fallbacks`，
  旧配置路径保持兼容。
- 重启后不保留半成功的模型响应；会话历史只在完整响应或明确错误处理后更新。

## 已确认决策

- 示例名单为 DeepSeek、智谱、通义千问、Moonshot/Kimi、硅基流动。
- fallback 链按配置顺序执行，每个 Provider 最多尝试一次。
- Anthropic Messages 等非 OpenAI-compatible 协议留到后续独立契约，不在本轮实现。

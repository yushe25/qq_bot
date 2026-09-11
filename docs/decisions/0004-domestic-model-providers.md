# 0004. 国内模型统一走 OpenAI-compatible Provider 路由

- 状态：accepted
- 日期：2026-09-10
- 关联文档：docs/design-contracts/model-routing.md

## 背景

项目当前只支持一个扁平 OpenAI-compatible 配置。用户确认需要接入国内常见模型服务，
至少包括 DeepSeek 与智谱，并希望主 Provider 失败时具备可控的备用路径。

## 决策

- 新增多 Provider 配置和统一 Router。
- 第一阶段只接受 OpenAI-compatible `/chat/completions` Provider。
- DeepSeek、智谱、通义千问、Moonshot/Kimi、硅基流动作为配置示例，不为每家编写独立业务适配器。
- API Key 只从环境变量读取；模型请求失败只对限流、超时和服务不可用做有限的单次 fallback。
- 不实现流式输出和非 OpenAI-compatible 协议；Anthropic 等协议留待后续独立契约。

## 后果

优点是新增国内模型主要依赖配置，Router 能降低单服务故障造成的沉默，且不改变现有
工具分发和 SafetyPipeline。代价是需要维护错误归一化、主备切换测试和厂商 endpoint
兼容性说明；不同厂商对工具调用和上下文长度的支持仍可能存在差异。

# 决策记录 ADR

ADR 用来记录方向性决策。聊天中的随口建议、临时需求和 Agent 的偏好都不算正式决策；写入 ADR 后才具有约束力。

## 使用方式

1. 方向调整时新建 `docs/decisions/NNNN-title.md`。
2. 按模板填写：状态、背景、决策、后果、关联文档。
3. 项目方确认后把状态改为 `Accepted`。
4. 决策被推翻时写新 ADR，旧 ADR 标记为 `Superseded`，不要直接改写历史。

## 模板

```markdown
# NNNN. 标题

- 状态：proposed | accepted | superseded
- 日期：YYYY-MM-DD
- 关联文档：相关契约或代码路径

## 背景

为什么要做这个决策。

## 决策

我们决定做什么，明确不做什么。

## 后果

正面影响、负面影响和需要承担的维护责任。
```

## 现有决策

- [0001. 项目定位为小群单机运行时](0001-small-group-single-instance.md)
- [0002. 自主学习只学表达不学身份](0002-learning-boundary.md)
- [0003. AI 辅助开发需要契约门禁](0003-ai-dev-contract-gate.md)
- [0004. 国内模型统一走 OpenAI-compatible Provider 路由](0004-domestic-model-providers.md)

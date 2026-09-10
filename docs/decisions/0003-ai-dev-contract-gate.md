# 0003. AI 辅助开发需要契约门禁

- 状态：accepted
- 日期：2026-09-09
- 关联文档：AGENTS.md、docs/design-contracts/README.md、docs/decisions/README.md

## 背景

项目是半成品，后续主要依靠 vibe coding 和 AI Agent 推进。没有约束时，Agent 容易追加无关功能、跳过测试或悄悄改变设计方向。

## 决策

- 仓库根目录维护 AGENTS.md，Agent 开工前必须读取。
- 新功能必须先有 `docs/design-contracts/` 下的设计契约，明确触发条件、数据契约、安全边界和验收指标。
- 行为类改动必须先补测试；每次改动必须通过 pytest 和 ruff，pyright 可用时一并执行。
- 方向调整必须写 ADR，ADR 优先级高于聊天中的随口建议。

## 后果

优点：防跑偏、可回放、验收有据。
缺点：前期要花时间写契约和文档，特性推进速度比纯 vibe coding 慢。

# 设计契约

设计契约是把“想法”变成“可以开发的规格”的中间产物。没有契约的功能不进入实现。

## 使用方式

1. 新功能先创建一个 `docs/design-contracts/<feature>.md`。
2. 按下方清单填写；任何一项写不清楚，说明设计还没完成。
3. 项目方确认后再开始实现。
4. 实现过程中契约与代码同步更新，验收时按契约核对。

## 必填章节

- 目标：解决什么问题，拟真收益是什么。
- 非目标：明确不做什么，防止范围蔓延。
- 触发条件：什么情况下执行，什么情况下不执行。
- 数据契约：数据结构、JSON schema、字段约束与示例。
- 安全边界：哪些输入必须拒绝，哪些能力必须禁用。
- 验收指标：可测量的完成标准。
- 失败与回滚：异常时的行为，重启后状态是否一致。
- 开放问题：尚未拍板的决策项。

## 状态约定

- `draft`：讨论中，未经确认。
- `proposal`：已写完契约，等待项目方确认。
- `approved`：已确认，可以实现。
- `implemented`：代码已完成并通过验收。
- `superseded`：被新契约替代。
- `deprecated`：不再使用。

契约状态必须写在文件开头，并随实现推进更新。

## 现有契约

- [learning-style.md](learning-style.md)：自主学习群友说话风格（implemented）。
- [sticker-media.md](sticker-media.md)：表情包发送与入站媒体理解（implemented）。
- [schedule-behavior.md](schedule-behavior.md)：作息、延迟与发言节奏（implemented）。
- [memory-private.md](memory-private.md)：轻量记忆与 owner 私聊（implemented）。
- [token-budget.md](token-budget.md)：Token 预算、上下文裁剪与 usage 记录（implemented）。

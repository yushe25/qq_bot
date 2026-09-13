from __future__ import annotations

import json
import re
from collections.abc import Sequence

from app.agent.llm_provider import ChatRequest, ChatResult
from app.config import LLMConfig
from app.domain.types import ToolCall


class MockProvider:
    """Deterministic provider for offline smoke tests and the demo mode."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    async def complete(self, req: ChatRequest) -> ChatResult:
        if _is_learning_request(req):
            content = json.dumps(
                {
                    "notes": [
                        {
                            "kind": "speech_style",
                            "key": "群内短句习惯",
                            "value": "群里喜欢用短句聊天，常用省略号表达无语",
                            "confidence": 0.8,
                            "evidence": [
                                {
                                    "sender_id": "mock-member",
                                    "message_id": "mock-observation",
                                    "text": "这也行...",
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
            return _with_usage(
                req,
                ChatResult(
                    kind="text",
                    content=content,
                ),
                content,
            )
        # 只把最后一次用户触发之后出现的工具结果视为“本轮已行动”，
        # 已行动后以空文本收尾，避免 mock 在同一轮里既发言又安排潜水。
        if _has_tool_after_last_user(req.messages):
            return _with_usage(req, ChatResult(kind="text", content=""), "")
        current_invocation = _last_user_content(req.messages)
        if "@" in current_invocation or "强制唤醒" in current_invocation or "消息中包含 @" in current_invocation:
            calls = [
                ToolCall(
                    name="memory_update",
                    arguments={
                        "kind": "active_topic",
                        "key": "当前话题",
                        "value": "群里刚聊到开黑",
                    },
                    id="mock-memory",
                ),
                ToolCall(
                    name="qq_send_group_message",
                    arguments={"groupId": _scene_id(req.system), "message": "在的，我看到了。"},
                    id="mock-send",
                ),
            ]
            return _with_usage(
                req,
                ChatResult(kind="tool_calls", calls=calls),
                json.dumps([call.arguments for call in calls], ensure_ascii=False),
            )
        calls = [
            ToolCall(
                name="qq_dive",
                arguments={"nextWakeMs": 60_000, "reason": "demo 潜水"},
                id="mock-dive",
            )
        ]
        return _with_usage(
            req,
            ChatResult(kind="tool_calls", calls=calls),
            json.dumps(calls[0].arguments, ensure_ascii=False),
        )


def _scene_id(system_prompt: str) -> str:
    match = re.search(r"当前会话：\S+\s+(\S+)", system_prompt)
    return match.group(1) if match else "demo-group"


def _message_role(message: object) -> str:
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "role", "") or "")


def _message_content(message: object) -> str:
    if isinstance(message, dict):
        value = message.get("content")
    else:
        value = getattr(message, "content", None)
    return str(value) if value is not None else ""


def _has_tool_after_last_user(messages: Sequence[object]) -> bool:
    last_user_index = -1
    for index, message in enumerate(messages):
        if _message_role(message) == "user":
            last_user_index = index
    return any(
        _message_role(message) == "tool" and index > last_user_index
        for index, message in enumerate(messages)
    )


def _last_user_content(messages: Sequence[object]) -> str:
    return next(
        (_message_content(message) for message in reversed(messages) if _message_role(message) == "user"),
        "",
    )


def _is_learning_request(req: ChatRequest) -> bool:
    marker = req.system + "\n" + _last_user_content(req.messages)
    return "群聊风格观察器" in marker or "提炼 1 到 3 条学习笔记" in marker


def _with_usage(req: ChatRequest, result: ChatResult, response_text: str) -> ChatResult:
    prompt_text = req.system + "".join(_message_content(message) for message in req.messages)
    result.prompt_tokens = max(1, len(prompt_text) // 3)
    result.completion_tokens = max(1, len(response_text) // 3)
    result.total_tokens = result.prompt_tokens + result.completion_tokens
    return result

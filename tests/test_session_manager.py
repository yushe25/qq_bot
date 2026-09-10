from __future__ import annotations

from app.runtime.session_manager import trim_conversation


def test_trim_conversation_keeps_tool_batch_together() -> None:
    conversation = [
        {"role": "user", "content": "旧消息"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "type": "function"}]},
        {"role": "tool", "tool_call_id": "t1", "content": "结果一"},
        {"role": "tool", "tool_call_id": "t1", "content": "结果二"},
        {"role": "user", "content": "新消息"},
        {"role": "assistant", "content": "好的"},
    ]

    trimmed = trim_conversation(conversation, 3)

    assert trimmed[0]["role"] == "assistant"
    assert trimmed[0]["tool_calls"][0]["id"] == "t1"
    assert [item["role"] for item in trimmed] == ["assistant", "tool", "tool", "user", "assistant"]


def test_trim_conversation_keeps_plain_suffix() -> None:
    conversation = [
        {"role": "user", "content": "一"},
        {"role": "assistant", "content": "二"},
        {"role": "user", "content": "三"},
        {"role": "assistant", "content": "四"},
    ]

    trimmed = trim_conversation(conversation, 2)

    assert trimmed == [
        {"role": "user", "content": "三"},
        {"role": "assistant", "content": "四"},
    ]


def test_trim_conversation_passes_through_short_history() -> None:
    conversation = [{"role": "user", "content": "hi"}]

    assert trim_conversation(conversation, 10) is conversation

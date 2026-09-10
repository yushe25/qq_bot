from __future__ import annotations

from app.agent.prompt_builder import DEFAULT_PERSONA, build_invocation_context, build_system_prompt
from app.domain.types import MessageSegment, NormalizedEvent, Session, TriggerCategory, WakeInput


def test_system_prompt_enforces_reply_punctuation_style() -> None:
    session = Session(id="group:100000", scene_type="group", scene_id="100000")
    prompt = build_system_prompt(session, DEFAULT_PERSONA)

    assert "说话不用“，”" in prompt
    assert "标点只用“！”、“？”这类表达情绪的标点" in prompt
    assert "“...”用来表达无语" in prompt
    assert prompt.count("说话不用") == 1


def test_default_persona_contains_reply_style_rules() -> None:
    session = Session(id="group:100000", scene_type="group", scene_id="100000")
    prompt = build_system_prompt(session, DEFAULT_PERSONA)

    assert "说话不用“，”" in prompt
    assert "“...”用来表达无语" in prompt


def test_invocation_keeps_only_recent_events_and_bounds_each_message() -> None:
    events = tuple(
        _event(f"m-{index}", text=f"消息-{index}")
        for index in range(20)
    )
    wake = WakeInput(
        session_id="group:100000",
        category=TriggerCategory.NORMAL,
        reason="普通消息概率唤醒",
        events=events,
    )

    invocation = build_invocation_context(wake, "（无）", max_messages=12, max_event_chars=160)

    assert "消息-7" not in invocation
    assert "消息-8" in invocation
    assert "消息-19" in invocation


def test_invocation_merges_consecutive_repeated_messages() -> None:
    events = tuple(_event(f"m-{index}", text="刷屏") for index in range(4))
    wake = WakeInput(
        session_id="group:100000",
        category=TriggerCategory.NORMAL,
        reason="普通消息概率唤醒",
        events=events,
    )

    invocation = build_invocation_context(wake, "（无）", max_messages=12, max_event_chars=160)

    assert invocation.count("刷屏") == 1
    assert "连续 4 条" in invocation


def test_invocation_truncates_long_event_text() -> None:
    wake = WakeInput(
        session_id="group:100000",
        category=TriggerCategory.NORMAL,
        reason="普通消息概率唤醒",
        events=(_event("m-1", text="x" * 300),),
    )

    invocation = build_invocation_context(wake, "（无）", max_messages=12, max_event_chars=160)

    assert "x" * 160 in invocation
    assert "x" * 161 not in invocation


def _event(event_id: str, text: str) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=event_id,
        session_id="group:100000",
        timestamp=1767171600000,
        sender_id="10001",
        sender_name="阿泽",
        kind="group",
        segments=(MessageSegment(kind="text", data={"text": text}),),
        text=text,
        raw={},
        bot_id="31415926",
    )

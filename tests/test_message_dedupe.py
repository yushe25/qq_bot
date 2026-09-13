from __future__ import annotations

from app.domain.types import MessageSegment, NormalizedEvent
from app.runtime.message_dedupe import MessageDedupe


def _event(*, timestamp: float, event_id: str = "") -> NormalizedEvent:
    return NormalizedEvent(
        event_id=event_id,
        session_id="group:100000",
        timestamp=timestamp,
        sender_id="10001",
        sender_name="阿泽",
        kind="group",
        segments=(MessageSegment(kind="text", data={"text": "哈哈"}),),
        text="哈哈",
        raw={},
    )


def test_fallback_dedupe_survives_receive_minute_boundary(monkeypatch) -> None:
    clock = iter([60.0, 120.0])
    monkeypatch.setattr("app.runtime.message_dedupe.time.time", lambda: next(clock))
    dedupe = MessageDedupe()
    event = _event(timestamp=1000.0)

    assert dedupe.add(event)
    assert not dedupe.add(event)


def test_fallback_dedupe_keeps_distinct_messages_with_same_text(monkeypatch) -> None:
    monkeypatch.setattr("app.runtime.message_dedupe.time.time", lambda: 60.0)
    dedupe = MessageDedupe()

    assert dedupe.add(_event(timestamp=1000.0))
    assert dedupe.add(_event(timestamp=1001.0))

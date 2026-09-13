from __future__ import annotations

from app.domain.types import MessageSegment, NormalizedEvent
from app.runtime.message_dedupe import MessageDedupe, message_dedupe_key
from app.storage.db import StateStore
from tests.conftest import TestStack


def _event(
    *,
    timestamp: float,
    event_id: str = "",
    session_id: str = "group:100000",
) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=event_id,
        session_id=session_id,
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


def test_message_dedupe_key_prefers_event_id_and_keeps_fallback_stable() -> None:
    identified = message_dedupe_key(_event(timestamp=1000.0, event_id="message-1"))
    fallback = message_dedupe_key(_event(timestamp=1000.0))
    same_fallback = message_dedupe_key(_event(timestamp=1000.0))
    later_fallback = message_dedupe_key(_event(timestamp=1001.0))

    assert identified == "id:message-1"
    assert fallback == same_fallback
    assert fallback != later_fallback


async def test_event_claim_persists_across_store_restart(tmp_path) -> None:
    database = tmp_path / "dedupe.db"
    first = StateStore(database)
    await first.init()
    assert await first.claim_event("id:message-1", now_s=100.0, ttl_seconds=3600) is True
    assert await first.claim_event("id:message-1", now_s=101.0, ttl_seconds=3600) is False
    await first.close()

    second = StateStore(database)
    await second.init()
    assert await second.claim_event("id:message-1", now_s=102.0, ttl_seconds=3600) is False
    await second.close()


async def test_event_claim_ttl_expiry_and_prune(tmp_path) -> None:
    store = StateStore(tmp_path / "dedupe.db")
    await store.init()

    assert await store.claim_event("fallback:one", now_s=0.0, ttl_seconds=1) is True
    assert await store.claim_event("fallback:one", now_s=0.5, ttl_seconds=1) is False
    assert await store.claim_event("fallback:one", now_s=2.0, ttl_seconds=1) is True
    assert await store.claim_event("fallback:two", now_s=3.0, ttl_seconds=10) is True

    removed = await store.prune_event_dedupe(now_s=4.0, max_entries=1)
    rows = await store._query("SELECT key, seen_at, expires_at FROM event_dedupe", ())

    assert removed >= 1
    assert len(rows) == 1
    assert set(rows[0]) == {"key", "seen_at", "expires_at"}
    await store.close()


async def test_whitelist_rejected_event_is_not_claimed(stack: TestStack) -> None:
    await stack.scheduler.ingest(
        _event(timestamp=1000.0, event_id="rejected", session_id="group:999999")
    )

    rows = await stack.store._query("SELECT key FROM event_dedupe", ())

    assert rows == []

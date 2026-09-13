from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.agent.llm_provider import ChatRequest, ChatResult
from app.domain.types import (
    MessageSegment,
    NormalizedEvent,
    TriggerCategory,
    WakeInput,
    now_ms,
    wake_input_from_json,
    wake_input_to_json,
)
from app.runtime.schedule import DelayRange, ScheduleProfile
from app.runtime.session_manager import SessionManager
from app.safety.rate_limiter import RateLimiter
from app.storage.db import StateStore
from tests.conftest import TestStack


def _event(
    session_id: str,
    text: str = "",
    *,
    kind: str = "group",
    segments: tuple[MessageSegment, ...] | None = None,
    bot_id: str = "31415926",
    event_id: str = "e",
) -> NormalizedEvent:
    segs = segments or (MessageSegment(kind="text", data={"text": text}),)
    return NormalizedEvent(
        event_id=event_id,
        session_id=session_id,
        timestamp=1767171600000,
        sender_id="10001",
        sender_name="阿泽",
        kind=kind,
        segments=segs,
        text=text,
        raw={},
        bot_id=bot_id,
    )


def test_classify_mention(stack: TestStack) -> None:
    event = _event(
        "group:100000",
        segments=(
            MessageSegment(kind="at", data={"qq": "31415926"}),
            MessageSegment(kind="text", data={"text": " 在吗"}),
        ),
    )
    category, _ = stack.scheduler._classify([event])
    assert category == TriggerCategory.MENTION


def test_classify_name_question_keyword_and_poke(stack: TestStack) -> None:
    scheduler = stack.scheduler

    name_event = _event("group:100000", "小皮吃饭了吗")
    assert scheduler._classify([name_event])[0] == TriggerCategory.NAME

    question_event = _event("group:100000", "晚上吃啥？")
    assert scheduler._classify([question_event])[0] == TriggerCategory.QUESTION

    keyword_event = _event("group:100000", "晚上一起开黑")
    assert scheduler._classify([keyword_event])[0] == TriggerCategory.KEYWORD

    poke_event = _event("group:100000", kind="poke", text="[拍一拍]")
    assert scheduler._classify([poke_event])[0] == TriggerCategory.POKE

    normal_event = _event("group:100000", "今天好安静")
    assert scheduler._classify([normal_event])[0] == TriggerCategory.NORMAL


def test_classify_private_message_force_wakes(stack: TestStack) -> None:
    event = _event("private:100001", "在吗", kind="private")
    category, reason = stack.scheduler._classify([event])
    assert category == TriggerCategory.MENTION
    assert "私聊" in reason


def test_wake_input_serialization_roundtrip_excludes_raw() -> None:
    event = NormalizedEvent(
        event_id="wake-json",
        session_id="group:100000",
        timestamp=123.0,
        sender_id="10001",
        sender_name="阿泽",
        kind="group",
        segments=(
            MessageSegment(kind="at", data={"qq": "31415926"}),
            MessageSegment(kind="text", data={"text": "在吗"}),
        ),
        text="@小皮 在吗",
        raw={"private": "must-not-persist"},
        bot_id="31415926",
    )
    wake = WakeInput(
        session_id=event.session_id,
        category=TriggerCategory.MENTION,
        reason="消息中包含 @",
        events=(event,),
        probability=0.5,
        deferred=True,
        defer_until_active=True,
        phase_ready=True,
    )

    payload = wake_input_to_json(wake)
    restored = wake_input_from_json(event.session_id, payload)

    assert "must-not-persist" not in payload
    assert restored is not None
    assert restored.category == wake.category
    assert restored.reason == wake.reason
    assert restored.probability == wake.probability
    assert restored.deferred is True
    assert restored.defer_until_active is True
    assert restored.phase_ready is True
    assert restored.events[0].event_id == event.event_id
    assert restored.events[0].segments == event.segments
    assert restored.events[0].text == event.text
    assert restored.events[0].raw == {}


async def test_pending_wake_persists_and_scheduler_restores_it(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    session.next_wake_at = now_ms() + 120_000
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.MENTION,
        reason="持久化上下文",
        events=(_event(session.id, "在吗", event_id="persist-1"),),
        deferred=True,
        phase_ready=True,
    )
    await stack.store.save_deferred_wake(session, wake)
    await stack.store.close()

    reopened = StateStore(stack.config.storage.database_path)
    await reopened.init()
    sessions = SessionManager(reopened)
    await sessions.load()
    stack.scheduler.store = reopened
    stack.scheduler.sessions = sessions
    stack.scheduler._pending_wakes.clear()
    stack.scheduler._started = False

    await stack.scheduler.start()
    await stack.scheduler.start()

    restored = stack.scheduler._pending_wakes[session.id]
    assert restored == wake
    restored_session = sessions.get(session.id)
    assert restored_session is not None
    assert restored_session.next_wake_at == session.next_wake_at
    await reopened.close()


async def test_due_pending_wake_uses_original_context_and_is_deleted(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    session.next_wake_at = now_ms() - 1.0
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.FORCE,
        reason="到期恢复上下文",
        events=(_event(session.id, "恢复事件", event_id="due-1"),),
        deferred=True,
    )
    await stack.store.save_deferred_wake(session, wake)
    await stack.scheduler.start()

    await stack.scheduler.tick()
    for _ in range(100):
        if session.id not in stack.scheduler._running:
            break
        await asyncio.sleep(0.01)

    assert await stack.store.load_pending_wakes() == []
    logs = await stack.store.list_wake_logs(session.id)
    assert any(item["reason"] == wake.reason and item["decided"] == "wake" for item in logs)


async def test_pending_wake_retries_and_deletes_after_attempt_limit(
    stack: TestStack,
    monkeypatch,
) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    session.next_wake_at = now_ms() + 60_000
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.FORCE,
        reason="失败重试",
        events=(_event(session.id, "重试", event_id="retry-1"),),
        deferred=True,
    )
    await stack.store.save_deferred_wake(session, wake)

    async def fail_wake(*args, **kwargs):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(stack.orchestrator, "run_wake", fail_wake)

    await stack.scheduler._run_wake(session, wake)
    assert (await stack.store.load_pending_wakes())[0][2] == wake
    await stack.scheduler._run_wake(session, wake)
    assert len(await stack.store.load_pending_wakes()) == 1
    await stack.scheduler._run_wake(session, wake)

    assert await stack.store.load_pending_wakes() == []
    audits = await stack.store.list_audit(limit=20)
    assert any(item["action"] == "pending_wake_failed" for item in audits)


async def test_bad_or_orphan_pending_wakes_are_deleted_and_audited(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    await stack.store._execute(
        """
        INSERT INTO pending_wakes (
            session_id, category, reason, events_json, next_wake_at, updated_at
        ) VALUES (?, 'mention', 'bad', '{broken', ?, ?)
        """,
        (session.id, now_ms(), now_ms()),
    )
    await stack.store._execute(
        """
        INSERT INTO pending_wakes (
            session_id, category, reason, events_json, next_wake_at, updated_at
        ) VALUES ('group:missing', 'mention', 'orphan', '{}', ?, ?)
        """,
        (now_ms(), now_ms()),
    )

    assert await stack.store.load_pending_wakes() == []
    audits = await stack.store.list_audit(limit=20)
    assert sum(1 for item in audits if item["action"] == "pending_wake_rejected") == 2


async def test_ingest_deduplicates_and_tick_logs_no_action(stack: TestStack, monkeypatch) -> None:
    # Pin the local clock inside an active window so this test exercises wake
    # probability, not whichever phase the wall clock happens to be in.
    monkeypatch.setattr(
        "app.runtime.scheduler.now_local",
        lambda profile: datetime(2026, 9, 9, 20, 0, tzinfo=timezone(timedelta(hours=8))),
    )
    session_id = "group:100000"
    event = _event(session_id, "今天好安静", event_id="unique-1")

    await stack.scheduler.ingest(event)
    await stack.scheduler.ingest(event)

    assert len(stack.scheduler._inbox[session_id]) == 1

    await asyncio.sleep(0.01)
    await stack.scheduler.tick()

    assert session_id not in stack.scheduler._inbox
    logs = await stack.store.list_wake_logs(session_id)
    assert any(item["decided"] == "no_action" for item in logs)


async def test_ignore_own_message(stack: TestStack) -> None:
    session_id = "group:100000"
    event = _event(session_id, "自己说的", event_id="own-1", bot_id="31415926")
    event = NormalizedEvent(
        event_id="own-1",
        session_id=session_id,
        timestamp=1767171600000,
        sender_id="31415926",
        sender_name="自己",
        kind="group",
        segments=(MessageSegment(kind="text", data={"text": "自己说的"}),),
        text="自己说的",
        raw={},
        bot_id="31415926",
    )
    await stack.scheduler.ingest(event)
    assert session_id not in stack.scheduler._inbox


async def test_rate_limited_wake_is_deferred_with_pending_context_and_tick_consumes_it(
    stack: TestStack, monkeypatch
) -> None:
    # Keep the phase active so the retry path under test is rate limiting.
    monkeypatch.setattr(
        "app.runtime.scheduler.now_local",
        lambda profile: datetime(2026, 9, 9, 20, 0, tzinfo=timezone(timedelta(hours=8))),
    )
    session = stack.sessions.ensure_session("group", "100000")
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.MENTION,
        reason="消息中包含 @",
    )
    stack.scheduler.wake_limiter = RateLimiter(max_per_minute=1, max_per_hour=100)
    assert stack.scheduler.wake_limiter.allow(session.id)
    assert not stack.scheduler.wake_limiter.allow(session.id)

    await stack.scheduler._decide_and_run(session, wake)

    assert stack.scheduler._pending_wakes[session.id] == wake
    assert session.next_wake_at > now_ms()
    logs = await stack.store.list_wake_logs(session.id)
    assert any(item["decided"] == "deferred" and item["reason"] == wake.reason for item in logs)

    stack.scheduler.wake_limiter.reset()
    session.next_wake_at = now_ms() - 1.0
    await stack.scheduler.tick()

    assert session.id not in stack.scheduler._pending_wakes
    logs = await stack.store.list_wake_logs(session.id)
    assert any(item["decided"] == "wake" and item["reason"] == wake.reason for item in logs)


async def test_quiet_window_defers_non_owner_mention(stack: TestStack, monkeypatch) -> None:
    stack.scheduler._schedule = ScheduleProfile(
        timezone="Asia/Shanghai",
        active_windows=((9, 12), (19, 24)),
        quiet_window=(1, 8),
        reply_delay=DelayRange(0, 0),
        mention_delay=DelayRange(0, 0),
        sticker_only_probability=0,
        sticker_with_text_probability=0,
    )
    monkeypatch.setattr(
        "app.runtime.scheduler.now_local",
        lambda profile: datetime(2026, 9, 9, 2, 0, tzinfo=timezone(timedelta(hours=8))),
    )
    session = stack.sessions.ensure_session("group", "100000")
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.MENTION,
        reason="消息中包含 @",
        events=(_event(session.id, "在吗"),),
    )

    await stack.scheduler._decide_and_run(session, wake)

    assert session.next_wake_at > now_ms() + 6 * 3600_000
    assert stack.scheduler._pending_wakes[session.id].deferred is True


async def test_quiet_window_allows_owner_with_mention_delay(stack: TestStack, monkeypatch) -> None:
    stack.scheduler._schedule = ScheduleProfile(
        timezone="Asia/Shanghai",
        active_windows=((9, 12), (19, 24)),
        quiet_window=(1, 8),
        reply_delay=DelayRange(0, 0),
        mention_delay=DelayRange(5, 5),
        sticker_only_probability=0,
        sticker_with_text_probability=0,
    )
    stack.whitelist.owner_qq = "10001"
    monkeypatch.setattr(
        "app.runtime.scheduler.now_local",
        lambda profile: datetime(2026, 9, 9, 2, 0, tzinfo=timezone(timedelta(hours=8))),
    )
    session = stack.sessions.ensure_session("group", "100000")
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.MENTION,
        reason="消息中包含 @",
        events=(_event(session.id, "在吗"),),
    )

    await stack.scheduler._decide_and_run(session, wake)

    assert 0 < session.next_wake_at - now_ms() < 60_000
    assert stack.scheduler._pending_wakes[session.id].deferred is True


async def test_slow_response_runs_after_phase_delay_elapses(stack: TestStack, monkeypatch) -> None:
    stack.scheduler._schedule = ScheduleProfile(
        timezone="Asia/Shanghai",
        active_windows=((9, 12), (19, 24)),
        quiet_window=(1, 8),
        reply_delay=DelayRange(0, 0),
        mention_delay=DelayRange(0, 0),
        sticker_only_probability=0,
        sticker_with_text_probability=0,
    )
    monkeypatch.setattr(
        "app.runtime.scheduler.now_local",
        lambda profile: datetime(2026, 9, 9, 14, 0, tzinfo=timezone(timedelta(hours=8))),
    )
    session = stack.sessions.ensure_session("group", "100000")
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.MENTION,
        reason="消息中包含 @",
        events=(_event(session.id, "在吗"),),
    )

    await stack.scheduler._decide_and_run(session, wake)

    pending = stack.scheduler._pending_wakes[session.id]
    assert pending.phase_ready is True

    session.next_wake_at = now_ms() - 1.0
    await stack.scheduler.tick()

    logs = await stack.store.list_wake_logs(session.id)
    assert any(item["decided"] == "wake" and item["reason"] == wake.reason for item in logs)


async def test_wake_records_provider_token_usage(stack: TestStack) -> None:
    class UsageProvider:
        async def complete(self, req: ChatRequest) -> ChatResult:
            return ChatResult(
                kind="text",
                content="收到",
                prompt_tokens=41,
                completion_tokens=5,
                total_tokens=46,
            )

    stack.orchestrator.provider = UsageProvider()
    session = stack.sessions.ensure_session("group", "100000")

    await stack.scheduler._decide_and_run(
        session,
        WakeInput(
            session_id=session.id,
            category=TriggerCategory.FORCE,
            reason="测试 token usage",
        ),
    )
    logs: list[dict[str, object]] = []
    for _ in range(100):
        logs = await stack.store.list_wake_logs(session.id)
        if any(item.get("decided") == "wake" and item.get("total_tokens") for item in logs):
            break
        await asyncio.sleep(0.01)

    usage_log = next(item for item in logs if item.get("decided") == "wake")
    assert usage_log["prompt_tokens"] == 41
    assert usage_log["completion_tokens"] == 5
    assert usage_log["total_tokens"] == 46


async def test_token_usage_log_failure_does_not_block_wake(stack: TestStack, monkeypatch) -> None:
    class UsageProvider:
        async def complete(self, req: ChatRequest) -> ChatResult:
            return ChatResult(
                kind="text",
                content="收到",
                prompt_tokens=7,
                completion_tokens=2,
                total_tokens=9,
            )

    async def fail_to_update(*args, **kwargs) -> None:
        raise RuntimeError("log unavailable")

    monkeypatch.setattr(stack.store, "update_latest_wake_log_usage", fail_to_update)
    stack.orchestrator.provider = UsageProvider()
    session = stack.sessions.ensure_session("group", "100000")

    await stack.scheduler._decide_and_run(
        session,
        WakeInput(
            session_id=session.id,
            category=TriggerCategory.FORCE,
            reason="测试 usage 写入失败",
        ),
    )
    for _ in range(100):
        if session.id not in stack.scheduler._running:
            break
        await asyncio.sleep(0.01)

    assert session.id not in stack.scheduler._running
    assert session.wake_count == 1
    logs = await stack.store.list_wake_logs(session.id)
    assert any(item["decided"] == "wake" for item in logs)

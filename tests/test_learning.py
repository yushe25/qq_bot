from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.llm_provider import ChatRequest, ChatResult
from app.agent.providers.mock import MockProvider
from app.config import ConsoleConfig
from app.console.router import build_console_router
from app.domain.types import MessageSegment, NormalizedEvent, Session, TriggerCategory, WakeInput, now_ms
from tests.conftest import TestStack


def _event(
    session_id: str,
    text: str = "",
    *,
    segments: tuple[MessageSegment, ...] | None = None,
    sender_id: str = "10001",
    sender_name: str = "阿泽",
    kind: str = "group",
    bot_id: str = "31415926",
    event_id: str = "e",
) -> NormalizedEvent:
    segs = segments or (MessageSegment(kind="text", data={"text": text}),)
    return NormalizedEvent(
        event_id=event_id,
        session_id=session_id,
        timestamp=1767171600000,
        sender_id=sender_id,
        sender_name=sender_name,
        kind=kind,
        segments=segs,
        text=text,
        raw={},
        bot_id=bot_id,
    )


def _note(
    kind: str = "speech_style",
    key: str = "短句习惯",
    value: str = "群里常用短句，爱用省略号",
    confidence: float = 0.9,
    sender_id: str = "10001",
    text: str = "绝了 这也能赢",
) -> dict[str, object]:
    return {
        "kind": kind,
        "key": key,
        "value": value,
        "confidence": confidence,
        "evidence": [{"sender_id": sender_id, "message_id": "m1", "text": text}],
    }


class _NotesProvider:
    def __init__(self, notes: list[dict[str, object]] | None = None, payloads: list[str] | None = None) -> None:
        self.notes = notes or []
        self.payloads = list(payloads or [])
        self.calls = 0
        self.last_request: ChatRequest | None = None

    async def complete(self, req: ChatRequest) -> ChatResult:
        self.calls += 1
        self.last_request = req
        if self.payloads:
            payload = self.payloads.pop(0)
            return ChatResult(kind="text", content=payload)
        return ChatResult(kind="text", content=json.dumps({"notes": self.notes}, ensure_ascii=False))


class _RaisingProvider(_NotesProvider):
    async def complete(self, req: ChatRequest) -> ChatResult:
        raise RuntimeError("learning llm down")


class _CapturingProvider(MockProvider):
    def __init__(self, config):
        super().__init__(config)
        self.last_system = ""

    async def complete(self, req: ChatRequest) -> ChatResult:
        self.last_system = req.system
        return await super().complete(req)


async def _seed_observations(stack: TestStack, session_id: str, count: int = 2) -> None:
    for index in range(count):
        await stack.store.append_observation(
            observation_id=f"obs-{index}",
            session_id=session_id,
            sender_id="10001",
            text=f"群消息 {index}",
            kind="group_message",
            ts=1767171600000.0 + index,
            max_per_session=100,
        )


async def test_ingest_captures_only_group_text_observations(stack: TestStack) -> None:
    stack.config.learning.exclude_members = ["10002"]
    session_id = "group:100000"
    await stack.scheduler.ingest(_event(session_id, "纯文本消息", event_id="text-1"))
    await stack.scheduler.ingest(
        _event(
            session_id,
            "[图片]",
            event_id="image-1",
            segments=(MessageSegment(kind="image", data={"file": "1.jpg"}),),
        )
    )
    await stack.scheduler.ingest(_event(session_id, "被排除成员", sender_id="10002", event_id="excluded-1"))
    await stack.scheduler.ingest(_event("private:100001", "私聊不进学习", event_id="private-1", kind="private"))
    await stack.scheduler.ingest(
        _event(session_id, "自己的消息", sender_id="31415926", bot_id="31415926", event_id="own-1")
    )

    await stack.scheduler.tick()

    items = await stack.store.list_observations(session_id)
    assert [(item["text"], item["kind"]) for item in items] == [("纯文本消息", "group_message")]


async def test_scheduler_triggers_learning_after_interval_and_message_count(stack: TestStack) -> None:
    stack.config.learning.interval_minutes = 0
    stack.config.learning.min_new_messages = 1
    stack.learning.provider = _NotesProvider(notes=[_note()])
    session_id = "group:100000"
    await stack.scheduler.ingest(_event(session_id, "晚上一起开黑？", event_id="trigger-1"))

    await stack.scheduler.tick()
    await stack.learning.wait_idle()

    notes = await stack.store.list_learning_notes(session_id)
    assert len(notes) == 1
    assert notes[0]["kind"] == "speech_style"
    state = await stack.store.get_learning_state(session_id)
    assert state["last_attempt_at"] > 0
    assert state["last_learning_at"] > 0
    audit = await stack.store.list_audit(limit=50)
    assert any(item["action"] == "learning" and "notes=1" in item["result"] for item in audit)


async def test_learning_runner_rejects_sensitive_low_confidence_and_invalid_kind(
    stack: TestStack,
) -> None:
    stack.config.learning.confidence_threshold = 0.7
    session_id = "group:100000"
    valid = _note()
    low = _note(key="低置信度", value="不写", confidence=0.5)
    sensitive = _note(key="手机号", value="联系 13800138000")
    invalid_kind = _note(kind="identity", key="真名", value="张三")
    stack.learning.provider = _NotesProvider(notes=[valid, low, sensitive, invalid_kind])
    await _seed_observations(stack, session_id, count=4)

    result = await stack.learning.run_once(stack.sessions.ensure_session("group", "100000"))

    assert result["accepted"] == 1
    notes = await stack.store.list_learning_notes(session_id)
    assert [item["key"] for item in notes] == ["短句习惯"]
    audit = await stack.store.list_audit(limit=50)
    rejected = [item for item in audit if item["action"] == "learning_note_rejected"]
    assert len(rejected) == 3
    assert any("敏感" in item["detail"] for item in rejected)


async def test_learning_retries_invalid_json_once_then_writes(stack: TestStack) -> None:
    session_id = "group:100000"
    stack.learning.provider = _NotesProvider(
        payloads=[
            "不是 JSON",
            json.dumps({"notes": [_note(key="重试成功", value="第二次输出")]}, ensure_ascii=False),
        ]
    )
    await _seed_observations(stack, session_id, count=1)

    result = await stack.learning.run_once(stack.sessions.ensure_session("group", "100000"))

    assert result["accepted"] == 1
    assert stack.learning.provider.calls == 2
    assert stack.learning.provider.last_request is not None


class _AlwaysInvalidProvider(_NotesProvider):
    async def complete(self, req: ChatRequest) -> ChatResult:
        return ChatResult(kind="text", content="还是不是 JSON")


async def test_learning_invalid_json_twice_fails_and_audits(stack: TestStack) -> None:
    session_id = "group:100000"
    stack.learning.provider = _AlwaysInvalidProvider()
    await _seed_observations(stack, session_id, count=1)

    result = await stack.learning.run_once(stack.sessions.ensure_session("group", "100000"))

    assert result["ok"] is False
    assert result["notes_written"] == 0
    audit = await stack.store.list_audit(limit=50)
    assert any(item["action"] == "learning_failed" and "JSON" in item["detail"] for item in audit)


async def test_learning_failure_is_audited_and_state_advances(stack: TestStack) -> None:
    session_id = "group:100000"
    stack.learning.provider = _RaisingProvider()
    await _seed_observations(stack, session_id, count=1)

    result = await stack.learning.run_once(stack.sessions.ensure_session("group", "100000"))

    assert result["ok"] is False
    assert "learning llm down" in result["reason"]
    state = await stack.store.get_learning_state(session_id)
    assert state["last_attempt_at"] > 0
    audit = await stack.store.list_audit(limit=50)
    assert any(item["action"] == "learning_failed" for item in audit)


async def test_learning_notes_injected_into_system_prompt(stack: TestStack) -> None:
    session_id = "group:100000"
    await stack.memory.commit_learning_note(
        session_id=session_id,
        kind="speech_style",
        key="短句习惯",
        value="群里常用短句，爱用省略号",
        confidence=0.9,
        source_members=["10001"],
        evidence=[{"sender_id": "10001", "message_id": "m1", "text": "绝了"}],
        ttl_days=7,
    )
    provider = _CapturingProvider(stack.config.llm)
    stack.orchestrator.provider = provider
    session = stack.sessions.ensure_session("group", "100000")
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.MENTION,
        reason="消息中包含 @",
    )

    await stack.orchestrator.run_wake(session, wake)

    assert "群内说话习惯" in provider.last_system
    assert "群里常用短句" in provider.last_system


async def test_prune_learning_notes_keeps_newest_and_clears_memory_snapshot(stack: TestStack) -> None:
    session_id = "group:100000"
    for index, key in enumerate(("旧笔记", "新笔记", "最新笔记")):
        await stack.memory.commit_learning_note(
            session_id=session_id,
            kind="speech_style",
            key=key,
            value=f"值 {index}",
            confidence=0.9,
            source_members=["10001"],
            evidence=[{"sender_id": "10001", "message_id": "m1", "text": "x"}],
            ttl_days=7,
        )

    await stack.memory.prune_learning_notes(session_id, max_notes=2)

    notes = await stack.store.list_learning_notes(session_id)
    assert {item["key"] for item in notes} == {"最新笔记", "新笔记"}
    snapshot = await stack.memory.learning_notes_text(session_id, limit=20)
    assert "最新笔记" in snapshot
    assert "旧笔记" not in snapshot


async def test_delete_learning_note_member_data_and_clear(stack: TestStack) -> None:
    session_id = "group:100000"
    await stack.memory.commit_learning_note(
        session_id=session_id,
        kind="speech_style",
        key="成员甲习惯",
        value="甲爱用短句",
        confidence=0.9,
        source_members=["10001"],
        evidence=[{"sender_id": "10001", "message_id": "m1", "text": "绝了"}],
        ttl_days=7,
    )
    await stack.memory.commit_learning_note(
        session_id=session_id,
        kind="speech_style",
        key="成员乙习惯",
        value="乙爱用表情",
        confidence=0.9,
        source_members=["10002"],
        evidence=[{"sender_id": "10002", "message_id": "m2", "text": "哈哈哈"}],
        ttl_days=7,
    )
    await stack.store.append_observation(
        observation_id="member-obs",
        session_id=session_id,
        sender_id="10001",
        text="甲的消息",
        kind="group_message",
        ts=1767171600000.0,
        max_per_session=100,
    )
    await stack.store.append_observation(
        observation_id="member-obs-2",
        session_id=session_id,
        sender_id="10002",
        text="乙的消息",
        kind="group_message",
        ts=1767171600001.0,
        max_per_session=100,
    )

    await stack.memory.delete_learning_note(session_id, "speech_style", "成员甲习惯")
    notes = await stack.store.list_learning_notes(session_id)
    assert "成员甲习惯" not in {item["key"] for item in notes}
    snapshot = await stack.memory.learning_notes_text(session_id, limit=20)
    assert "成员甲习惯" not in snapshot

    await stack.memory.delete_member_data(session_id, "10002")
    notes = await stack.store.list_learning_notes(session_id)
    assert "成员乙习惯" not in {item["key"] for item in notes}
    snapshot = await stack.memory.learning_notes_text(session_id, limit=20)
    assert "成员乙习惯" not in snapshot
    # Per-member deletion is isolated: member 10001's observation survives.
    remaining_observations = await stack.store.list_observations(session_id)
    assert {item["sender_id"] for item in remaining_observations} == {"10001"}

    await stack.memory.commit_learning_note(
        session_id=session_id,
        kind="group_norm",
        key="新笔记",
        value="群里习惯",
        confidence=0.9,
        source_members=["10003"],
        evidence=[{"sender_id": "10003", "message_id": "m3", "text": "习惯"}],
        ttl_days=7,
    )
    await stack.memory.clear_learning(session_id)
    assert await stack.store.list_learning_notes(session_id) == []
    snapshot = await stack.memory.learning_notes_text(session_id, limit=20)
    assert "群内说话习惯" not in snapshot


async def test_delete_expired_learning_removes_notes_and_snapshots(stack: TestStack) -> None:
    session_id = "group:100000"
    await stack.memory.commit_learning_note(
        session_id=session_id,
        kind="speech_style",
        key="已过期",
        value="过期的笔记",
        confidence=0.9,
        source_members=["10001"],
        evidence=[{"sender_id": "10001", "message_id": "m1", "text": "x"}],
        ttl_days=7,
    )
    old = now_ms() - 1000
    await stack.store.upsert_learning_note(
        session_id=session_id,
        kind="speech_style",
        key="已过期",
        value="过期的笔记",
        confidence=0.9,
        source_members=["10001"],
        evidence=[{"sender_id": "10001", "message_id": "m1", "text": "x"}],
        expires_at=old,
    )
    await stack.store.upsert_memory(
        session_id=session_id,
        kind="speech_style",
        key="已过期",
        value="过期的笔记",
        importance=2,
        expires_at=old,
    )

    await stack.store.delete_expired_learning(session_id)

    remaining_notes = await stack.store.list_learning_notes(session_id)
    assert "已过期" not in {item["key"] for item in remaining_notes}
    snapshot = await stack.memory.learning_notes_text(session_id, limit=20)
    assert "已过期" not in snapshot


async def test_console_learning_delete_endpoints() -> None:
    calls: dict[str, list[object]] = {"note": [], "member": [], "clear": []}

    async def delete_learning_note(session_id: str, kind: str, key: str) -> bool:
        calls["note"].append((session_id, kind, key))
        return True

    async def delete_member_data(session_id: str, member_id: str) -> bool:
        calls["member"].append((session_id, member_id))
        return True

    async def clear_learning(session_id: str) -> bool:
        calls["clear"].append(session_id)
        return True

    app = FastAPI()
    app.state.runtime = SimpleNamespace(
        sessions=SimpleNamespace(
            get=lambda session_id: Session(id=session_id, scene_type="group", scene_id="100000")
        ),
        console=SimpleNamespace(
            delete_learning_note=delete_learning_note,
            delete_member_data=delete_member_data,
            clear_learning=clear_learning,
        ),
    )
    app.include_router(build_console_router(ConsoleConfig(token="secret")))
    client = TestClient(app)
    headers = {"authorization": "Bearer secret"}
    session_id = "group:100000"

    response = client.delete(
        f"/api/sessions/{session_id}/learning-notes",
        headers=headers,
        params={"kind": "speech_style", "key": "短句习惯"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert calls["note"] == [("group:100000", "speech_style", "短句习惯")]

    response = client.delete(f"/api/sessions/{session_id}/learning-members/10001", headers=headers)
    assert response.status_code == 200
    assert calls["member"] == [("group:100000", "10001")]

    response = client.delete(f"/api/sessions/{session_id}/learning", headers=headers)
    assert response.status_code == 200
    assert calls["clear"] == ["group:100000"]


async def test_rate_limiter_has_capacity_does_not_consume() -> None:
    from app.safety.rate_limiter import RateLimiter

    limiter = RateLimiter(max_per_minute=1, max_per_hour=1)
    assert limiter.has_capacity("s1")
    assert limiter.has_capacity("s1")
    assert limiter.allow("s1")
    assert not limiter.has_capacity("s1")
    assert not limiter.allow("s1")

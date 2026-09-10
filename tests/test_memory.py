from __future__ import annotations

from app.agent.tools import ToolContext, execute_tool
from app.domain.types import ToolCall
from app.memory.store import MAX_SNAPSHOT_ITEMS, MAX_SNAPSHOT_VALUE_CHARS
from tests.conftest import TestStack

DAY_MS = 24 * 60 * 60 * 1000


def _set_clock(monkeypatch, start: float = 1_800_000_000_000.0) -> list[float]:
    clock = [start]

    def now() -> float:
        return clock[0]

    monkeypatch.setattr("app.memory.store.now_ms", now)
    monkeypatch.setattr("app.storage.db.now_ms", now)
    return clock


async def test_memory_update_validates_kind_and_writes_snapshot(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )

    invalid = await execute_tool(
        context,
        ToolCall(
            name="memory_update",
            arguments={"kind": "identity", "key": "真名", "value": "不该写入"},
        ),
    )

    assert invalid.success is False
    assert "kind" in invalid.message
    assert await stack.store.load_memory(session.id) == []

    valid = await execute_tool(
        context,
        ToolCall(
            name="memory_update",
            arguments={"kind": "active_topic", "key": "当前话题", "value": "晚上开黑"},
        ),
    )

    assert valid.success is True
    items = await stack.store.load_memory(session.id)
    assert [(item["kind"], item["key"], item["value"]) for item in items] == [
        ("active_topic", "当前话题", "晚上开黑")
    ]
    assert int(items[0]["importance"]) == 3
    assert float(items[0]["expires_at"]) > 0
    assert "晚上开黑" in await stack.memory.snapshot(session.id)


async def test_memory_snapshot_excludes_expired_and_learning_items(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    await stack.store.upsert_memory(
        session_id=session.id,
        kind="impression",
        key="过期印象",
        value="不该出现",
        importance=2,
        expires_at=1.0,
    )
    await stack.store.upsert_memory(
        session_id=session.id,
        kind="speech_style",
        key="学习笔记",
        value="学习内容只走独立区块",
        importance=3,
        expires_at=0.0,
    )

    snapshot = await stack.memory.snapshot(session.id)

    assert "过期印象" not in snapshot
    assert "学习笔记" not in snapshot


async def test_memory_snapshot_decays_old_high_importance_items(
    stack: TestStack,
    monkeypatch,
) -> None:
    clock = _set_clock(monkeypatch)
    session = stack.sessions.ensure_session("group", "100000")
    await stack.memory.update(session.id, "active_topic", "旧话题", "三天前的重要话题")

    clock[0] += 3 * DAY_MS
    await stack.memory.update(session.id, "impression", "新印象", "刚提到的普通印象")
    snapshot = await stack.memory.snapshot(session.id)

    assert snapshot.index("成员印象：刚提到的普通印象") < snapshot.index("当前话题：三天前的重要话题")


async def test_memory_snapshot_limits_item_count_and_value_length(
    stack: TestStack,
    monkeypatch,
) -> None:
    clock = _set_clock(monkeypatch)
    session = stack.sessions.ensure_session("group", "100000")
    for index in range(MAX_SNAPSHOT_ITEMS + 3):
        clock[0] += 1000
        value = "x" * (MAX_SNAPSHOT_VALUE_CHARS + 50) if index == 10 else f"印象 {index}"
        await stack.memory.update(session.id, "impression", f"key-{index}", value)

    snapshot = await stack.memory.snapshot(session.id)

    assert snapshot.count("\n") + 1 <= MAX_SNAPSHOT_ITEMS
    assert "印象 0" not in snapshot
    assert "印象 " + str(MAX_SNAPSHOT_ITEMS + 2) in snapshot
    assert "x" * MAX_SNAPSHOT_VALUE_CHARS in snapshot
    assert "x" * (MAX_SNAPSHOT_VALUE_CHARS + 1) not in snapshot

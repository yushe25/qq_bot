from __future__ import annotations

import asyncio

import pytest

from app.platform.qq.read_cache import GroupReadCache


async def test_member_cache_ttl_and_invalidation() -> None:
    clock = [0.0]
    calls = 0
    cache = GroupReadCache(member_ttl_seconds=300, clock=lambda: clock[0])

    async def loader() -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        return [{"user_id": calls}]

    first = await cache.get_members("100000", loader)
    second = await cache.get_members("100000", loader)
    assert first == second == [{"user_id": 1}]
    assert calls == 1

    clock[0] = 299.0
    await cache.get_members("100000", loader)
    assert calls == 1

    clock[0] = 301.0
    await cache.get_members("100000", loader)
    assert calls == 2

    cache.invalidate_members("100000")
    await cache.get_members("100000", loader)
    assert calls == 3


async def test_member_cache_coalesces_concurrent_misses() -> None:
    cache = GroupReadCache()
    release = asyncio.Event()
    calls = 0

    async def loader() -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        await release.wait()
        return [{"user_id": 10001}]

    first = asyncio.create_task(cache.get_members("100000", loader))
    second = asyncio.create_task(cache.get_members("100000", loader))
    await asyncio.sleep(0)
    assert calls == 1
    release.set()

    assert await first == await second == [{"user_id": 10001}]


async def test_history_cache_is_incremental_and_group_scoped() -> None:
    calls: dict[str, int] = {}
    cache = GroupReadCache()

    async def loader(group_id: str):
        calls[group_id] = calls.get(group_id, 0) + 1
        return [{"message_id": index, "group_id": group_id} for index in range(5)]

    first = await cache.get_history("100000", 5, lambda: loader("100000"))
    assert [item["message_id"] for item in first] == list(range(5))
    cache.observe_message("100000", {"message_id": 5})
    updated = await cache.get_history("100000", 3, lambda: loader("100000"))
    assert [item["message_id"] for item in updated] == [3, 4, 5]
    assert calls == {"100000": 1}

    other = await cache.get_history("200000", 2, lambda: loader("200000"))
    assert [item["group_id"] for item in other] == ["200000", "200000"]
    assert calls == {"100000": 1, "200000": 1}


async def test_history_message_seq_bypasses_cache_and_failures_are_not_cached() -> None:
    calls = 0
    cache = GroupReadCache()

    async def loader() -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary")
        return [{"message_id": calls}]

    with pytest.raises(RuntimeError, match="temporary"):
        await cache.get_history("100000", 1, loader)
    assert await cache.get_history("100000", 1, loader) == [{"message_id": 2}]
    assert calls == 2

    await cache.get_history("100000", 1, loader, message_seq=10)
    assert calls == 3

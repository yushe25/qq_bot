from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

T = TypeVar("T")

MEMBER_TTL_SECONDS = 5 * 60.0
HISTORY_TTL_SECONDS = 60.0
HISTORY_LIMIT = 50
MAX_CACHED_GROUPS = 50


class GroupReadCache:
    def __init__(
        self,
        *,
        member_ttl_seconds: float = MEMBER_TTL_SECONDS,
        history_ttl_seconds: float = HISTORY_TTL_SECONDS,
        history_limit: int = HISTORY_LIMIT,
        max_groups: int = MAX_CACHED_GROUPS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.member_ttl_seconds = max(0.0, float(member_ttl_seconds))
        self.history_ttl_seconds = max(0.0, float(history_ttl_seconds))
        self.history_limit = max(1, int(history_limit))
        self.max_groups = max(1, int(max_groups))
        self._clock = clock
        self._members: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()
        self._history: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()
        self._member_locks: dict[str, asyncio.Lock] = {}
        self._history_locks: dict[str, asyncio.Lock] = {}

    async def get_members(
        self,
        group_id: str,
        loader: Callable[[], Awaitable[list[dict[str, Any]]]],
    ) -> list[dict[str, Any]]:
        cached = self._members.get(group_id)
        if cached is not None and cached[0] > self._clock():
            self._members.move_to_end(group_id)
            return [dict(item) for item in cached[1]]
        lock = self._member_locks.setdefault(group_id, asyncio.Lock())
        async with lock:
            cached = self._members.get(group_id)
            if cached is not None and cached[0] > self._clock():
                self._members.move_to_end(group_id)
                return [dict(item) for item in cached[1]]
            members = await loader()
            value = [dict(item) for item in members]
            self._members[group_id] = (self._clock() + self.member_ttl_seconds, value)
            self._members.move_to_end(group_id)
            self._prune(self._members)
            return [dict(item) for item in value]

    async def get_history(
        self,
        group_id: str,
        limit: int,
        loader: Callable[[], Awaitable[Sequence[dict[str, Any]]]],
        *,
        message_seq: int | None = None,
    ) -> list[dict[str, Any]]:
        requested = max(1, int(limit))
        if message_seq is not None:
            messages = list(await loader())
            return [dict(item) for item in messages[-requested:]]
        cached = self._history.get(group_id)
        if cached is not None and cached[0] > self._clock() and len(cached[1]) >= requested:
            self._history.move_to_end(group_id)
            return [dict(item) for item in cached[1][-requested:]]
        lock = self._history_locks.setdefault(group_id, asyncio.Lock())
        async with lock:
            cached = self._history.get(group_id)
            if cached is not None and cached[0] > self._clock() and len(cached[1]) >= requested:
                self._history.move_to_end(group_id)
                return [dict(item) for item in cached[1][-requested:]]
            messages = list(await loader())
            value = [dict(item) for item in messages[-self.history_limit :]]
            self._history[group_id] = (self._clock() + self.history_ttl_seconds, value)
            self._history.move_to_end(group_id)
            self._prune(self._history)
            return [dict(item) for item in value[-requested:]]

    def observe_message(self, group_id: str, message: dict[str, Any]) -> None:
        cached = self._history.get(group_id)
        if cached is None or cached[0] <= self._clock():
            messages: list[dict[str, Any]] = []
        else:
            messages = cached[1]
        message_id = message.get("message_id") or message.get("message_seq")
        if message_id is not None and any(
            (item.get("message_id") or item.get("message_seq")) == message_id
            for item in messages
        ):
            messages = [item for item in messages if (item.get("message_id") or item.get("message_seq")) != message_id]
        messages.append(dict(message))
        self._history[group_id] = (
            self._clock() + self.history_ttl_seconds,
            messages[-self.history_limit :],
        )
        self._history.move_to_end(group_id)
        self._prune(self._history)

    def invalidate_members(self, group_id: str) -> None:
        self._members.pop(group_id, None)

    def _prune(self, cache: OrderedDict[str, tuple[float, list[dict[str, Any]]]]) -> None:
        now = self._clock()
        for group_id in list(cache):
            if cache[group_id][0] <= now:
                cache.pop(group_id, None)
        while len(cache) > self.max_groups:
            cache.popitem(last=False)

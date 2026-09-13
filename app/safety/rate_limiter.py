from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(
        self,
        max_per_minute: int,
        max_per_hour: int,
        max_per_session_per_hour: int | None = None,
    ) -> None:
        self.max_per_minute = max_per_minute
        self.max_per_hour = max_per_hour
        self.max_per_session_per_hour = (
            max_per_hour if max_per_session_per_hour is None else max_per_session_per_hour
        )
        self._global: deque[float] = deque()
        self._hourly_global: deque[float] = deque()
        self._per_session: dict[str, deque[float]] = defaultdict(deque)
        self._hourly_per_session: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, items: deque[float], window: float, now: float) -> None:
        while items and now - items[0] > window:
            items.popleft()

    def allow(self, session_id: str | None = None, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        self._prune(self._global, 60, now)
        self._prune(self._hourly_global, 3600, now)
        if len(self._global) >= self.max_per_minute:
            return False
        if len(self._hourly_global) >= self.max_per_hour:
            return False
        if session_id:
            bucket = self._per_session[session_id]
            hourly_bucket = self._hourly_per_session[session_id]
            self._prune(bucket, 60, now)
            self._prune(hourly_bucket, 3600, now)
            if len(bucket) >= self.max_per_minute:
                return False
            if len(hourly_bucket) >= self.max_per_session_per_hour:
                return False
        self._global.append(now)
        self._hourly_global.append(now)
        if session_id:
            self._per_session[session_id].append(now)
            self._hourly_per_session[session_id].append(now)
        return True

    def has_capacity(self, session_id: str | None = None, now: float | None = None) -> bool:
        """Check remaining capacity without consuming an allowance."""
        now = time.time() if now is None else now
        self._prune(self._global, 60, now)
        self._prune(self._hourly_global, 3600, now)
        if len(self._global) >= self.max_per_minute:
            return False
        if len(self._hourly_global) >= self.max_per_hour:
            return False
        if session_id:
            bucket = self._per_session[session_id]
            hourly_bucket = self._hourly_per_session[session_id]
            self._prune(bucket, 60, now)
            self._prune(hourly_bucket, 3600, now)
            if len(bucket) >= self.max_per_minute:
                return False
            if len(hourly_bucket) >= self.max_per_session_per_hour:
                return False
        return True

    def next_available_at(self, session_id: str | None = None, now: float | None = None) -> float:
        """Return the earliest timestamp at which a new allowance may be granted."""
        now = time.time() if now is None else now
        available = now
        self._prune(self._global, 60, now)
        self._prune(self._hourly_global, 3600, now)
        if len(self._global) >= self.max_per_minute:
            available = max(available, self._global[0] + 60)
        if len(self._hourly_global) >= self.max_per_hour:
            available = max(available, self._hourly_global[0] + 3600)
        if session_id:
            bucket = self._per_session[session_id]
            hourly_bucket = self._hourly_per_session[session_id]
            self._prune(bucket, 60, now)
            self._prune(hourly_bucket, 3600, now)
            if len(bucket) >= self.max_per_minute:
                available = max(available, bucket[0] + 60)
            if len(hourly_bucket) >= self.max_per_session_per_hour:
                available = max(available, hourly_bucket[0] + 3600)
        return available

    def reset(self) -> None:
        self._global.clear()
        self._hourly_global.clear()
        self._per_session.clear()
        self._hourly_per_session.clear()

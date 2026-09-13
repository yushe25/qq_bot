from __future__ import annotations

import hashlib
import time

from app.domain.types import NormalizedEvent


def message_dedupe_key(event: NormalizedEvent) -> str:
    if event.event_id:
        return f"id:{event.event_id}"
    raw = f"{event.session_id}|{event.sender_id}|{event.text}|{event.timestamp}"
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:20]
    return f"fallback:{digest}"


class MessageDedupe:
    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 10_000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._seen: dict[str, float] = {}

    def add(self, event: NormalizedEvent) -> bool:
        now = time.time()
        self._prune(now)
        key = self._key(event)
        if key in self._seen:
            return False
        self._seen[key] = now
        return True

    def _key(self, event: NormalizedEvent) -> str:
        return message_dedupe_key(event)

    def _prune(self, now: float) -> None:
        if len(self._seen) <= self.max_entries:
            expired = [key for key, ts in self._seen.items() if now - ts > self.ttl_seconds]
            for key in expired:
                self._seen.pop(key, None)
            return
        cutoff = now - self.ttl_seconds
        kept = {key: ts for key, ts in self._seen.items() if ts > cutoff}
        self._seen = dict(list(kept.items())[-self.max_entries :])

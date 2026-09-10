from __future__ import annotations

from typing import Any

from app.domain.types import now_ms
from app.storage.db import StateStore

LEARNING_KINDS = ("speech_style", "group_norm", "topic_trend")
MEMORY_KINDS = ("active_topic", "pending_thought", "impression")
KIND_LABELS = {
    "speech_style": "说话习惯",
    "group_norm": "群内习惯",
    "topic_trend": "话题偏好",
}
KIND_ORDER = {kind: index for index, kind in enumerate(LEARNING_KINDS)}
MEMORY_LABELS = {
    "active_topic": "当前话题",
    "pending_thought": "未完成念头",
    "impression": "成员印象",
}
MEMORY_IMPORTANCE = {
    "active_topic": 3,
    "pending_thought": 2,
    "impression": 1,
}
MEMORY_TTL_DAYS = {
    "active_topic": 7.0,
    "pending_thought": 14.0,
    "impression": 30.0,
}
MEMORY_DECAY_PER_DAY = 0.75
MAX_SNAPSHOT_ITEMS = 12
MAX_SNAPSHOT_VALUE_CHARS = 120
MAX_MEMORY_KEY_CHARS = 80
MAX_MEMORY_VALUE_CHARS = 300


class MemoryStore:
    def __init__(self, state_store: StateStore) -> None:
        self.state_store = state_store

    async def update(self, session_id: str, kind: str, key: str, value: str) -> None:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"不支持的记忆 kind: {kind}")
        key = key.strip()
        value = value.strip()
        if not key or len(key) > MAX_MEMORY_KEY_CHARS:
            raise ValueError(f"记忆 key 必须为 1 到 {MAX_MEMORY_KEY_CHARS} 字符")
        if not value or len(value) > MAX_MEMORY_VALUE_CHARS:
            raise ValueError(f"记忆 value 必须为 1 到 {MAX_MEMORY_VALUE_CHARS} 字符")
        await self.state_store.upsert_memory(
            session_id=session_id,
            kind=kind,
            key=key,
            value=value,
            importance=MEMORY_IMPORTANCE[kind],
            expires_at=now_ms() + MEMORY_TTL_DAYS[kind] * 24 * 3600 * 1000,
        )

    async def snapshot(self, session_id: str) -> str:
        items = await self.state_store.load_memory(session_id)
        current_ms = now_ms()
        visible: list[tuple[float, dict[str, Any]]] = []
        for item in items:
            kind = str(item["kind"])
            if kind not in MEMORY_KINDS:
                continue
            age_days = max(0.0, (current_ms - float(item["updated_at"])) / (24 * 3600 * 1000))
            score = float(item["importance"]) - MEMORY_DECAY_PER_DAY * age_days
            visible.append((score, item))
        visible.sort(
            key=lambda pair: (
                -pair[0],
                -float(pair[1]["updated_at"]),
                str(pair[1]["kind"]),
                str(pair[1]["key"]),
            )
        )

        groups: dict[str, list[tuple[float, dict[str, Any]]]] = {}
        for score, item in visible[:MAX_SNAPSHOT_ITEMS]:
            groups.setdefault(str(item["kind"]), []).append((score, item))

        lines = []
        ordered_kinds = sorted(
            groups,
            key=lambda kind: (-max(score for score, _ in groups[kind]), MEMORY_KINDS.index(kind)),
        )
        for kind in ordered_kinds:
            values = [_snapshot_value(str(item["value"])) for _, item in groups[kind]]
            lines.append(f"{MEMORY_LABELS[kind]}：" + "；".join(values))
        return "\n".join(lines) or "（暂无记忆）"

    async def learning_notes_text(
        self,
        session_id: str,
        limit: int = 20,
        max_value_chars: int = 160,
    ) -> str:
        items = await self.state_store.load_memory(session_id)
        learned = [item for item in items if item["kind"] in LEARNING_KINDS]
        learned.sort(key=lambda item: (KIND_ORDER.get(item["kind"], 99), -float(item["updated_at"])))
        lines: list[str] = []
        for item in learned[:limit]:
            label = KIND_LABELS.get(str(item["kind"]), str(item["kind"]))
            key = str(item["key"]).replace("\n", " ")[:40]
            value = str(item["value"]).replace("\n", " ")[:max_value_chars]
            lines.append(f"- {label}「{key}」：{value}")
        return "\n".join(lines)

    async def commit_learning_note(
        self,
        session_id: str,
        kind: str,
        key: str,
        value: str,
        confidence: float,
        source_members: list[str],
        evidence: list[dict[str, Any]],
        ttl_days: float,
    ) -> None:
        expires_at = now_ms() + ttl_days * 24 * 3600 * 1000
        await self.state_store.upsert_learning_note(
            session_id=session_id,
            kind=kind,
            key=key,
            value=value,
            confidence=confidence,
            source_members=source_members,
            evidence=evidence,
            expires_at=expires_at,
        )
        await self.state_store.upsert_memory(
            session_id=session_id,
            kind=kind,
            key=key,
            value=value,
            importance=2,
            expires_at=expires_at,
        )

    async def prune_learning_notes(self, session_id: str, max_notes: int) -> None:
        await self.state_store.prune_learning_notes(session_id, max_notes)
        await self.state_store.prune_learning_memory_items(session_id, max_notes, list(LEARNING_KINDS))

    async def delete_learning_note(self, session_id: str, kind: str, key: str) -> None:
        await self.state_store.delete_learning_note(session_id, kind, key)
        await self.state_store.delete_memory_item(session_id, kind, key)

    async def delete_member_data(self, session_id: str, member_id: str) -> None:
        await self.state_store.delete_observations_by_member(session_id, member_id)
        await self.state_store.delete_learning_notes_by_member(session_id, member_id)

    async def clear_learning(self, session_id: str) -> None:
        await self.state_store.delete_observations(session_id)
        await self.state_store.delete_learning_notes(session_id)
        await self.state_store.prune_learning_memory_items(session_id, 0, list(LEARNING_KINDS))


def _snapshot_value(value: str) -> str:
    return " ".join(value.replace("\n", " ").split())[:MAX_SNAPSHOT_VALUE_CHARS]

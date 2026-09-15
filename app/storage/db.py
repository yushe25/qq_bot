from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.domain.types import Session, WakeInput, now_ms, wake_input_from_json, wake_input_to_json
from app.storage.migrations import Migration, run_migrations

logger = logging.getLogger("qqf.storage")
DEFAULT_WAL_WARN_BYTES = 64 * 1024 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    scene_type TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    state TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    next_wake_at REAL NOT NULL DEFAULT 0,
    cooldown_until REAL NOT NULL DEFAULT 0,
    last_wake_at REAL,
    last_message_at REAL NOT NULL DEFAULT 0,
    wake_count INTEGER NOT NULL DEFAULT 0,
    conversation TEXT NOT NULL DEFAULT '[]',
    pending_output TEXT NOT NULL DEFAULT '[]',
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_items (
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    importance INTEGER NOT NULL DEFAULT 1,
    expires_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (session_id, kind, key)
);

CREATE INDEX IF NOT EXISTS idx_memory_lookup ON memory_items(session_id, kind);

CREATE TABLE IF NOT EXISTS wake_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    category TEXT NOT NULL,
    reason TEXT NOT NULL,
    decided TEXT NOT NULL,
    probability REAL,
    next_wake_at REAL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wake_log_session ON wake_log(session_id, id);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    result TEXT NOT NULL,
    session_id TEXT,
    actor_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sticker_usage_counts (
    id TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_send_counts (
    session_id TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0,
    day TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    text TEXT NOT NULL,
    kind TEXT NOT NULL,
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_observations_session_ts ON observations(session_id, ts);

CREATE TABLE IF NOT EXISTS learning_state (
    session_id TEXT PRIMARY KEY,
    last_attempt_at REAL NOT NULL DEFAULT 0,
    last_learning_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_notes (
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_members TEXT NOT NULL DEFAULT '[]',
    evidence TEXT NOT NULL DEFAULT '[]',
    expires_at REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (session_id, kind, key)
);

CREATE INDEX IF NOT EXISTS idx_learning_notes_session ON learning_notes(session_id, expires_at);

CREATE TABLE IF NOT EXISTS pending_wakes (
    session_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    reason TEXT NOT NULL,
    events_json TEXT NOT NULL DEFAULT '[]',
    probability REAL,
    deferred INTEGER NOT NULL DEFAULT 0,
    defer_until_active INTEGER NOT NULL DEFAULT 0,
    phase_ready INTEGER NOT NULL DEFAULT 0,
    next_wake_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pending_wakes_due
    ON pending_wakes(next_wake_at, session_id);

CREATE TABLE IF NOT EXISTS event_dedupe (
    key TEXT PRIMARY KEY,
    seen_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_event_dedupe_expiry
    ON event_dedupe(expires_at);
"""

_STICKER_INGEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbound_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    width INTEGER,
    height INTEGER,
    local_path TEXT NOT NULL,
    status TEXT NOT NULL,
    vision_provider TEXT,
    tag_confidence REAL,
    auto_approved INTEGER NOT NULL DEFAULT 0,
    first_seen_at REAL NOT NULL,
    reviewed_at REAL,
    reviewed_by TEXT,
    reject_reason TEXT
);

CREATE TABLE IF NOT EXISTS inbound_media_groups (
    media_id INTEGER NOT NULL,
    group_id TEXT NOT NULL,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (media_id, group_id)
);

CREATE TABLE IF NOT EXISTS sticker_context_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL,
    group_id TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    observed_at REAL NOT NULL,
    before_context_json TEXT NOT NULL,
    after_context_json TEXT NOT NULL DEFAULT '[]',
    situation_tags_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL,
    UNIQUE (source_message_id, media_id)
);

CREATE TABLE IF NOT EXISTS sticker_preferences (
    media_id INTEGER NOT NULL,
    group_id TEXT NOT NULL,
    situation_tag TEXT NOT NULL,
    positive_score REAL NOT NULL DEFAULT 0,
    negative_score REAL NOT NULL DEFAULT 0,
    usage_count INTEGER NOT NULL DEFAULT 0,
    last_updated_at REAL NOT NULL,
    PRIMARY KEY (media_id, group_id, situation_tag)
);

CREATE INDEX IF NOT EXISTS idx_inbound_media_status ON inbound_media(status, first_seen_at);
CREATE INDEX IF NOT EXISTS idx_media_groups_group ON inbound_media_groups(group_id, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_context_group_time ON sticker_context_observations(group_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_preferences_group_score ON sticker_preferences(group_id, positive_score);
"""

_STICKER_INGEST_METADATA_SCHEMA = """
ALTER TABLE inbound_media ADD COLUMN name TEXT NOT NULL DEFAULT '';
ALTER TABLE inbound_media ADD COLUMN description TEXT NOT NULL DEFAULT '';
ALTER TABLE inbound_media ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE inbound_media ADD COLUMN moods_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE inbound_media ADD COLUMN scenes_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE inbound_media ADD COLUMN avoid_scenes_json TEXT NOT NULL DEFAULT '[]';
"""

_UPSERT_SESSION_SQL = """
INSERT INTO sessions (
    id, scene_type, scene_id, state, persona_id,
    next_wake_at, cooldown_until, last_wake_at,
    last_message_at, wake_count, conversation, pending_output, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    scene_type = excluded.scene_type,
    scene_id = excluded.scene_id,
    state = excluded.state,
    persona_id = excluded.persona_id,
    next_wake_at = excluded.next_wake_at,
    cooldown_until = excluded.cooldown_until,
    last_wake_at = excluded.last_wake_at,
    last_message_at = excluded.last_message_at,
    wake_count = excluded.wake_count,
    conversation = excluded.conversation,
    pending_output = excluded.pending_output,
    updated_at = excluded.updated_at
"""


def _session_params(session: Session) -> tuple[Any, ...]:
    data = session.to_dict()
    return (
        data["id"],
        data["scene_type"],
        data["scene_id"],
        data["state"],
        data["persona_id"],
        data["next_wake_at"],
        data["cooldown_until"],
        data["last_wake_at"],
        data["last_message_at"],
        data["wake_count"],
        _json(data["conversation"]),
        _json(data["pending_output"]),
        now_ms(),
    )


class StateStore:
    """Async facade over SQLite using an event-loop lock plus worker-thread execution."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        wal_warn_bytes: int = DEFAULT_WAL_WARN_BYTES,
    ) -> None:
        self._path = Path(database_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self._wal_warn_bytes = max(0, int(wal_warn_bytes))

    async def init(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._open_locked)

    async def close(self) -> None:
        async with self._lock:
            conn = self._conn
            self._conn = None
            if conn is not None:
                await asyncio.to_thread(_close_connection_sync, conn)

    async def upsert_session(self, session: Session) -> None:
        await self._execute(_UPSERT_SESSION_SQL, _session_params(session))

    async def load_sessions(self) -> list[Session]:
        rows = await self._query("SELECT * FROM sessions ORDER BY id", ())
        sessions: list[Session] = []
        for row in rows:
            for column in ("conversation", "pending_output"):
                raw = row.get(column)
                if isinstance(raw, str):
                    try:
                        row[column] = json.loads(raw)
                    except json.JSONDecodeError:
                        row[column] = []
            sessions.append(Session.from_dict(row))
        return sessions

    async def save_deferred_wake(self, session: Session, wake: WakeInput) -> None:
        async with self._lock:
            conn = self._connection()
            await asyncio.to_thread(_save_deferred_wake_sync, conn, session, wake)

    async def load_pending_wakes(self) -> list[tuple[str, float, WakeInput]]:
        rows = await self._query(
            """
            SELECT p.session_id, p.events_json, p.next_wake_at, s.id AS session_exists
            FROM pending_wakes p
            LEFT JOIN sessions s ON s.id = p.session_id
            ORDER BY p.next_wake_at ASC
            """,
            (),
        )
        pending: list[tuple[str, float, WakeInput]] = []
        rejected: list[tuple[str, str]] = []
        for row in rows:
            session_id = str(row["session_id"])
            if row.get("session_exists") is None:
                rejected.append((session_id, "orphan session"))
                continue
            wake = wake_input_from_json(session_id, str(row["events_json"]))
            if wake is None:
                rejected.append((session_id, "invalid events_json"))
                continue
            pending.append((session_id, float(row["next_wake_at"]), wake))
        for session_id, reason in rejected:
            await self.delete_pending_wake(session_id)
            await self.append_audit(
                action="pending_wake_rejected",
                detail=reason,
                result="deleted",
                session_id=session_id,
                actor_type="scheduler",
            )
        return pending

    async def delete_pending_wake(self, session_id: str) -> None:
        await self._execute("DELETE FROM pending_wakes WHERE session_id = ?", (session_id,))

    async def increment_pending_wake_attempts(self, session_id: str) -> int:
        async with self._lock:
            conn = self._connection()
            return await asyncio.to_thread(_increment_pending_wake_attempts_sync, conn, session_id)

    async def claim_event(self, key: str, now_s: float, ttl_seconds: int) -> bool:
        async with self._lock:
            conn = self._connection()
            return await asyncio.to_thread(_claim_event_sync, conn, key, now_s, ttl_seconds)

    async def prune_event_dedupe(self, now_s: float, max_entries: int) -> int:
        async with self._lock:
            conn = self._connection()
            return await asyncio.to_thread(_prune_event_dedupe_sync, conn, now_s, max_entries)

    async def upsert_memory(
        self,
        session_id: str,
        kind: str,
        key: str,
        value: str,
        importance: int = 1,
        expires_at: float = 0.0,
    ) -> None:
        await self._execute(
            """
            INSERT INTO memory_items (
                session_id, kind, key, value, importance, expires_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, kind, key) DO UPDATE SET
                value = excluded.value,
                importance = excluded.importance,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            (session_id, kind, key, value, importance, expires_at, now_ms()),
        )

    async def load_memory(self, session_id: str) -> list[dict[str, Any]]:
        return await self._query(
            """
            SELECT session_id, kind, key, value, importance, expires_at, updated_at
            FROM memory_items
            WHERE session_id = ? AND (expires_at = 0 OR expires_at > ?)
            ORDER BY importance DESC, updated_at DESC
            """,
            (session_id, now_ms()),
        )

    async def delete_memory(self, session_id: str) -> None:
        await self._execute("DELETE FROM memory_items WHERE session_id = ?", (session_id,))

    async def delete_memory_item(self, session_id: str, kind: str, key: str) -> None:
        await self._execute(
            "DELETE FROM memory_items WHERE session_id = ? AND kind = ? AND key = ?",
            (session_id, kind, key),
        )

    async def append_observation(
        self,
        observation_id: str,
        session_id: str,
        sender_id: str,
        text: str,
        kind: str,
        ts: float,
        max_per_session: int = 250,
    ) -> None:
        safe_id = f"{session_id}|{observation_id}"
        await self._execute(
            """
            INSERT OR IGNORE INTO observations (id, session_id, sender_id, text, kind, ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (safe_id, session_id, sender_id, text, kind, ts),
        )
        if max_per_session > 0:
            await self._execute(
                """
                DELETE FROM observations
                WHERE session_id = ? AND id NOT IN (
                    SELECT id FROM observations
                    WHERE session_id = ?
                    ORDER BY ts DESC
                    LIMIT ?
                )
                """,
                (session_id, session_id, max_per_session),
            )

    async def list_observations(
        self,
        session_id: str,
        after_ts: float = 0.0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await self._query(
            """
            SELECT id, session_id, sender_id, text, kind, ts
            FROM observations
            WHERE session_id = ? AND ts > ?
            ORDER BY ts ASC
            LIMIT ?
            """,
            (session_id, after_ts, limit),
        )

    async def count_observations(self, session_id: str, after_ts: float = 0.0) -> int:
        rows = await self._query(
            "SELECT COUNT(*) AS total FROM observations WHERE session_id = ? AND ts > ?",
            (session_id, after_ts),
        )
        return int(rows[0]["total"])

    async def delete_observations(self, session_id: str) -> None:
        await self._execute("DELETE FROM observations WHERE session_id = ?", (session_id,))

    async def delete_observations_by_member(self, session_id: str, sender_id: str) -> None:
        await self._execute(
            "DELETE FROM observations WHERE session_id = ? AND sender_id = ?",
            (session_id, sender_id),
        )

    async def get_learning_state(self, session_id: str) -> dict[str, Any]:
        rows = await self._query(
            "SELECT session_id, last_attempt_at, last_learning_at, updated_at "
            "FROM learning_state WHERE session_id = ?",
            (session_id,),
        )
        if rows:
            return rows[0]
        return {
            "session_id": session_id,
            "last_attempt_at": 0.0,
            "last_learning_at": 0.0,
            "updated_at": 0.0,
        }

    async def update_learning_state(
        self,
        session_id: str,
        last_attempt_at: float | None = None,
        last_learning_at: float | None = None,
    ) -> None:
        current = await self.get_learning_state(session_id)
        attempt = current["last_attempt_at"] if last_attempt_at is None else last_attempt_at
        learned = current["last_learning_at"] if last_learning_at is None else last_learning_at
        await self._execute(
            """
            INSERT INTO learning_state (session_id, last_attempt_at, last_learning_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                last_attempt_at = excluded.last_attempt_at,
                last_learning_at = excluded.last_learning_at,
                updated_at = excluded.updated_at
            """,
            (session_id, attempt, learned, now_ms()),
        )

    async def upsert_learning_note(
        self,
        session_id: str,
        kind: str,
        key: str,
        value: str,
        confidence: float,
        source_members: list[str],
        evidence: list[dict[str, Any]],
        expires_at: float,
    ) -> None:
        await self._execute(
            """
            INSERT INTO learning_notes (
                session_id, kind, key, value, confidence,
                source_members, evidence, expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, kind, key) DO UPDATE SET
                value = excluded.value,
                confidence = excluded.confidence,
                source_members = excluded.source_members,
                evidence = excluded.evidence,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            (
                session_id,
                kind,
                key,
                value,
                confidence,
                _json(source_members),
                _json(evidence),
                expires_at,
                now_ms(),
                now_ms(),
            ),
        )

    async def list_learning_notes(self, session_id: str) -> list[dict[str, Any]]:
        rows = await self._query(
            """
            SELECT session_id, kind, key, value, confidence,
                   source_members, evidence, expires_at, created_at, updated_at
            FROM learning_notes
            WHERE session_id = ?
            ORDER BY created_at DESC
            """,
            (session_id,),
        )
        for row in rows:
            row["source_members"] = _loads_list(row["source_members"])
            row["evidence"] = _loads_list(row["evidence"])
        return rows

    async def delete_learning_note(self, session_id: str, kind: str, key: str) -> None:
        await self._execute(
            "DELETE FROM learning_notes WHERE session_id = ? AND kind = ? AND key = ?",
            (session_id, kind, key),
        )

    async def delete_learning_notes(self, session_id: str) -> None:
        await self._execute("DELETE FROM learning_notes WHERE session_id = ?", (session_id,))

    async def delete_learning_notes_by_member(self, session_id: str, member_id: str) -> None:
        rows = await self.list_learning_notes(session_id)
        for row in rows:
            if member_id in row["source_members"]:
                kind = str(row["kind"])
                key = str(row["key"])
                await self.delete_learning_note(session_id, kind, key)
                await self.delete_memory_item(session_id, kind, key)

    async def prune_learning_notes(self, session_id: str, max_notes: int) -> None:
        if max_notes <= 0:
            await self._execute("DELETE FROM learning_notes WHERE session_id = ?", (session_id,))
            return
        await self._execute(
            """
            DELETE FROM learning_notes
            WHERE session_id = ? AND rowid NOT IN (
                SELECT rowid FROM learning_notes
                WHERE session_id = ?
                ORDER BY updated_at DESC, rowid DESC
                LIMIT ?
            )
            """,
            (session_id, session_id, max_notes),
        )

    async def prune_learning_memory_items(self, session_id: str, max_notes: int, kinds: Sequence[str]) -> None:
        if not kinds:
            return
        if max_notes <= 0:
            placeholders = ", ".join("?" for _ in kinds)
            await self._execute(
                f"DELETE FROM memory_items WHERE session_id = ? AND kind IN ({placeholders})",
                (session_id, *kinds),
            )
            return
        for kind in kinds:
            await self._execute(
                """
                DELETE FROM memory_items
                WHERE session_id = ? AND kind = ? AND rowid NOT IN (
                    SELECT rowid FROM memory_items
                    WHERE session_id = ? AND kind = ?
                    ORDER BY updated_at DESC, rowid DESC
                    LIMIT ?
                )
                """,
                (session_id, kind, session_id, kind, max_notes),
            )

    async def delete_expired_learning(self, session_id: str, cutoff: float | None = None) -> None:
        expired_at = now_ms() if cutoff is None else cutoff
        await self._execute(
            "DELETE FROM learning_notes WHERE session_id = ? AND expires_at > 0 AND expires_at <= ?",
            (session_id, expired_at),
        )
        kinds = ("speech_style", "group_norm", "topic_trend")
        placeholders = ", ".join("?" for _ in kinds)
        await self._execute(
            f"""
            DELETE FROM memory_items
            WHERE session_id = ? AND kind IN ({placeholders}) AND expires_at > 0 AND expires_at <= ?
            """,
            (session_id, *kinds, expired_at),
        )

    async def append_audit(
        self,
        action: str,
        detail: str,
        result: str,
        session_id: str | None = None,
        actor_type: str = "system",
    ) -> None:
        await self._execute(
            """
            INSERT INTO audit_logs (timestamp, action, detail, result, session_id, actor_type)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (now_ms(), action, detail, result, session_id, actor_type),
        )

    async def list_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._query(
            "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    async def append_wake_log(
        self,
        session_id: str,
        category: str,
        reason: str,
        decided: str,
        probability: float | None = None,
        next_wake_at: float | None = None,
    ) -> None:
        await self._execute(
            """
            INSERT INTO wake_log (
                session_id, category, reason, decided, probability, next_wake_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, category, reason, decided, probability, next_wake_at, now_ms()),
        )

    async def update_latest_wake_log_usage(
        self,
        session_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> None:
        await self._execute(
            """
            UPDATE wake_log
            SET prompt_tokens = ?, completion_tokens = ?, total_tokens = ?
            WHERE id = (
                SELECT id FROM wake_log
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT 1
            )
            """,
            (
                max(0, int(prompt_tokens)),
                max(0, int(completion_tokens)),
                max(0, int(total_tokens)),
                session_id,
            ),
        )

    async def list_wake_logs(
        self,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if session_id:
            return await self._query(
                "SELECT * FROM wake_log WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
        return await self._query("SELECT * FROM wake_log ORDER BY id DESC LIMIT ?", (limit,))

    async def get_sticker_usage(self, sticker_id: str) -> int:
        rows = await self._query(
            "SELECT count FROM sticker_usage_counts WHERE id = ?",
            (sticker_id,),
        )
        return int(rows[0]["count"]) if rows else 0

    async def increment_sticker_usage(self, sticker_id: str, delta: int = 1) -> None:
        await self._execute(
            """
            INSERT INTO sticker_usage_counts (id, count, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                count = count + ?,
                updated_at = excluded.updated_at
            """,
            (sticker_id, delta, now_ms(), delta),
        )

    async def get_inbound_media_by_hash(self, sha256: str) -> dict[str, Any] | None:
        rows = await self._query("SELECT * FROM inbound_media WHERE sha256 = ?", (sha256,))
        return rows[0] if rows else None

    async def get_inbound_media(self, media_id: int) -> dict[str, Any] | None:
        rows = await self._query("SELECT * FROM inbound_media WHERE id = ?", (media_id,))
        return rows[0] if rows else None

    async def update_inbound_media_status(
        self,
        media_id: int,
        status: str,
        *,
        local_path: str | None = None,
        vision_provider: str | None = None,
        tag_confidence: float | None = None,
        auto_approved: bool | None = None,
        reviewed_by: str | None = None,
        reject_reason: str | None = None,
    ) -> None:
        await self._execute(
            """
            UPDATE inbound_media SET
                status = ?,
                local_path = COALESCE(?, local_path),
                vision_provider = COALESCE(?, vision_provider),
                tag_confidence = COALESCE(?, tag_confidence),
                auto_approved = COALESCE(?, auto_approved),
                reviewed_at = ?,
                reviewed_by = COALESCE(?, reviewed_by),
                reject_reason = COALESCE(?, reject_reason)
            WHERE id = ?
            """,
            (
                status,
                local_path,
                vision_provider,
                tag_confidence,
                int(auto_approved) if auto_approved is not None else None,
                now_ms(),
                reviewed_by,
                reject_reason,
                media_id,
            ),
        )

    async def update_inbound_media_metadata(
        self,
        media_id: int,
        *,
        name: str = "",
        description: str = "",
        tags: Sequence[str] = (),
        moods: Sequence[str] = (),
        scenes: Sequence[str] = (),
        avoid_scenes: Sequence[str] = (),
    ) -> None:
        await self._execute(
            """
            UPDATE inbound_media SET
                name = ?, description = ?, tags_json = ?, moods_json = ?,
                scenes_json = ?, avoid_scenes_json = ?
            WHERE id = ?
            """,
            (
                name,
                description,
                _json(list(tags)),
                _json(list(moods)),
                _json(list(scenes)),
                _json(list(avoid_scenes)),
                media_id,
            ),
        )

    async def list_inbound_media_for_review(self, group_id: str, status: str = "pending_review") -> list[dict[str, Any]]:
        return await self._query(
            """
            SELECT m.*, g.group_id, g.first_seen_at AS group_first_seen_at,
                   g.last_seen_at AS group_last_seen_at, g.seen_count
            FROM inbound_media m
            JOIN inbound_media_groups g ON g.media_id = m.id
            WHERE m.status = ? AND g.group_id = ?
            ORDER BY m.first_seen_at ASC
            """,
            (status, group_id),
        )

    async def get_inbound_media_groups(self, media_id: int) -> list[dict[str, Any]]:
        return await self._query(
            "SELECT * FROM inbound_media_groups WHERE media_id = ? ORDER BY last_seen_at DESC",
            (media_id,),
        )

    async def list_sticker_context_for_media(self, media_id: int) -> list[dict[str, Any]]:
        return await self._query(
            "SELECT * FROM sticker_context_observations WHERE media_id = ? ORDER BY observed_at DESC LIMIT 50",
            (media_id,),
        )

    async def list_rejected_inbound_media(self, older_than: float) -> list[dict[str, Any]]:
        return await self._query(
            "SELECT * FROM inbound_media WHERE status = 'rejected' AND first_seen_at < ?",
            (older_than,),
        )

    async def delete_inbound_media(self, media_id: int) -> None:
        async with self._lock:
            conn = self._connection()
            await asyncio.to_thread(_delete_inbound_media_sync, conn, media_id)

    async def upsert_inbound_media(
        self,
        *,
        sha256: str,
        mime_type: str,
        byte_size: int,
        width: int | None,
        height: int | None,
        local_path: str,
        status: str,
        vision_provider: str | None = None,
        tag_confidence: float | None = None,
        auto_approved: bool = False,
        first_seen_at: float | None = None,
    ) -> int:
        async with self._lock:
            conn = self._connection()
            return await asyncio.to_thread(
                _upsert_inbound_media_sync,
                conn,
                sha256,
                mime_type,
                byte_size,
                width,
                height,
                local_path,
                status,
                vision_provider,
                tag_confidence,
                auto_approved,
                first_seen_at if first_seen_at is not None else now_ms(),
            )

    async def touch_inbound_media_group(self, media_id: int, group_id: str, seen_at: float | None = None) -> None:
        timestamp = seen_at if seen_at is not None else now_ms()
        await self._execute(
            """
            INSERT INTO inbound_media_groups (
                media_id, group_id, first_seen_at, last_seen_at, seen_count
            ) VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(media_id, group_id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                seen_count = seen_count + 1
            """,
            (media_id, group_id, timestamp, timestamp),
        )

    async def inbound_media_group_stats(self, group_id: str) -> dict[str, Any]:
        rows = await self._query(
            """
            SELECT COUNT(*) AS item_count, COALESCE(SUM(m.byte_size), 0) AS byte_size
            FROM inbound_media_groups g
            JOIN inbound_media m ON m.id = g.media_id
            WHERE g.group_id = ?
            """,
            (group_id,),
        )
        row = rows[0] if rows else {"item_count": 0, "byte_size": 0}
        return {"item_count": int(row["item_count"]), "byte_size": int(row["byte_size"])}

    async def insert_sticker_context_observation(
        self,
        *,
        media_id: int,
        group_id: str,
        source_message_id: str,
        sender_id: str,
        observed_at: float,
        before_context: list[dict[str, Any]],
        source: str,
    ) -> int:
        async with self._lock:
            conn = self._connection()
            return await asyncio.to_thread(
                _insert_sticker_context_sync,
                conn,
                media_id,
                group_id,
                source_message_id,
                sender_id,
                observed_at,
                _json(before_context),
                source,
            )

    async def update_sticker_context_after(
        self,
        observation_id: int,
        after_context: list[dict[str, Any]],
        situation_tags: list[str],
    ) -> None:
        await self._execute(
            """
            UPDATE sticker_context_observations
            SET after_context_json = ?, situation_tags_json = ?
            WHERE id = ?
            """,
            (_json(after_context), _json(situation_tags), observation_id),
        )

    async def list_sticker_context_observations(
        self,
        group_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if group_id:
            return await self._query(
                "SELECT * FROM sticker_context_observations WHERE group_id = ? ORDER BY observed_at DESC LIMIT ?",
                (group_id, limit),
            )
        return await self._query(
            "SELECT * FROM sticker_context_observations ORDER BY observed_at DESC LIMIT ?",
            (limit,),
        )

    async def increment_sticker_preference(
        self,
        media_id: int,
        group_id: str,
        situation_tag: str,
        *,
        positive_delta: float = 0.0,
        negative_delta: float = 0.0,
        usage_delta: int = 0,
    ) -> None:
        await self._execute(
            """
            INSERT INTO sticker_preferences (
                media_id, group_id, situation_tag, positive_score, negative_score,
                usage_count, last_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(media_id, group_id, situation_tag) DO UPDATE SET
                positive_score = positive_score + ?,
                negative_score = negative_score + ?,
                usage_count = usage_count + ?,
                last_updated_at = excluded.last_updated_at
            """,
            (
                media_id,
                group_id,
                situation_tag,
                positive_delta,
                negative_delta,
                usage_delta,
                now_ms(),
                positive_delta,
                negative_delta,
                usage_delta,
            ),
        )

    async def list_sticker_preferences(
        self,
        group_id: str,
        media_ids: Sequence[int] | None = None,
    ) -> list[dict[str, Any]]:
        if media_ids:
            placeholders = ",".join("?" for _ in media_ids)
            return await self._query(
                f"SELECT * FROM sticker_preferences WHERE group_id = ? AND media_id IN ({placeholders})",
                (group_id, *media_ids),
            )
        return await self._query(
            "SELECT * FROM sticker_preferences WHERE group_id = ? ORDER BY positive_score DESC, negative_score ASC",
            (group_id,),
        )

    async def get_daily_send_count(self, session_id: str, day: str) -> int:
        rows = await self._query(
            "SELECT count FROM daily_send_counts WHERE session_id = ? AND day = ?",
            (session_id, day),
        )
        return int(rows[0]["count"]) if rows else 0

    async def increment_daily_send_count(self, session_id: str, day: str) -> None:
        await self._execute(
            """
            INSERT INTO daily_send_counts (session_id, count, day, updated_at)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                count = CASE WHEN day = excluded.day THEN count + 1 ELSE 1 END,
                day = excluded.day,
                updated_at = excluded.updated_at
            """,
            (session_id, day, now_ms()),
        )

    async def reset_daily_send_count(self, session_id: str) -> None:
        await self._execute("DELETE FROM daily_send_counts WHERE session_id = ?", (session_id,))

    def _open_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        run_migrations(
            self._path,
            _MIGRATIONS,
            backup_dir=self._path.parent / "backups",
        )
        conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        self._conn = conn
        wal_size = _wal_size_bytes(self._path)
        if self._wal_warn_bytes > 0 and wal_size >= self._wal_warn_bytes:
            logger.warning(
                "SQLite WAL size is %s bytes (threshold %s)",
                wal_size,
                self._wal_warn_bytes,
            )

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("StateStore is not initialized; call init() first")
        return self._conn

    async def _execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        async with self._lock:
            conn = self._connection()
            await asyncio.to_thread(_execute_sync, conn, sql, params)

    async def _query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        async with self._lock:
            conn = self._connection()
            return await asyncio.to_thread(_query_sync, conn, sql, params)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return []


def _execute_sync(conn: sqlite3.Connection, sql: str, params: Sequence[Any]) -> None:
    cursor = conn.execute(sql, params)
    cursor.close()


def _query_sync(conn: sqlite3.Connection, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, params)
    rows = [dict(row) for row in cursor.fetchall()]
    cursor.close()
    return rows


def _upsert_session_sync(conn: sqlite3.Connection, session: Session) -> None:
    cursor = conn.execute(_UPSERT_SESSION_SQL, _session_params(session))
    cursor.close()


def _save_deferred_wake_sync(conn: sqlite3.Connection, session: Session, wake: WakeInput) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        _upsert_session_sync(conn, session)
        cursor = conn.execute(
            """
            INSERT INTO pending_wakes (
                session_id, category, reason, events_json, probability,
                deferred, defer_until_active, phase_ready,
                next_wake_at, attempts, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                category = excluded.category,
                reason = excluded.reason,
                events_json = excluded.events_json,
                probability = excluded.probability,
                deferred = excluded.deferred,
                defer_until_active = excluded.defer_until_active,
                phase_ready = excluded.phase_ready,
                next_wake_at = excluded.next_wake_at,
                updated_at = excluded.updated_at
            """,
            (
                session.id,
                wake.category.value,
                wake.reason,
                wake_input_to_json(wake),
                wake.probability,
                int(wake.deferred),
                int(wake.defer_until_active),
                int(wake.phase_ready),
                session.next_wake_at,
                now_ms(),
            ),
        )
        cursor.close()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _increment_pending_wake_attempts_sync(conn: sqlite3.Connection, session_id: str) -> int:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE pending_wakes SET attempts = attempts + 1, updated_at = ? WHERE session_id = ?",
            (now_ms(), session_id),
        )
        row = conn.execute(
            "SELECT attempts FROM pending_wakes WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        conn.execute("COMMIT")
        return int(row["attempts"]) if row is not None else 0
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _upsert_inbound_media_sync(
    conn: sqlite3.Connection,
    sha256: str,
    mime_type: str,
    byte_size: int,
    width: int | None,
    height: int | None,
    local_path: str,
    status: str,
    vision_provider: str | None,
    tag_confidence: float | None,
    auto_approved: bool,
    first_seen_at: float,
) -> int:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            INSERT INTO inbound_media (
                sha256, mime_type, byte_size, width, height, local_path, status,
                vision_provider, tag_confidence, auto_approved, first_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sha256) DO UPDATE SET
                mime_type = excluded.mime_type,
                byte_size = excluded.byte_size,
                width = excluded.width,
                height = excluded.height,
                local_path = excluded.local_path
            """,
            (
                sha256,
                mime_type,
                byte_size,
                width,
                height,
                local_path,
                status,
                vision_provider,
                tag_confidence,
                int(auto_approved),
                first_seen_at,
            ),
        )
        row = conn.execute("SELECT id FROM inbound_media WHERE sha256 = ?", (sha256,)).fetchone()
        conn.execute("COMMIT")
        if row is None:
            raise RuntimeError("inbound_media upsert did not return an id")
        return int(row["id"])
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _insert_sticker_context_sync(
    conn: sqlite3.Connection,
    media_id: int,
    group_id: str,
    source_message_id: str,
    sender_id: str,
    observed_at: float,
    before_context_json: str,
    source: str,
) -> int:
    conn.execute("BEGIN IMMEDIATE")
    try:
        cursor = conn.execute(
            """
            INSERT INTO sticker_context_observations (
                media_id, group_id, source_message_id, sender_id, observed_at,
                before_context_json, after_context_json, situation_tags_json, source
            ) VALUES (?, ?, ?, ?, ?, ?, '[]', '[]', ?)
            ON CONFLICT(source_message_id, media_id) DO UPDATE SET
                group_id = excluded.group_id,
                sender_id = excluded.sender_id,
                observed_at = excluded.observed_at,
                before_context_json = excluded.before_context_json,
                source = excluded.source
            """,
            (
                media_id,
                group_id,
                source_message_id,
                sender_id,
                observed_at,
                before_context_json,
                source,
            ),
        )
        row = conn.execute(
            "SELECT id FROM sticker_context_observations WHERE source_message_id = ? AND media_id = ?",
            (source_message_id, media_id),
        ).fetchone()
        conn.execute("COMMIT")
        if row is not None:
            return int(row["id"])
        if cursor.lastrowid is not None:
            return int(cursor.lastrowid)
        raise RuntimeError("context observation insert did not return an id")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _delete_inbound_media_sync(conn: sqlite3.Connection, media_id: int) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM sticker_context_observations WHERE media_id = ?", (media_id,))
        conn.execute("DELETE FROM sticker_preferences WHERE media_id = ?", (media_id,))
        conn.execute("DELETE FROM inbound_media_groups WHERE media_id = ?", (media_id,))
        conn.execute("DELETE FROM inbound_media WHERE id = ?", (media_id,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _claim_event_sync(conn: sqlite3.Connection, key: str, now_s: float, ttl_seconds: int) -> bool:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "DELETE FROM event_dedupe WHERE key = ? AND expires_at <= ?",
            (key, now_s),
        )
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO event_dedupe (key, seen_at, expires_at)
            VALUES (?, ?, ?)
            """,
            (key, now_s, now_s + max(1, ttl_seconds)),
        )
        claimed = cursor.rowcount > 0
        cursor.close()
        conn.execute("COMMIT")
        return claimed
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _prune_event_dedupe_sync(conn: sqlite3.Connection, now_s: float, max_entries: int) -> int:
    conn.execute("BEGIN IMMEDIATE")
    try:
        expired_cursor = conn.execute(
            "DELETE FROM event_dedupe WHERE expires_at <= ?",
            (now_s,),
        )
        removed = max(0, expired_cursor.rowcount)
        expired_cursor.close()
        if max_entries <= 0:
            count_cursor = conn.execute("DELETE FROM event_dedupe")
            removed += max(0, count_cursor.rowcount)
            count_cursor.close()
        else:
            count_cursor = conn.execute(
                """
                DELETE FROM event_dedupe
                WHERE key NOT IN (
                    SELECT key FROM event_dedupe
                    ORDER BY seen_at DESC
                    LIMIT ?
                )
                """,
                (max_entries,),
            )
            removed += max(0, count_cursor.rowcount)
            count_cursor.close()
        conn.execute("COMMIT")
        return removed
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _migrate_schema(conn: sqlite3.Connection) -> None:
    wake_log_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(wake_log)").fetchall()
    }
    for column in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if column not in wake_log_columns:
            conn.execute(f"ALTER TABLE wake_log ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")


def _apply_initial_schema(conn: sqlite3.Connection) -> None:
    _execute_schema_statements(conn, _SCHEMA)
    _migrate_schema(conn)


def _apply_sticker_ingest_schema(conn: sqlite3.Connection) -> None:
    _execute_schema_statements(conn, _STICKER_INGEST_SCHEMA)


def _apply_sticker_ingest_metadata_schema(conn: sqlite3.Connection) -> None:
    _execute_schema_statements(conn, _STICKER_INGEST_METADATA_SCHEMA)


def _execute_schema_statements(conn: sqlite3.Connection, schema: str) -> None:
    for statement in schema.split(";"):
        if statement.strip():
            conn.execute(statement)


_MIGRATIONS = (
    Migration(version=1, name="initial_schema", apply=_apply_initial_schema),
    Migration(version=2, name="sticker_ingest", apply=_apply_sticker_ingest_schema),
    Migration(version=3, name="sticker_ingest_metadata", apply=_apply_sticker_ingest_metadata_schema),
)


def _wal_size_bytes(database_path: Path) -> int:
    wal_path = Path(f"{database_path}-wal")
    try:
        return wal_path.stat().st_size
    except OSError:
        return 0


def _close_connection_sync(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        logger.warning("SQLite WAL checkpoint failed during shutdown", exc_info=True)
    conn.close()

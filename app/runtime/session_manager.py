from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from app.domain.types import Session, SessionState, scene_session_id
from app.storage.db import StateStore


def trim_conversation(conversation: list[dict[str, Any]], max_messages: int) -> list[dict[str, Any]]:
    """Keep the latest messages without splitting an assistant tool_calls turn from its tool results."""
    if max_messages <= 0 or not conversation:
        return []
    if len(conversation) <= max_messages:
        return conversation
    start = len(conversation) - max_messages
    while start > 0 and conversation[start].get("role") == "tool":
        start -= 1
    return conversation[start:]


class SessionManager:
    def __init__(self, store: StateStore) -> None:
        self._store = store
        self._sessions: dict[str, Session] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def load(self) -> None:
        for session in await self._store.load_sessions():
            if session.state in (SessionState.ACTIVE, SessionState.WAKING):
                session.state = SessionState.IDLE
            self._sessions[session.id] = session

    async def save_all(self) -> None:
        for session in self._sessions.values():
            await self._store.upsert_session(session)

    def ensure_session(self, scene_type: str, scene_id: str) -> Session:
        session_id = scene_session_id(scene_type, scene_id)
        session = self._sessions.get(session_id)
        if session is None:
            session = Session(id=session_id, scene_type=scene_type, scene_id=scene_id)
            self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def all(self) -> list[Session]:
        return list(self._sessions.values())

    def lock(self, session_id: str) -> asyncio.Lock:
        return self._locks[session_id]

    async def save(self, session: Session) -> None:
        await self._store.upsert_session(session)

    async def set_state(self, session: Session, state: SessionState) -> None:
        session.state = state
        await self._store.upsert_session(session)

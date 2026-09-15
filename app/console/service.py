from __future__ import annotations

from app.domain.types import SessionState
from app.memory.store import MemoryStore
from app.runtime.bus import EventBus
from app.runtime.session_manager import SessionManager
from app.storage.db import StateStore


class ConsoleService:
    def __init__(
        self,
        sessions: SessionManager,
        memory: MemoryStore,
        store: StateStore,
        bus: EventBus | None = None,
    ) -> None:
        self.sessions = sessions
        self.memory = memory
        self.store = store
        self.bus = bus

    async def pause(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if session is None:
            return False
        session.state = SessionState.PAUSED
        session.next_wake_at = 0.0
        await self.sessions.save(session)
        self._publish("session-updated", {"session": session.to_dict()})
        return True

    async def resume(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if session is None:
            return False
        session.state = SessionState.IDLE
        session.next_wake_at = 0.0
        await self.sessions.save(session)
        self._publish("session-updated", {"session": session.to_dict()})
        return True

    async def reset_memory(self, session_id: str) -> bool:
        if self.sessions.get(session_id) is None:
            return False
        await self.store.delete_memory(session_id)
        await self.store.append_audit(
            action="reset_memory",
            detail=f"console reset memory for {session_id}",
            result="ok",
            session_id=session_id,
            actor_type="console",
        )
        return True

    async def delete_learning_note(self, session_id: str, kind: str, key: str) -> bool:
        if self.sessions.get(session_id) is None:
            return False
        notes = await self.store.list_learning_notes(session_id)
        existed = any(str(item["kind"]) == kind and str(item["key"]) == key for item in notes)
        await self.memory.delete_learning_note(session_id, kind, key)
        if not existed:
            return False
        await self.store.append_audit(
            action="learning_note_deleted",
            detail=f"{kind}/{key}",
            result="ok",
            session_id=session_id,
            actor_type="console",
        )
        self._publish("learning-updated", {"session_id": session_id, "deleted_note": key})
        return True

    async def delete_member_data(self, session_id: str, member_id: str) -> bool:
        if self.sessions.get(session_id) is None:
            return False
        await self.memory.delete_member_data(session_id, member_id)
        await self.store.append_audit(
            action="learning_member_deleted",
            detail=f"member={member_id}",
            result="ok",
            session_id=session_id,
            actor_type="console",
        )
        self._publish("learning-updated", {"session_id": session_id, "deleted_member": member_id})
        return True

    async def clear_learning(self, session_id: str) -> bool:
        if self.sessions.get(session_id) is None:
            return False
        await self.memory.clear_learning(session_id)
        await self.store.append_audit(
            action="learning_cleared",
            detail="console cleared learning data",
            result="ok",
            session_id=session_id,
            actor_type="console",
        )
        await self.store.update_learning_state(session_id, last_attempt_at=0.0, last_learning_at=0.0)
        self._publish("learning-updated", {"session_id": session_id, "cleared": True})
        return True

    def _publish(self, event_type: str, payload: dict[str, object]) -> None:
        if self.bus:
            self.bus.publish({"type": event_type, **payload})

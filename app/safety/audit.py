from __future__ import annotations

from app.storage.db import StateStore


class AuditLog:
    def __init__(self, state_store: StateStore) -> None:
        self.state_store = state_store

    async def record(
        self,
        action: str,
        detail: str,
        result: str,
        session_id: str | None = None,
        actor_type: str = "system",
    ) -> None:
        await self.state_store.append_audit(
            action=action,
            detail=detail,
            result=result,
            session_id=session_id,
            actor_type=actor_type,
        )

    async def recent(self, limit: int = 100) -> list[dict[str, object]]:
        return await self.state_store.list_audit(limit=limit)


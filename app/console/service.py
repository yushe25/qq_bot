from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from app.domain.types import SessionState
from app.media.sticker_promotion import StickerPromotionService
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
        sticker_dir: str | Path | None = None,
        sticker_promotion: StickerPromotionService | None = None,
    ) -> None:
        self.sessions = sessions
        self.memory = memory
        self.store = store
        self.bus = bus
        self.sticker_dir = Path(sticker_dir).resolve() if sticker_dir is not None else None
        self.sticker_promotion = sticker_promotion

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

    async def list_sticker_review(self, group_id: str) -> list[dict[str, object]]:
        rows = await self.store.list_inbound_media_for_review(group_id)
        return [_review_item(row) for row in rows]

    async def approve_sticker(
        self,
        media_id: int,
        group_id: str,
        metadata: dict[str, object] | None = None,
    ) -> str:
        if self.sticker_promotion is None:
            return "unavailable"
        status = await self.sticker_promotion.approve(media_id, group_id, metadata=metadata)
        if status == "approved":
            await self.store.append_audit(
                action="sticker_review_approved",
                detail=f"media={media_id} group={group_id}",
                result="approved",
                actor_type="console",
            )
        return status

    async def reject_sticker(self, media_id: int, group_id: str, reason: str) -> bool:
        media = await self.store.get_inbound_media(media_id)
        if media is None:
            return False
        local_path = await self._move_to_rejected(media)
        await self.store.update_inbound_media_status(
            media_id,
            "rejected",
            local_path=local_path,
            reviewed_by="console",
            reject_reason=reason[:200],
        )
        await self.store.append_audit(
            action="sticker_review_rejected",
            detail=f"media={media_id} group={group_id}",
            result="rejected",
            actor_type="console",
        )
        return True

    async def update_sticker_metadata(self, media_id: int, metadata: dict[str, object]) -> bool:
        media = await self.store.get_inbound_media(media_id)
        if media is None:
            return False
        await self.store.update_inbound_media_metadata(
            media_id,
            name=str(metadata.get("name") or ""),
            description=str(metadata.get("description") or ""),
            tags=_string_list(metadata.get("tags")),
            moods=_string_list(metadata.get("moods")),
            scenes=_string_list(metadata.get("scenes")),
            avoid_scenes=_string_list(metadata.get("avoidScenes") or metadata.get("avoid_scenes")),
        )
        await self.store.append_audit(
            action="sticker_review_metadata_updated",
            detail=f"media={media_id}",
            result="ok",
            actor_type="console",
        )
        return True

    async def sticker_preview_path(self, media_id: int) -> Path | None:
        media = await self.store.get_inbound_media(media_id)
        if media is None or self.sticker_dir is None:
            return None
        path = (self.sticker_dir / str(media["local_path"])).resolve()
        try:
            path.relative_to(self.sticker_dir)
        except ValueError:
            return None
        return path if path.is_file() else None

    async def sticker_context(self, media_id: int) -> list[dict[str, object]]:
        rows = await self.store.list_sticker_context_for_media(media_id)
        return [_context_item(row) for row in rows]

    async def _move_to_rejected(self, media: dict[str, object]) -> str | None:
        if self.sticker_dir is None:
            return None
        source = (self.sticker_dir / str(media["local_path"])).resolve()
        try:
            source.relative_to(self.sticker_dir)
        except ValueError:
            return None
        target = self.sticker_dir / "rejected" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file() and not target.exists():
            shutil.move(str(source), str(target))
        if not target.is_file():
            return None
        return target.relative_to(self.sticker_dir).as_posix()

    def _publish(self, event_type: str, payload: dict[str, object]) -> None:
        if self.bus:
            self.bus.publish({"type": event_type, **payload})


def _review_item(row: dict[str, Any]) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "status": str(row["status"]),
        "mimeType": str(row["mime_type"]),
        "byteSize": int(row["byte_size"]),
        "groupId": str(row["group_id"]),
        "seenCount": int(row["seen_count"]),
        "name": str(row.get("name") or ""),
        "description": str(row.get("description") or ""),
        "tags": _json_list(row.get("tags_json")),
        "moods": _json_list(row.get("moods_json")),
        "scenes": _json_list(row.get("scenes_json")),
        "avoidScenes": _json_list(row.get("avoid_scenes_json")),
        "tagConfidence": row.get("tag_confidence"),
        "firstSeenAt": float(row.get("group_first_seen_at") or row.get("first_seen_at") or 0),
    }


def _context_item(row: dict[str, Any]) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "groupId": str(row["group_id"]),
        "observedAt": float(row["observed_at"]),
        "beforeContext": _json_list(row.get("before_context_json")),
        "afterContext": _json_list(row.get("after_context_json")),
        "situationTags": _json_list(row.get("situation_tags_json")),
    }


def _json_list(value: object) -> list[object]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return list(parsed)
    return []


def _string_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []

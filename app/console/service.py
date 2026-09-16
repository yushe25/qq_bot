from __future__ import annotations

import base64
import binascii
import json
import math
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.domain.types import SessionState
from app.media.sticker_promotion import StickerPromotionService
from app.memory.store import MemoryStore
from app.runtime.bus import EventBus
from app.runtime.session_manager import SessionManager
from app.storage.db import StateStore


class InvalidReviewCursor(ValueError):
    pass


class InvalidLogCursor(ValueError):
    pass


class ConsoleService:
    def __init__(
        self,
        sessions: SessionManager,
        memory: MemoryStore,
        store: StateStore,
        bus: EventBus | None = None,
        sticker_dir: str | Path | None = None,
        sticker_promotion: StickerPromotionService | None = None,
        allowed_groups: Sequence[str] = (),
    ) -> None:
        self.sessions = sessions
        self.memory = memory
        self.store = store
        self.bus = bus
        self.sticker_dir = Path(sticker_dir).resolve() if sticker_dir is not None else None
        self.sticker_promotion = sticker_promotion
        self.allowed_groups = tuple(str(item) for item in allowed_groups)

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

    async def list_sticker_review(
        self,
        group_id: str | None = None,
        *,
        limit: int = 10,
        cursor: str | None = None,
    ) -> dict[str, object]:
        if not 10 <= limit <= 50:
            raise ValueError("limit must be between 10 and 50")
        if group_id is not None and group_id not in self.allowed_groups:
            raise PermissionError("group is not in safety.allowGroups")
        decoded = _decode_review_cursor(cursor) if cursor else None
        rows, has_more = await self.store.list_inbound_media_for_review(
            self.allowed_groups,
            group_id=group_id,
            limit=limit,
            cursor=decoded,
        )
        total_pending = await self.store.count_inbound_media_for_review(
            self.allowed_groups,
            group_id=group_id,
        )
        items = [_review_item(row) for row in rows]
        next_cursor = None
        if has_more and items:
            last = rows[-1]
            next_cursor = _encode_review_cursor(
                float(last["group_first_seen_at"]),
                int(last["id"]),
                str(last["group_id"]),
            )
        return {
            "items": items,
            "nextCursor": next_cursor,
            "totalPending": total_pending,
        }

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

    async def list_logs(
        self,
        log_type: str = "all",
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, object]:
        if log_type not in {"all", "wake", "audit"}:
            raise ValueError("invalid log type")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        decoded = _decode_log_cursor(cursor) if cursor else None
        rows, has_more = await self.store.list_merged_logs(
            log_type,
            limit=limit,
            cursor=decoded,
        )
        items = [_merged_log_item(row) for row in rows]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = _encode_log_cursor(
                float(last["timestamp"]),
                int(last["source_priority"]),
                int(last["source_id"]),
            )
        return {"items": items, "nextCursor": next_cursor}

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
    media_id = int(row["id"])
    return {
        "id": media_id,
        "mediaId": media_id,
        "groupId": str(row["group_id"]),
        "previewUrl": f"/api/stickers/{media_id}/preview",
        "status": str(row.get("review_status") or row["status"]),
        "mimeType": str(row["mime_type"]),
        "byteSize": int(row["byte_size"]),
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


def _encode_review_cursor(first_seen_at: float, media_id: int, group_id: str) -> str:
    payload = json.dumps(
        [1, first_seen_at, media_id, group_id],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_review_cursor(value: str) -> tuple[float, int, str]:
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(f"{value}{padding}")
        payload = json.loads(raw.decode("utf-8"))
        if (
            not isinstance(payload, list)
            or len(payload) != 4
            or payload[0] != 1
            or not isinstance(payload[1], (int, float))
            or isinstance(payload[1], bool)
            or not isinstance(payload[2], int)
            or isinstance(payload[2], bool)
            or not isinstance(payload[3], str)
        ):
            raise ValueError("invalid cursor payload")
        first_seen_at = float(payload[1])
        media_id = int(payload[2])
        group_id = payload[3]
        if not math.isfinite(first_seen_at) or media_id < 0 or not group_id:
            raise ValueError("invalid cursor values")
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error) as exc:
        raise InvalidReviewCursor("invalid cursor") from exc
    return first_seen_at, media_id, group_id


def _context_item(row: dict[str, Any]) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "groupId": str(row["group_id"]),
        "observedAt": float(row["observed_at"]),
        "beforeContext": _json_list(row.get("before_context_json")),
        "afterContext": _json_list(row.get("after_context_json")),
        "situationTags": _json_list(row.get("situation_tags_json")),
    }


def _merged_log_item(row: dict[str, Any]) -> dict[str, object]:
    log_type = str(row["type"])
    source_id = int(row["source_id"])
    return {
        "id": f"{log_type}:{source_id}",
        "type": log_type,
        "timestamp": float(row["timestamp"]),
        "sessionId": str(row.get("session_id") or ""),
        "title": str(row.get("title") or ""),
        "result": str(row.get("result") or ""),
        "detail": str(row.get("detail") or ""),
    }


def _encode_log_cursor(timestamp: float, source_priority: int, source_id: int) -> str:
    payload = json.dumps(
        [1, timestamp, source_priority, source_id],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_log_cursor(value: str) -> tuple[float, int, int]:
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(f"{value}{padding}")
        payload = json.loads(raw.decode("utf-8"))
        if (
            not isinstance(payload, list)
            or len(payload) != 4
            or payload[0] != 1
            or not isinstance(payload[1], (int, float))
            or isinstance(payload[1], bool)
            or not isinstance(payload[2], int)
            or isinstance(payload[2], bool)
            or not isinstance(payload[3], int)
            or isinstance(payload[3], bool)
        ):
            raise ValueError("invalid cursor payload")
        timestamp = float(payload[1])
        source_priority = int(payload[2])
        source_id = int(payload[3])
        if not math.isfinite(timestamp) or source_priority < 0 or source_id < 0:
            raise ValueError("invalid cursor values")
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error) as exc:
        raise InvalidLogCursor("invalid cursor") from exc
    return timestamp, source_priority, source_id


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

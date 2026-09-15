from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import SafetyConfig, StickerIngestConfig, StickerIngestGroupConfig
from app.domain.types import NormalizedEvent
from app.media.sticker_promotion import StickerPromotionService
from app.platform.gateway import QQGateway
from app.safety.audit import AuditLog
from app.safety.media_guard import MediaGuard
from app.storage.db import StateStore

logger = logging.getLogger("qqf.media.ingest")

_MAX_IMAGE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class IngestResult:
    accepted: bool
    reason: str
    media_id: int | None = None
    sha256: str = ""


class InboundIngest:
    def __init__(
        self,
        library_dir: str | Path,
        config: StickerIngestConfig,
        safety: SafetyConfig,
        gateway: QQGateway,
        store: StateStore,
        *,
        audit: AuditLog | None = None,
        media_guard: MediaGuard | None = None,
        promotion: StickerPromotionService | None = None,
    ) -> None:
        self.library_dir = Path(library_dir).resolve()
        self.config = config
        self.safety = safety
        self.gateway = gateway
        self.store = store
        self.audit = audit
        self.promotion = promotion
        self.media_guard = media_guard or MediaGuard(max_bytes=_MAX_IMAGE_BYTES, max_pixels=40_960_000)
        self.inbox_dir = self.library_dir / "inbox"

    async def handle_event(self, event: NormalizedEvent) -> list[IngestResult]:
        if not self.config.enabled or event.kind != "group":
            return []
        group_id = _session_scene_id(event.session_id)
        group_config = self.config.groups.get(group_id)
        if group_config is None or not group_config.enabled or group_id not in self.safety.allow_groups:
            return []
        image_segments = [segment for segment in event.segments if segment.kind in {"image", "mface"}]
        if not image_segments:
            return []
        stats = await self.store.inbound_media_group_stats(group_id)
        results: list[IngestResult] = []
        for segment in image_segments:
            result = await self._ingest_segment(event, group_id, group_config, segment, stats)
            results.append(result)
            if result.accepted:
                stats = await self.store.inbound_media_group_stats(group_id)
        return results

    async def _ingest_segment(
        self,
        event: NormalizedEvent,
        group_id: str,
        group_config: StickerIngestGroupConfig,
        segment: object,
        stats: dict[str, int],
    ) -> IngestResult:
        try:
            payload = await self.gateway.fetch_image_segment(segment, max_bytes=_MAX_IMAGE_BYTES)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 - media boundary; never break chat ingestion
            await self._audit("sticker_ingest_fetch_failed", "image fetch failed", "failed", group_id)
            logger.warning("inbound image fetch failed: %s", type(exc).__name__)
            return IngestResult(False, "fetch failed")
        guard_ok, guard_reason = self.media_guard.check_bytes(payload)
        if not guard_ok:
            await self._audit("sticker_ingest_rejected", guard_reason, "rejected", group_id)
            return IngestResult(False, guard_reason)
        image_type = _detect_image_type(payload)
        if image_type is None:
            await self._audit("sticker_ingest_rejected", "unsupported image", "rejected", group_id)
            return IngestResult(False, "unsupported image")
        mime_type, extension = image_type
        digest = hashlib.sha256(payload).hexdigest()
        existing = await self.store.get_inbound_media_by_hash(digest)
        if existing is not None:
            await self.store.touch_inbound_media_group(int(existing["id"]), group_id, event.timestamp)
            return IngestResult(True, "duplicate", int(existing["id"]), digest)
        if stats["item_count"] >= group_config.max_items or stats["byte_size"] >= group_config.max_bytes:
            await self._audit("sticker_ingest_quota", f"group={group_id}", "blocked", group_id)
            return IngestResult(False, "quota reached")

        relative_path = Path("inbox") / f"{digest}{extension}"
        target = (self.library_dir / relative_path).resolve()
        try:
            target.relative_to(self.library_dir)
        except ValueError:
            await self._audit("sticker_ingest_rejected", "path escape", "rejected", group_id)
            return IngestResult(False, "invalid path")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(payload)
        media_id = await self.store.upsert_inbound_media(
            sha256=digest,
            mime_type=mime_type,
            byte_size=len(payload),
            width=None,
            height=None,
            local_path=relative_path.as_posix(),
            status="pending_review",
            first_seen_at=event.timestamp,
        )
        await self.store.touch_inbound_media_group(media_id, group_id, event.timestamp)
        status = "pending_review"
        if self.promotion is not None:
            status = await self.promotion.promote(media_id, group_id, group_config)
        await self._audit("sticker_ingest_saved", f"group={group_id} bytes={len(payload)}", status, group_id)
        return IngestResult(True, status, media_id, digest)

    async def _audit(self, action: str, detail: str, result: str, session_id: str) -> None:
        if self.audit is not None:
            await self.audit.record(
                action=action,
                detail=detail,
                result=result,
                session_id=f"group:{session_id}",
                actor_type="system",
            )


def _detect_image_type(payload: bytes) -> tuple[str, str] | None:
    if payload.startswith(b"\xff\xd8"):
        return "image/jpeg", ".jpg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def _session_scene_id(session_id: str) -> str:
    return session_id.split(":", 1)[1] if ":" in session_id else session_id

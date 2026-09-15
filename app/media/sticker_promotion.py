from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol

from app.agent.sticker_manifest import (
    StickerManifestEntry,
    load_manifest,
    manifest_lock,
    save_manifest_atomic,
)
from app.agent.sticker_tagger import StickerTagDraft
from app.config import StickerAutoTagConfig, StickerIngestGroupConfig
from app.safety.audit import AuditLog
from app.storage.db import StateStore


class _Tagger(Protocol):
    async def draft_for_image(self, image_path: Path) -> StickerTagDraft: ...


class StickerPromotionService:
    def __init__(
        self,
        library_dir: str | Path,
        *,
        auto_tag: StickerAutoTagConfig,
        tagger: _Tagger,
        store: StateStore,
        audit: AuditLog | None = None,
    ) -> None:
        self.library_dir = Path(library_dir).resolve()
        self.auto_tag = auto_tag
        self.tagger = tagger
        self.store = store
        self.audit = audit
        self.manifest_path = self.library_dir / "manifest.yaml"

    async def promote(
        self,
        media_id: int,
        group_id: str,
        group_config: StickerIngestGroupConfig,
    ) -> str:
        media = await self.store.get_inbound_media(media_id)
        if media is None:
            return "missing"
        if not group_config.vision_enabled or not self.auto_tag.enabled:
            return "pending_review"
        source = (self.library_dir / str(media["local_path"])).resolve()
        try:
            source.relative_to(self.library_dir)
        except ValueError:
            await self._audit("sticker_promotion_rejected", "path escape", group_id)
            return "pending_review"
        try:
            draft = await self.tagger.draft_for_image(source)
        except Exception as exc:  # noqa: BLE001 - vision boundary; keep pending on failure
            await self._audit("sticker_promotion_failed", type(exc).__name__, group_id)
            return "pending_review"
        await self.store.update_inbound_media_status(
            media_id,
            "pending_review",
            vision_provider=self.auto_tag.provider,
            tag_confidence=draft.confidence,
        )
        if draft.confidence < group_config.auto_approve_confidence:
            return "pending_review"

        target = (self.library_dir / "images" / f"{media['sha256']}{source.suffix.lower()}").resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with manifest_lock(self.manifest_path):
            entries = load_manifest(self.manifest_path) if self.manifest_path.exists() else []
            sticker_id = _entry_id(entries, str(media["sha256"]))
            relative_file = target.relative_to(self.library_dir).as_posix()
            if not target.exists():
                shutil.move(str(source), str(target))
            entry = StickerManifestEntry.model_validate(
                {
                    "id": sticker_id,
                    "name": draft.name,
                    "file": relative_file,
                    "approved": True,
                    "description": draft.description,
                    "tags": draft.tags,
                    "moods": draft.moods,
                    "scenes": draft.scenes,
                    "avoid_scenes": draft.avoid_scenes,
                    "tag_source": "auto",
                    "content_hash": f"sha256:{media['sha256']}",
                    "tag_confidence": draft.confidence,
                    "source": "group_capture",
                    "source_group_ids": [group_id],
                    "scope_groups": [group_id],
                }
            )
            entries = [item for item in entries if item.id != sticker_id]
            entries.append(entry)
            save_manifest_atomic(self.manifest_path, entries)
        await self.store.update_inbound_media_status(
            media_id,
            "approved",
            vision_provider=self.auto_tag.provider,
            tag_confidence=draft.confidence,
            auto_approved=True,
        )
        await self._audit("sticker_promotion_approved", f"group={group_id}", group_id)
        return "approved"

    async def _audit(self, action: str, detail: str, group_id: str) -> None:
        if self.audit is not None:
            await self.audit.record(
                action=action,
                detail=detail,
                result=action,
                session_id=f"group:{group_id}",
                actor_type="system",
            )


def _entry_id(entries: list[StickerManifestEntry], sha256: str) -> str:
    base = f"auto-{sha256[:12]}"
    existing = {entry.id for entry in entries}
    if base not in existing:
        return base
    index = 2
    while f"{base}-{index}" in existing:
        index += 1
    return f"{base}-{index}"

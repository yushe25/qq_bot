from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

from app.agent.sticker_manifest import load_manifest
from app.agent.sticker_tagger import StickerTagDraft
from app.config import StickerAutoTagConfig, StickerAutoTagProviderConfig, StickerIngestGroupConfig
from app.media.sticker_promotion import StickerPromotionService
from app.safety.audit import AuditLog
from app.storage.db import StateStore


class _FakeTagger:
    def __init__(self, confidence: float) -> None:
        self.confidence = confidence
        self.calls = 0

    async def draft_for_image(self, image_path: Path) -> StickerTagDraft:
        self.calls += 1
        assert image_path.is_file()
        return StickerTagDraft(
            name="无语猫",
            description="白猫翻白眼",
            tags=["无语"],
            moods=["无语"],
            scenes=["被吐槽时"],
            avoid_scenes=["严肃道歉时"],
            confidence=self.confidence,
        )


def _gif_bytes() -> bytes:
    return b"GIF89a" + struct.pack("<HH", 1, 1) + b"\x00\x00\x00"


async def _setup(tmp_path: Path, confidence: float) -> tuple[StickerPromotionService, StateStore, _FakeTagger, int]:
    library_dir = tmp_path / "stickers"
    inbox = library_dir / "inbox"
    inbox.mkdir(parents=True)
    payload = _gif_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    path = inbox / f"{digest}.gif"
    path.write_bytes(payload)
    store = StateStore(tmp_path / "promotion.db")
    await store.init()
    media_id = await store.upsert_inbound_media(
        sha256=digest,
        mime_type="image/gif",
        byte_size=len(payload),
        width=1,
        height=1,
        local_path=f"inbox/{digest}.gif",
        status="pending_review",
        first_seen_at=1.0,
    )
    tagger = _FakeTagger(confidence)
    service = StickerPromotionService(
        library_dir,
        auto_tag=StickerAutoTagConfig(
            enabled=True,
            provider="vision-main",
            providers={
                "vision-main": StickerAutoTagProviderConfig(
                    base_url="https://vision.example/v1",
                    api_key_env="QQF_VISION_API_KEY",
                    model="vision-model",
                )
            },
        ),
        tagger=tagger,
        store=store,
        audit=AuditLog(store),
    )
    return service, store, tagger, media_id


async def test_promotion_auto_approves_high_confidence_sticker(tmp_path: Path) -> None:
    service, store, tagger, media_id = await _setup(tmp_path, 0.9)
    try:
        status = await service.promote(
            media_id,
            "100000",
            StickerIngestGroupConfig(vision_enabled=True, auto_approve_confidence=0.85),
        )
        media = await store.get_inbound_media(media_id)
    finally:
        await store.close()

    entries = load_manifest(tmp_path / "stickers" / "manifest.yaml")
    assert status == "approved"
    assert tagger.calls == 1
    assert media is not None and media["status"] == "approved" and media["auto_approved"] == 1
    assert media["local_path"] == entries[0].file
    assert media["name"] == "无语猫"
    assert json.loads(media["tags_json"]) == ["无语"]
    assert entries[0].source == "group_capture"
    assert entries[0].scope_groups == ["100000"]
    assert (tmp_path / "stickers" / entries[0].file).is_file()


async def test_promotion_keeps_low_confidence_pending(tmp_path: Path) -> None:
    service, store, _, media_id = await _setup(tmp_path, 0.5)
    try:
        status = await service.promote(
            media_id,
            "100000",
            StickerIngestGroupConfig(vision_enabled=True, auto_approve_confidence=0.85),
        )
        media = await store.get_inbound_media(media_id)
    finally:
        await store.close()

    assert status == "pending_review"
    assert media is not None and media["status"] == "pending_review"
    assert not (tmp_path / "stickers" / "manifest.yaml").exists()


async def test_promotion_does_not_call_vision_without_group_authorization(tmp_path: Path) -> None:
    service, store, tagger, media_id = await _setup(tmp_path, 0.9)
    try:
        status = await service.promote(
            media_id,
            "100000",
            StickerIngestGroupConfig(vision_enabled=False),
        )
    finally:
        await store.close()

    assert status == "pending_review"
    assert tagger.calls == 0

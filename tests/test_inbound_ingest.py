from __future__ import annotations

import struct
from pathlib import Path

from app.config import SafetyConfig, StickerIngestConfig, StickerIngestGroupConfig
from app.domain.types import MessageSegment, NormalizedEvent
from app.media.inbound_ingest import InboundIngest
from app.platform.gateway import NullGateway
from app.safety.audit import AuditLog
from app.safety.media_guard import MediaGuard
from app.storage.db import StateStore


def _gif_bytes(width: int = 1, height: int = 1) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00"


def _event(group_id: str = "100000", event_id: str = "img-1", private: bool = False) -> NormalizedEvent:
    scene_type = "private" if private else "group"
    return NormalizedEvent(
        event_id=event_id,
        session_id=f"{scene_type}:{group_id}",
        timestamp=123.0,
        sender_id="10001",
        sender_name="阿泽",
        kind=scene_type,
        segments=(MessageSegment(kind="image", data={"file": event_id}),),
        text="[图片]",
        raw={},
        bot_id="99999",
    )


async def _tagger(tmp_path: Path, gateway: NullGateway, config: StickerIngestConfig) -> tuple[InboundIngest, StateStore]:
    store = StateStore(tmp_path / "ingest.db")
    await store.init()
    ingest = InboundIngest(
        tmp_path / "stickers",
        config,
        SafetyConfig(allow_groups=["100000"]),
        gateway,
        store,
        audit=AuditLog(store),
        media_guard=MediaGuard(max_bytes=1024, max_pixels=10_000),
    )
    return ingest, store


def _config(max_items: int = 200) -> StickerIngestConfig:
    return StickerIngestConfig(
        enabled=True,
        groups={"100000": StickerIngestGroupConfig(enabled=True, max_items=max_items, max_bytes=1024)},
    )


async def test_inbound_ingest_saves_and_deduplicates_image(tmp_path: Path) -> None:
    gateway = NullGateway(image_payloads={"img-1": _gif_bytes(), "img-2": _gif_bytes()})
    ingest, store = await _tagger(tmp_path, gateway, _config())
    try:
        first = await ingest.handle_event(_event(event_id="img-1"))
        second = await ingest.handle_event(_event(event_id="img-2"))
        stats = await store.inbound_media_group_stats("100000")
    finally:
        await store.close()

    assert first[0].accepted is True
    assert first[0].reason == "pending_review"
    assert second[0].accepted is True
    assert second[0].reason == "duplicate"
    assert stats["item_count"] == 1
    assert stats["byte_size"] == len(_gif_bytes())
    assert len(list((tmp_path / "stickers" / "inbox").glob("*.gif"))) == 1


async def test_inbound_ingest_ignores_private_and_disabled_groups(tmp_path: Path) -> None:
    gateway = NullGateway(image_payloads={"img-1": _gif_bytes()})
    ingest, store = await _tagger(tmp_path, gateway, _config())
    try:
        private = await ingest.handle_event(_event(private=True))
        other_group = await ingest.handle_event(_event(group_id="200000"))
    finally:
        await store.close()

    assert private == []
    assert other_group == []


async def test_inbound_ingest_rejects_unsupported_bytes(tmp_path: Path) -> None:
    gateway = NullGateway(image_payloads={"img-1": b"not-an-image"})
    ingest, store = await _tagger(tmp_path, gateway, _config())
    try:
        result = await ingest.handle_event(_event(event_id="img-1"))
    finally:
        await store.close()

    assert result[0].accepted is False
    assert not list((tmp_path / "stickers" / "inbox").glob("*"))


async def test_inbound_ingest_enforces_group_item_quota(tmp_path: Path) -> None:
    gateway = NullGateway(
        image_payloads={
            "img-1": _gif_bytes(),
            "img-2": b"GIF89a" + struct.pack("<HH", 2, 2) + b"\x00\x00\x00",
        }
    )
    ingest, store = await _tagger(tmp_path, gateway, _config(max_items=1))
    try:
        first = await ingest.handle_event(_event(event_id="img-1"))
        second = await ingest.handle_event(_event(event_id="img-2"))
    finally:
        await store.close()

    assert first[0].accepted is True
    assert second[0].accepted is False
    assert second[0].reason == "quota reached"

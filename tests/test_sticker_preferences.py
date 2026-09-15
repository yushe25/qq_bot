from __future__ import annotations

from pathlib import Path

from app.config import StickerIngestConfig, StickerIngestGroupConfig
from app.domain.types import MessageSegment, NormalizedEvent
from app.media.inbound_ingest import IngestResult
from app.memory.sticker_preferences import StickerPreferenceService
from app.runtime.sticker_context import StickerContextObserver
from app.storage.db import StateStore


async def test_sticker_preferences_format_positive_and_negative_signals(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "prefs.db")
    await store.init()
    service = StickerPreferenceService(store)
    try:
        await service.record_member_usage(42, "100000", ["吐槽", "无语"])
        await service.apply_owner_correction(42, "100000", ["安慰"], suitable=False)
        experience = await service.experience_for_group("100000", {42: "auto-42"})
    finally:
        await store.close()

    assert "吐槽 +1" in experience["auto-42"]
    assert "无语 +1" in experience["auto-42"]
    assert "不适合安慰" in experience["auto-42"]


async def test_context_observer_updates_member_usage_preferences(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "prefs.db")
    await store.init()
    preferences = StickerPreferenceService(store)
    observer = StickerContextObserver(
        StickerIngestConfig(
            enabled=True,
            observation_before_messages=1,
            observation_after_messages=1,
            groups={"100000": StickerIngestGroupConfig(enabled=True)},
        ),
        store,
        preferences=preferences,
    )
    image_event = NormalizedEvent(
        event_id="img-1",
        session_id="group:100000",
        timestamp=1.0,
        sender_id="10001",
        sender_name="阿泽",
        kind="group",
        segments=(MessageSegment(kind="image", data={"file": "x"}),),
        text="[图片]",
        raw={},
        bot_id="99999",
    )
    after_event = NormalizedEvent(
        event_id="txt-1",
        session_id="group:100000",
        timestamp=2.0,
        sender_id="10002",
        sender_name="小皮",
        kind="group",
        segments=(MessageSegment(kind="text", data={"text": "哈哈笑死"}),),
        text="哈哈笑死",
        raw={},
        bot_id="99999",
    )
    try:
        await observer.observe_event(
            image_event,
            [IngestResult(accepted=True, reason="approved", media_id=42, sha256="hash")],
        )
        await observer.observe_event(after_event)
        rows = await store.list_sticker_preferences("100000", [42])
    finally:
        await store.close()

    assert any(row["situation_tag"] == "开心" and row["positive_score"] >= 1 for row in rows)

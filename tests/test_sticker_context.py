from __future__ import annotations

import json
from pathlib import Path

from app.config import StickerIngestConfig, StickerIngestGroupConfig
from app.domain.types import MessageSegment, NormalizedEvent
from app.media.inbound_ingest import IngestResult
from app.runtime.sticker_context import StickerContextObserver
from app.storage.db import StateStore


def _text_event(event_id: str, text: str, *, group_id: str = "100000", private: bool = False) -> NormalizedEvent:
    kind = "private" if private else "group"
    return NormalizedEvent(
        event_id=event_id,
        session_id=f"{kind}:{group_id}",
        timestamp=float(len(event_id)),
        sender_id="10001",
        sender_name="阿泽",
        kind=kind,
        segments=(MessageSegment(kind="text", data={"text": text}),),
        text=text,
        raw={"private": "must not persist", "text": text},
        bot_id="99999",
    )


def _image_event() -> NormalizedEvent:
    return NormalizedEvent(
        event_id="image-1",
        session_id="group:100000",
        timestamp=3.0,
        sender_id="10002",
        sender_name="小皮",
        kind="group",
        segments=(MessageSegment(kind="image", data={"file": "raw-media"}),),
        text="[图片]",
        raw={"file": "raw-media"},
        bot_id="99999",
    )


async def test_sticker_context_records_before_after_and_tags(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "context.db")
    await store.init()
    observer = StickerContextObserver(
        StickerIngestConfig(
            enabled=True,
            observation_before_messages=2,
            observation_after_messages=2,
            groups={"100000": StickerIngestGroupConfig(enabled=True)},
        ),
        store,
    )
    try:
        await observer.observe_event(_text_event("t1", "这也太离谱了"))
        await observer.observe_event(_text_event("t2", "给我整无语了"))
        await observer.observe_event(
            _image_event(),
            [IngestResult(accepted=True, reason="pending_review", media_id=42, sha256="hash")],
        )
        await observer.observe_event(_text_event("t3", "哈哈哈笑死"))
        await observer.observe_event(_text_event("t4", "确实可以"))
        rows = await store.list_sticker_context_observations("100000")
    finally:
        await store.close()

    assert len(rows) == 1
    row = rows[0]
    before = json.loads(row["before_context_json"])
    after = json.loads(row["after_context_json"])
    tags = json.loads(row["situation_tags_json"])
    assert [item["text"] for item in before] == ["这也太离谱了", "给我整无语了"]
    assert [item["text"] for item in after] == ["哈哈哈笑死", "确实可以"]
    assert "无语" in tags
    assert "开心" in tags
    assert "raw-media" not in row["before_context_json"]
    assert "raw-media" not in row["after_context_json"]


async def test_sticker_context_ignores_private_and_disabled_groups(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "context.db")
    await store.init()
    observer = StickerContextObserver(
        StickerIngestConfig(
            enabled=True,
            groups={"100000": StickerIngestGroupConfig(enabled=True)},
        ),
        store,
    )
    try:
        await observer.observe_event(_text_event("p1", "私聊内容", private=True))
        await observer.observe_event(_text_event("g1", "别的群", group_id="200000"))
        rows = await store.list_sticker_context_observations()
    finally:
        await store.close()

    assert rows == []

from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import ConsoleConfig, StickerAutoTagConfig
from app.console.router import build_console_router
from app.console.service import ConsoleService
from app.media.sticker_promotion import StickerPromotionService
from app.memory.store import MemoryStore
from app.runtime.bus import EventBus
from app.runtime.session_manager import SessionManager
from app.storage.db import StateStore


def _gif_bytes() -> bytes:
    return b"GIF89a" + struct.pack("<HH", 1, 1) + b"\x00\x00\x00"


async def _client(tmp_path: Path) -> tuple[TestClient, StateStore, int]:
    store = StateStore(tmp_path / "console.db")
    await store.init()
    library_dir = tmp_path / "stickers"
    inbox = library_dir / "inbox"
    inbox.mkdir(parents=True)
    payload = _gif_bytes()
    data_path = inbox / "capture.gif"
    data_path.write_bytes(payload)
    import hashlib

    digest = hashlib.sha256(payload).hexdigest()
    media_id = await store.upsert_inbound_media(
        sha256=digest,
        mime_type="image/gif",
        byte_size=len(payload),
        width=1,
        height=1,
        local_path="inbox/capture.gif",
        status="pending_review",
        first_seen_at=1.0,
    )
    await store.touch_inbound_media_group(media_id, "100000", 1.0)
    promotion = StickerPromotionService(
        library_dir,
        auto_tag=StickerAutoTagConfig(),
        tagger=None,
        store=store,
    )
    service = ConsoleService(
        SessionManager(store),
        MemoryStore(store),
        store,
        EventBus(),
        sticker_dir=library_dir,
        sticker_promotion=promotion,
    )
    app = FastAPI()
    app.state.runtime = SimpleNamespace(console=service)
    app.include_router(build_console_router(ConsoleConfig(token="secret")))
    return TestClient(app), store, media_id


async def test_console_sticker_preview_metadata_and_approve(tmp_path: Path) -> None:
    client, store, media_id = await _client(tmp_path)
    headers = {"authorization": "Bearer secret"}
    try:
        review = client.get("/api/stickers/review?groupId=100000", headers=headers)
        preview = client.get(f"/api/stickers/{media_id}/preview", headers=headers)
        update = client.patch(
            f"/api/stickers/{media_id}/metadata",
            headers=headers,
            json={"name": "无语猫", "tags": ["无语"], "moods": ["无语"], "scenes": ["吐槽"]},
        )
        approve = client.post(
            f"/api/stickers/{media_id}/approve",
            headers=headers,
            json={"groupId": "100000", "name": "无语猫", "tags": ["无语"]},
        )
        media = await store.get_inbound_media(media_id)
    finally:
        await store.close()

    assert review.status_code == 200
    assert review.json()["items"][0]["id"] == media_id
    assert preview.status_code == 200
    assert preview.content == _gif_bytes()
    assert update.status_code == 200
    assert approve.status_code == 200
    assert media is not None
    assert media["name"] == "无语猫"
    assert str(media["local_path"]).startswith("images/")


async def test_console_sticker_reject_updates_local_path(tmp_path: Path) -> None:
    client, store, media_id = await _client(tmp_path)
    try:
        reject = client.post(
            f"/api/stickers/{media_id}/reject",
            headers={"authorization": "Bearer secret"},
            json={"groupId": "100000", "reason": "test"},
        )
        media = await store.get_inbound_media(media_id)
    finally:
        await store.close()

    assert reject.status_code == 200
    assert media is not None
    assert media["status"] == "rejected"
    assert str(media["local_path"]).startswith("rejected/")


async def test_console_sticker_endpoints_require_auth(tmp_path: Path) -> None:
    client, store, media_id = await _client(tmp_path)
    try:
        assert client.get("/api/stickers/review?groupId=100000").status_code == 401
        assert client.get(f"/api/stickers/{media_id}/preview").status_code == 401
    finally:
        await store.close()


async def test_console_sticker_preview_rejects_path_traversal(tmp_path: Path) -> None:
    client, store, media_id = await _client(tmp_path)
    try:
        await store._execute("UPDATE inbound_media SET local_path = '../outside.gif' WHERE id = ?", (media_id,))
        response = client.get(
            f"/api/stickers/{media_id}/preview",
            headers={"authorization": "Bearer secret"},
        )
    finally:
        await store.close()

    assert response.status_code == 404

from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
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
        allowed_groups=("100000", "200000"),
    )
    app = FastAPI()
    app.state.runtime = SimpleNamespace(console=service)
    app.include_router(build_console_router(ConsoleConfig(token="secret")))
    return TestClient(app), store, media_id


async def _add_review_media(
    store: StateStore,
    *,
    group_id: str,
    first_seen_at: float,
    marker: str,
) -> int:
    digest = hashlib.sha256(marker.encode("utf-8")).hexdigest()
    media_id = await store.upsert_inbound_media(
        sha256=digest,
        mime_type="image/gif",
        byte_size=1,
        width=1,
        height=1,
        local_path=f"inbox/{marker}.gif",
        status="pending_review",
        first_seen_at=first_seen_at,
    )
    await store.touch_inbound_media_group(media_id, group_id, first_seen_at)
    return media_id


async def test_console_sticker_preview_metadata_and_approve(tmp_path: Path) -> None:
    client, store, media_id = await _client(tmp_path)
    headers = {"authorization": "Bearer secret"}
    metadata = {
        "name": "无语猫",
        "description": "白猫翻白眼",
        "tags": ["无语", "猫"],
        "moods": ["无语"],
        "scenes": ["吐槽"],
        "avoidScenes": ["严肃道歉"],
    }
    try:
        review = client.get("/api/stickers/review?groupId=100000", headers=headers)
        preview = client.get(f"/api/stickers/{media_id}/preview", headers=headers)
        update = client.patch(
            f"/api/stickers/{media_id}/metadata",
            headers=headers,
            json=metadata,
        )
        approve = client.post(
            f"/api/stickers/{media_id}/approve",
            headers=headers,
            json={"groupId": "100000", "metadata": metadata},
        )
        media = await store.get_inbound_media(media_id)
        groups = await store.get_inbound_media_groups(media_id)
        remaining = client.get("/api/stickers/review?groupId=100000", headers=headers)
    finally:
        await store.close()

    assert review.status_code == 200
    assert review.json()["items"][0]["mediaId"] == media_id
    assert review.json()["totalPending"] == 1
    assert preview.status_code == 200
    assert preview.content == _gif_bytes()
    assert update.status_code == 200
    assert approve.status_code == 200
    assert media is not None
    assert media["name"] == "无语猫"
    assert str(media["local_path"]).startswith("images/")
    assert groups[0]["review_status"] == "approved"
    assert remaining.json()["totalPending"] == 0


@pytest.mark.parametrize(
    "metadata",
    [
        {"name": "x" * 81},
        {"description": "x" * 121},
        {"tags": [f"tag-{index}" for index in range(9)]},
        {"tags": ["x" * 41]},
        {"tags": ["无语", "无语"]},
        {"description": "[CQ:image,file=http://evil]"},
        {"description": "联系 13800138000"},
    ],
)
async def test_console_sticker_metadata_rejects_invalid_values(
    tmp_path: Path,
    metadata: dict[str, object],
) -> None:
    client, store, media_id = await _client(tmp_path)
    headers = {"authorization": "Bearer secret"}
    try:
        response = client.patch(
            f"/api/stickers/{media_id}/metadata",
            headers=headers,
            json=metadata,
        )
        media = await store.get_inbound_media(media_id)
    finally:
        await store.close()

    assert response.status_code == 422
    assert media is not None and media["status"] == "pending_review"


async def test_console_sticker_approve_validates_nested_metadata(tmp_path: Path) -> None:
    client, store, media_id = await _client(tmp_path)
    try:
        response = client.post(
            f"/api/stickers/{media_id}/approve",
            headers={"authorization": "Bearer secret"},
            json={"groupId": "100000", "metadata": {"tags": ["x" * 41]}},
        )
        media = await store.get_inbound_media(media_id)
    finally:
        await store.close()

    assert response.status_code == 422
    assert media is not None and media["status"] == "pending_review"


async def test_console_sticker_review_aggregates_groups_and_paginates(tmp_path: Path) -> None:
    client, store, existing_media_id = await _client(tmp_path)
    headers = {"authorization": "Bearer secret"}
    try:
        for index in range(11):
            await _add_review_media(
                store,
                group_id="100000" if index % 2 else "200000",
                first_seen_at=10.0 + index,
                marker=f"review-{index}",
            )
        first_page = client.get("/api/stickers/review?limit=10", headers=headers)
        second_page = client.get(
            f"/api/stickers/review?limit=10&cursor={first_page.json()['nextCursor']}",
            headers=headers,
        )
        filtered = client.get("/api/stickers/review?groupId=100000&limit=10", headers=headers)
    finally:
        await store.close()

    assert first_page.status_code == 200
    assert second_page.status_code == 200
    assert filtered.status_code == 200
    first_items = first_page.json()["items"]
    second_items = second_page.json()["items"]
    assert len(first_items) == 10
    assert first_page.json()["totalPending"] == 12
    assert first_page.json()["nextCursor"]
    assert len(second_items) == 2
    assert second_page.json()["nextCursor"] is None
    keys = {(item["mediaId"], item["groupId"]) for item in [*first_items, *second_items]}
    assert len(keys) == 12
    assert existing_media_id in {item["mediaId"] for item in second_items}
    assert [item["firstSeenAt"] for item in first_items] == sorted(
        (item["firstSeenAt"] for item in first_items),
        reverse=True,
    )
    assert all(item["groupId"] == "100000" for item in filtered.json()["items"])
    assert all(item["previewUrl"].startswith("/api/stickers/") for item in first_items)


async def test_console_sticker_review_rejects_invalid_cursor_and_group(tmp_path: Path) -> None:
    client, store, _ = await _client(tmp_path)
    headers = {"authorization": "Bearer secret"}
    try:
        invalid_cursor = client.get("/api/stickers/review?cursor=not-a-cursor", headers=headers)
        invalid_group = client.get("/api/stickers/review?groupId=999999", headers=headers)
    finally:
        await store.close()

    assert invalid_cursor.status_code == 400
    assert invalid_group.status_code == 403


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


async def test_console_sticker_group_reviews_are_isolated(tmp_path: Path) -> None:
    client, store, media_id = await _client(tmp_path)
    headers = {"authorization": "Bearer secret"}
    try:
        await store.touch_inbound_media_group(media_id, "200000", 2.0)
        approve = client.post(
            f"/api/stickers/{media_id}/approve",
            headers=headers,
            json={
                "groupId": "100000",
                "metadata": {"name": "共享素材", "tags": ["测试"]},
            },
        )
        groups_after_approve = await store.get_inbound_media_groups(media_id)
        review_other = client.get("/api/stickers/review?groupId=200000", headers=headers)
        reject_other = client.post(
            f"/api/stickers/{media_id}/reject",
            headers=headers,
            json={"groupId": "200000", "reason": "只拒绝另一个群"},
        )
        media = await store.get_inbound_media(media_id)
        groups_after_reject = await store.get_inbound_media_groups(media_id)
    finally:
        await store.close()

    assert approve.status_code == 200
    assert reject_other.status_code == 200
    assert {row["group_id"]: row["review_status"] for row in groups_after_approve} == {
        "100000": "approved",
        "200000": "pending_review",
    }
    assert review_other.json()["totalPending"] == 1
    assert media is not None and media["status"] == "approved"
    assert str(media["local_path"]).startswith("images/")
    assert {row["group_id"]: row["review_status"] for row in groups_after_reject} == {
        "100000": "approved",
        "200000": "rejected",
    }


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

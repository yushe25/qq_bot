from __future__ import annotations

import struct
from pathlib import Path

import pytest
import yaml

from app.safety.media_guard import MediaGuard
from app.storage.db import StateStore


def _gif_bytes(width: int = 1, height: int = 1) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00"


def _write_gif(path: Path, width: int = 1, height: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_gif_bytes(width, height))


def _write_manifest(path: Path, entries: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(entries, allow_unicode=True), encoding="utf-8")


def _library(tmp_path: Path, entries: list[dict[str, object]], store: StateStore | None = None):
    root = tmp_path / "stickers"
    _write_manifest(root / "manifest.yaml", entries)
    _write_gif(root / "images" / "mock-01.gif")
    return init_library(root, store)


def init_library(root: Path, store: StateStore | None = None):
    from app.agent.sticker_library import StickerLibrary

    guard = MediaGuard(max_bytes=1024, max_pixels=10_000)
    return StickerLibrary(root, enabled=True, media_guard=guard, store=store)


def test_sticker_library_disabled_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "stickers"
    _write_manifest(
        root / "manifest.yaml",
        [
            {
                "id": "mock-01",
                "name": "无语猫",
                "file": "images/mock-01.gif",
                "tags": [],
                "approved": True,
                "usage_count": 0,
            }
        ],
    )
    _write_gif(root / "images" / "mock-01.gif")
    from app.agent.sticker_library import StickerLibrary

    library = StickerLibrary(root, enabled=False)

    assert library.available is False
    assert library.list_ids() == []
    assert library.resolve("mock-01") is None


def test_sticker_library_resolves_approved_entry(tmp_path: Path) -> None:
    library = _library(
        tmp_path,
        [
            {
                "id": "mock-01",
                "name": "无语猫",
                "file": "images/mock-01.gif",
                "tags": ["无语", "猫"],
                "approved": True,
                "usage_count": 0,
            }
        ],
    )

    sticker = library.resolve("mock-01")

    assert sticker is not None
    assert sticker.sticker_id == "mock-01"
    assert sticker.path.is_file()
    assert list(library.list_ids()) == ["mock-01"]


def test_sticker_library_rejects_unapproved_and_unknown(tmp_path: Path) -> None:
    library = _library(
        tmp_path,
        [
            {
                "id": "mock-01",
                "name": "无语猫",
                "file": "images/mock-01.gif",
                "tags": [],
                "approved": False,
                "usage_count": 0,
            }
        ],
    )

    assert library.resolve("mock-01") is None
    assert library.resolve("missing") is None


def test_sticker_library_rejects_path_traversal(tmp_path: Path) -> None:
    library = _library(
        tmp_path,
        [
            {
                "id": "evil",
                "name": "坏图",
                "file": "../../outside.gif",
                "tags": [],
                "approved": True,
                "usage_count": 0,
            }
        ],
    )

    assert library.available is False
    assert library.error is not None
    assert "路径" in library.error
    assert library.resolve("evil") is None


def test_sticker_library_degrades_on_bad_manifest(tmp_path: Path) -> None:
    root = tmp_path / "stickers"
    root.mkdir(parents=True)
    (root / "manifest.yaml").write_text("not: [valid", encoding="utf-8")

    library = init_library(root)

    assert library.available is False
    assert library.error
    assert library.resolve("anything") is None


def test_sticker_library_rejects_oversized_file(tmp_path: Path) -> None:
    root = tmp_path / "stickers"
    _write_manifest(
        root / "manifest.yaml",
        [
            {
                "id": "mock-01",
                "name": "大图",
                "file": "images/mock-01.gif",
                "tags": [],
                "approved": True,
                "usage_count": 0,
            }
        ],
    )
    _write_gif(root / "images" / "mock-01.gif", 400, 400)

    library = init_library(root)

    assert library.resolve("mock-01") is None


@pytest.mark.asyncio
async def test_sticker_usage_count_persists(tmp_path: Path) -> None:
    db_path = tmp_path / "qqf.db"
    store = StateStore(str(db_path))
    await store.init()
    try:
        library = _library(
            tmp_path,
            [
                {
                    "id": "mock-01",
                    "name": "无语猫",
                    "file": "images/mock-01.gif",
                    "tags": ["无语"],
                    "approved": True,
                    "usage_count": 3,
                }
            ],
            store=store,
        )

        assert await library.usage_count("mock-01") == 3
        await library.record_usage("mock-01")
        assert await library.usage_count("mock-01") == 4
    finally:
        await store.close()

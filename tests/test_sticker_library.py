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


def init_library(
    root: Path,
    store: StateStore | None = None,
    *,
    enabled: bool = True,
    allowed_ids: tuple[str, ...] = ("mock-01",),
):
    from app.agent.sticker_library import StickerLibrary

    guard = MediaGuard(max_bytes=1024, max_pixels=10_000)
    return StickerLibrary(
        root,
        enabled=enabled,
        allowed_ids=allowed_ids,
        media_guard=guard,
        store=store,
    )


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

    library = StickerLibrary(root, enabled=False, allowed_ids=["mock-01"])

    assert library.available is False
    assert library.list_ids() == []
    assert library.resolve("mock-01") is None


def test_sticker_library_only_lists_and_resolves_allowed_ids(tmp_path: Path) -> None:
    root = tmp_path / "stickers"
    entries = [
        {
            "id": "pilot-01",
            "name": "试点",
            "file": "images/pilot-01.gif",
            "tags": [],
            "approved": True,
            "usage_count": 0,
        },
        {
            "id": "normal-01",
            "name": "普通",
            "file": "images/normal-01.gif",
            "tags": [],
            "approved": True,
            "usage_count": 0,
        },
    ]
    _write_manifest(root / "manifest.yaml", entries)
    _write_gif(root / "images" / "pilot-01.gif")
    _write_gif(root / "images" / "normal-01.gif")

    library = init_library(root, allowed_ids=("pilot-01",))

    assert library.available is True
    assert library.list_ids() == ["pilot-01"]
    assert library.resolve("pilot-01") is not None
    assert library.resolve("normal-01") is None


def test_sticker_library_enabled_requires_allowlist(tmp_path: Path) -> None:
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

    library = init_library(root, allowed_ids=())

    assert library.available is False
    assert library.list_ids() == []


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


def test_sticker_catalog_only_contains_sendable_semantic_entries(tmp_path: Path) -> None:
    root = tmp_path / "stickers"
    _write_manifest(
        root / "manifest.yaml",
        [
            {
                "id": "pilot-01",
                "name": "无语猫",
                "file": "images/pilot-01.gif",
                "approved": True,
                "description": "白猫翻白眼",
                "tags": ["无语", "猫"],
                "moods": ["无语"],
                "scenes": ["被吐槽时"],
                "avoid_scenes": ["严肃道歉时"],
            },
            {
                "id": "pending-01",
                "name": "未审核",
                "file": "images/pending-01.gif",
                "approved": False,
                "tags": ["测试"],
            },
            {
                "id": "broken-01",
                "name": "文件缺失",
                "file": "images/missing.gif",
                "approved": True,
                "tags": ["测试"],
            },
        ],
    )
    _write_gif(root / "images" / "pilot-01.gif")

    library = init_library(root, allowed_ids=("pilot-01", "pending-01", "broken-01"))

    catalog = library.catalog()

    assert [entry.sticker_id for entry in catalog] == ["pilot-01"]
    assert catalog[0].description == "白猫翻白眼"
    assert catalog[0].scenes == ("被吐槽时",)
    assert "images/" not in library.catalog_text()
    assert "无语猫" in library.catalog_text()


def test_sticker_catalog_text_is_bounded_and_prioritizes_semantics(tmp_path: Path) -> None:
    library = _library(
        tmp_path,
        [
            {
                "id": "mock-01",
                "name": "无语猫",
                "file": "images/mock-01.gif",
                "approved": True,
                "description": "白猫翻白眼",
                "tags": ["无语", "敷衍"],
                "scenes": ["被吐槽时"],
                "avoid_scenes": ["严肃道歉时"],
            }
        ],
    )

    text = library.catalog_text(max_chars=24)

    assert len(text) <= 24
    assert text.startswith("- mock-01 | 无语猫")
    assert "images/" not in text


def test_sticker_library_isolates_group_captured_assets(tmp_path: Path) -> None:
    root = tmp_path / "stickers"
    _write_manifest(
        root / "manifest.yaml",
        [
            {
                "id": "auto-01",
                "name": "无语猫",
                "file": "images/mock-01.gif",
                "approved": True,
                "source": "group_capture",
                "source_group_ids": ["100000"],
                "scope_groups": ["100000"],
            }
        ],
    )
    _write_gif(root / "images" / "mock-01.gif")
    library = init_library(root, allowed_ids=("auto-01",))

    assert [entry.sticker_id for entry in library.catalog_for_scene("100000")] == ["auto-01"]
    assert library.catalog_for_scene("200000") == ()
    assert library.resolve_for_scene("auto-01", "100000") is not None
    assert library.resolve_for_scene("auto-01", "200000") is None


def test_sticker_library_legacy_assets_remain_global(tmp_path: Path) -> None:
    library = _library(
        tmp_path,
        [
            {
                "id": "mock-01",
                "name": "旧素材",
                "file": "images/mock-01.gif",
                "approved": True,
            }
        ],
    )

    assert library.resolve_for_scene("mock-01", "999999") is not None


def test_sticker_library_can_explicitly_disable_same_group_only(tmp_path: Path) -> None:
    root = tmp_path / "stickers"
    _write_manifest(
        root / "manifest.yaml",
        [
            {
                "id": "auto-01",
                "name": "无语猫",
                "file": "images/mock-01.gif",
                "approved": True,
                "source": "group_capture",
                "scope_groups": ["100000"],
            }
        ],
    )
    _write_gif(root / "images" / "mock-01.gif")
    from app.agent.sticker_library import StickerLibrary

    library = StickerLibrary(
        root,
        enabled=True,
        allowed_ids=["auto-01"],
        media_guard=MediaGuard(max_bytes=1024, max_pixels=10_000),
        same_group_only=False,
    )

    assert library.resolve_for_scene("auto-01", "200000") is not None

from __future__ import annotations

import json
import struct
from argparse import Namespace
from pathlib import Path

import scripts.sticker_maintenance as cli
from app.domain.types import now_ms
from app.storage.db import StateStore


def _gif_bytes() -> bytes:
    return b"GIF89a" + struct.pack("<HH", 1, 1) + b"\x00\x00\x00"


async def test_sticker_maintenance_cleans_explicit_old_rejected(tmp_path: Path) -> None:
    library_dir = tmp_path / "stickers"
    rejected = library_dir / "rejected"
    rejected.mkdir(parents=True)
    image_path = rejected / "old.gif"
    image_path.write_bytes(_gif_bytes())
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "storage": {
                    "dir": str(tmp_path / "state"),
                    "databasePath": str(tmp_path / "state" / "qqf.db"),
                },
                "stickerDir": str(library_dir),
                "safety": {"allowGroups": ["100000"]},
                "sticker": {
                    "ingest": {
                        "enabled": True,
                        "groups": {"100000": {"enabled": True}},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    store = StateStore(tmp_path / "state" / "qqf.db")
    await store.init()
    media_id = await store.upsert_inbound_media(
        sha256="a" * 64,
        mime_type="image/gif",
        byte_size=len(_gif_bytes()),
        width=1,
        height=1,
        local_path="rejected/old.gif",
        status="rejected",
        first_seen_at=now_ms() - 8 * 24 * 60 * 60 * 1000,
    )
    await store.touch_inbound_media_group(media_id, "100000")
    await store.close()

    result = await cli.run(
        Namespace(
            config=str(config_path),
            stats=True,
            clean_rejected=True,
            days=7,
        )
    )

    assert result["cleanedRejected"] == [media_id]
    assert not image_path.exists()
    store = StateStore(tmp_path / "state" / "qqf.db")
    await store.init()
    try:
        assert await store.get_inbound_media(media_id) is None
    finally:
        await store.close()

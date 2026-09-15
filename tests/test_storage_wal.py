from __future__ import annotations

import logging
from pathlib import Path

import pytest

import app.storage.db as storage_db
from app.storage.db import StateStore


async def test_clean_close_checkpoints_wal(tmp_path: Path) -> None:
    database = tmp_path / "wal.db"
    store = StateStore(database)
    await store.init()
    await store.append_wake_log("group:100000", "normal", "wal", "no_action")
    assert Path(f"{database}-wal").exists()

    await store.close()

    wal = Path(f"{database}-wal")
    assert not wal.exists() or wal.stat().st_size == 0


async def test_large_wal_logs_warning_on_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(storage_db, "_wal_size_bytes", lambda path: 1024)
    store = StateStore(tmp_path / "large-wal.db", wal_warn_bytes=512)

    with caplog.at_level(logging.WARNING, logger="qqf.storage"):
        await store.init()
    await store.close()

    assert "WAL size" in caplog.text

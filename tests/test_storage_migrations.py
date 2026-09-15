from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.storage.db import StateStore
from app.storage.migrations import Migration, backup_database, run_migrations, verify_database
from scripts.restore_db import restore_database


async def test_new_database_records_initial_migration(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "new.db")
    await store.init()
    try:
        conn = sqlite3.connect(tmp_path / "new.db")
        rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
        conn.close()
    finally:
        await store.close()

    assert rows == [(1, rows[0][1])]
    assert isinstance(rows[0][1], str) and rows[0][1]


async def test_legacy_database_migrates_with_backup_and_preserves_data(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    conn = sqlite3.connect(database)
    conn.execute(
        """
        CREATE TABLE wake_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            category TEXT NOT NULL,
            reason TEXT NOT NULL,
            decided TEXT NOT NULL,
            probability REAL,
            next_wake_at REAL,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO wake_log (session_id, category, reason, decided, created_at) VALUES (?, ?, ?, ?, ?)",
        ("group:100000", "mention", "legacy", "wake", 1.0),
    )
    conn.commit()
    conn.close()

    store = StateStore(database)
    await store.init()
    try:
        conn = sqlite3.connect(database)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(wake_log)")}
        row = conn.execute("SELECT prompt_tokens, completion_tokens, total_tokens FROM wake_log").fetchone()
        versions = conn.execute("SELECT version FROM schema_migrations").fetchall()
        conn.close()
    finally:
        await store.close()

    assert {"prompt_tokens", "completion_tokens", "total_tokens"} <= columns
    assert row == (0, 0, 0)
    assert versions == [(1,)]
    assert list((tmp_path / "backups").glob("legacy-*.db"))


def test_migration_failure_rolls_back_and_keeps_version_zero(tmp_path: Path) -> None:
    database = tmp_path / "failed.db"

    def fail(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE should_rollback (id INTEGER PRIMARY KEY)")
        raise RuntimeError("migration failed")

    with pytest.raises(RuntimeError, match="migration failed"):
        run_migrations(database, [Migration(1, "fail", fail)])

    conn = sqlite3.connect(database)
    versions = conn.execute("SELECT version FROM schema_migrations").fetchall()
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='should_rollback'"
    ).fetchone()
    conn.close()

    assert versions == []
    assert table is None


def test_backup_and_restore_are_verified_and_require_force(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE sample (value TEXT)")
    conn.execute("INSERT INTO sample VALUES ('before')")
    conn.commit()
    conn.close()

    backup = backup_database(source, tmp_path / "backups")
    assert verify_database(backup) == "ok"

    target = tmp_path / "target.db"
    conn = sqlite3.connect(target)
    conn.execute("CREATE TABLE sample (value TEXT)")
    conn.execute("INSERT INTO sample VALUES ('existing')")
    conn.commit()
    conn.close()
    with pytest.raises(FileExistsError):
        restore_database(backup, target, force=False)

    conn = sqlite3.connect(source)
    conn.execute("UPDATE sample SET value = 'after'")
    conn.commit()
    conn.close()

    result = restore_database(backup, target, force=True)
    assert result["integrity_check"] == "ok"
    conn = sqlite3.connect(target)
    assert conn.execute("SELECT value FROM sample").fetchone()[0] == "before"
    conn.close()

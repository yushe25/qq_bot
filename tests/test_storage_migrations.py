from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.storage.db import StateStore, _apply_sticker_ingest_group_review_schema
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

    assert [row[0] for row in rows] == [1, 2, 3, 4]
    assert all(isinstance(row[1], str) and row[1] for row in rows)


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
        tables = {item[0] for item in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
    finally:
        await store.close()

    assert {"prompt_tokens", "completion_tokens", "total_tokens"} <= columns
    assert row == (0, 0, 0)
    assert versions == [(1,), (2,), (3,), (4,)]
    assert "inbound_media" in tables
    assert list((tmp_path / "backups").glob("legacy-*.db"))


def test_group_review_migration_backfills_existing_media_status(tmp_path: Path) -> None:
    database = tmp_path / "group-review.db"
    conn = sqlite3.connect(database)
    conn.execute(
        """
        CREATE TABLE inbound_media (
            id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            reviewed_at REAL,
            reviewed_by TEXT,
            reject_reason TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE inbound_media_groups (
            media_id INTEGER NOT NULL,
            group_id TEXT NOT NULL,
            first_seen_at REAL NOT NULL,
            last_seen_at REAL NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (media_id, group_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO inbound_media (id, status, reviewed_at, reviewed_by, reject_reason) VALUES (?, ?, ?, ?, ?)",
        (1, "approved", 10.0, "console", None),
    )
    conn.execute(
        "INSERT INTO inbound_media (id, status, reviewed_at, reviewed_by, reject_reason) VALUES (?, ?, ?, ?, ?)",
        (2, "rejected", 20.0, "console", "bad"),
    )
    conn.execute(
        "INSERT INTO inbound_media_groups (media_id, group_id, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)",
        (1, "100000", 1.0, 1.0),
    )
    conn.execute(
        "INSERT INTO inbound_media_groups (media_id, group_id, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)",
        (2, "200000", 2.0, 2.0),
    )
    conn.commit()
    conn.close()

    run_migrations(
        database,
        [
            Migration(
                4,
                "sticker_ingest_group_review",
                _apply_sticker_ingest_group_review_schema,
            )
        ],
    )

    conn = sqlite3.connect(database)
    rows = conn.execute(
        "SELECT group_id, review_status, reviewed_at, reviewed_by, reject_reason "
        "FROM inbound_media_groups ORDER BY group_id"
    ).fetchall()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(inbound_media_groups)")}
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(inbound_media_groups)")}
    conn.close()

    assert {"review_status", "reviewed_at", "reviewed_by", "reject_reason"} <= columns
    assert "idx_media_groups_review" in indexes
    assert rows == [
        ("100000", "approved", 10.0, "console", None),
        ("200000", "rejected", 20.0, "console", "bad"),
    ]


async def test_group_review_state_is_isolated_by_group(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "isolation.db")
    await store.init()
    try:
        media_id = await store.upsert_inbound_media(
            sha256="a" * 64,
            mime_type="image/gif",
            byte_size=1,
            width=1,
            height=1,
            local_path="inbox/a.gif",
            status="pending_review",
            first_seen_at=1.0,
        )
        await store.touch_inbound_media_group(media_id, "100000", 1.0)
        await store.touch_inbound_media_group(media_id, "200000", 1.0)
        assert await store.update_inbound_media_group_review(
            media_id,
            "100000",
            "approved",
            reviewed_by="console",
        )
        groups = await store.get_inbound_media_groups(media_id)
    finally:
        await store.close()

    by_group = {str(row["group_id"]): row for row in groups}
    assert by_group["100000"]["review_status"] == "approved"
    assert by_group["200000"]["review_status"] == "pending_review"


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

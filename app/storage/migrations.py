from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("qqf.storage.migrations")

DEFAULT_BACKUP_KEEP = 7


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]

    @property
    def checksum(self) -> str:
        payload = f"{self.version}:{self.name}:{self.apply.__module__}.{self.apply.__qualname__}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run_migrations(
    database_path: str | Path,
    migrations: Sequence[Migration],
    *,
    backup_dir: str | Path | None = None,
    backup_keep: int = DEFAULT_BACKUP_KEEP,
) -> int:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        had_schema = _has_user_tables(conn)
        _ensure_migration_table(conn)
        applied = _applied_migrations(conn)
        pending = [item for item in sorted(migrations, key=lambda item: item.version) if item.version not in applied]
        if not pending:
            return _current_version(conn)

        if had_schema and backup_dir is not None:
            backup_path = backup_database(path, backup_dir, keep=backup_keep, connection=conn)
            logger.info("SQLite migration backup created: %s", backup_path.name)

        for migration in pending:
            _apply_migration(conn, migration)
        integrity = _integrity_check(conn)
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check failed after migrations: {integrity}")
        return _current_version(conn)
    except Exception:
        logger.exception("SQLite migration failed; original database was preserved")
        raise
    finally:
        conn.close()


def backup_database(
    database_path: str | Path,
    backup_dir: str | Path,
    *,
    keep: int = DEFAULT_BACKUP_KEEP,
    connection: sqlite3.Connection | None = None,
) -> Path:
    source_path = Path(database_path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    target_dir = Path(backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{source_path.stem}-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000}.db"

    source_conn = connection or sqlite3.connect(source_path)
    target_conn = sqlite3.connect(target_path)
    try:
        source_conn.backup(target_conn)
        integrity = _integrity_check(target_conn)
        if integrity != "ok":
            raise RuntimeError(f"backup integrity_check failed: {integrity}")
    finally:
        target_conn.close()
        if connection is None:
            source_conn.close()
    _prune_backups(target_dir, source_path.stem, keep)
    return target_path


def verify_database(path: str | Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return _integrity_check(conn)
    finally:
        conn.close()


def _ensure_migration_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            checksum TEXT NOT NULL,
            applied_at REAL NOT NULL
        )
        """
    )
    conn.commit()


def _applied_migrations(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    return {int(row["version"]): str(row["checksum"]) for row in rows}


def _current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
    return int(row["version"]) if row is not None and row["version"] is not None else 0


def _apply_migration(conn: sqlite3.Connection, migration: Migration) -> None:
    applied = _applied_migrations(conn)
    existing_checksum = applied.get(migration.version)
    if existing_checksum is not None:
        if existing_checksum != migration.checksum:
            raise RuntimeError(f"migration {migration.version} checksum mismatch")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        migration.apply(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version, checksum, applied_at) VALUES (?, ?, ?)",
            (migration.version, migration.checksum, time.time()),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _has_user_tables(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def _integrity_check(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row is not None else "unknown"


def _prune_backups(target_dir: Path, stem: str, keep: int) -> None:
    if keep <= 0:
        return
    backups = sorted(
        target_dir.glob(f"{stem}-*.db"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale in backups[keep:]:
        stale.unlink(missing_ok=True)

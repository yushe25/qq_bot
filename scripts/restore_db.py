from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.storage.migrations import backup_database, verify_database


def restore_database(backup_path: Path, database_path: Path, *, force: bool = False) -> dict[str, str]:
    if not backup_path.is_file():
        raise FileNotFoundError(backup_path)
    integrity = verify_database(backup_path)
    if integrity != "ok":
        raise RuntimeError(f"backup integrity_check failed: {integrity}")
    if database_path.exists() and not force:
        raise FileExistsError("target database exists; pass --force to replace it")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    pre_restore = ""
    if database_path.exists():
        pre_restore = str(backup_database(database_path, database_path.parent / "backups"))

    temp_path = database_path.with_name(f".{database_path.name}.{uuid.uuid4().hex}.restore")
    try:
        shutil.copy2(backup_path, temp_path)
        os.replace(temp_path, database_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    restored_integrity = verify_database(database_path)
    if restored_integrity != "ok":
        raise RuntimeError(f"restored database integrity_check failed: {restored_integrity}")
    return {
        "restored": str(database_path),
        "integrity_check": restored_integrity,
        "pre_restore_backup": pre_restore,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="恢复 SQLite 备份")
    parser.add_argument("--backup", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--force", action="store_true", help="显式允许覆盖已存在的数据库")
    args = parser.parse_args()
    try:
        result = restore_database(
            Path(args.backup).resolve(),
            Path(args.database).resolve(),
            force=args.force,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"restore failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

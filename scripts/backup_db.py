from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.storage.migrations import backup_database, verify_database


def main() -> int:
    parser = argparse.ArgumentParser(description="创建 SQLite 在线备份")
    parser.add_argument("--database", default="state/qqf.db")
    parser.add_argument("--backup-dir", default="state/backups")
    parser.add_argument("--keep", type=int, default=7)
    args = parser.parse_args()

    source = Path(args.database).resolve()
    if not source.is_file():
        print(f"database not found: {source}", file=sys.stderr)
        return 1
    try:
        backup_path = backup_database(source, Path(args.backup_dir), keep=args.keep)
        integrity = verify_database(backup_path)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"backup": str(backup_path), "integrity_check": integrity}, ensure_ascii=False))
    return 0 if integrity == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

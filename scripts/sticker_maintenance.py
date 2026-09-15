from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import AppConfig
from app.domain.types import now_ms
from app.storage.db import StateStore


async def run(args: argparse.Namespace) -> dict[str, object]:
    config = AppConfig.load(args.config)
    store = StateStore(config.storage.database_path)
    await store.init()
    try:
        stats: dict[str, dict[str, int]] = {}
        for group_id, group_config in config.sticker.ingest.groups.items():
            usage = await store.inbound_media_group_stats(group_id)
            stats[group_id] = {
                **usage,
                "maxItems": group_config.max_items,
                "maxBytes": group_config.max_bytes,
            }
        cleaned: list[int] = []
        if args.clean_rejected:
            cutoff = now_ms() - max(0, int(args.days)) * 24 * 60 * 60 * 1000
            rows = await store.list_rejected_inbound_media(cutoff)
            library_dir = Path(config.sticker_dir).resolve()
            for row in rows:
                path = (library_dir / str(row["local_path"])).resolve()
                try:
                    path.relative_to(library_dir)
                except ValueError:
                    continue
                path.unlink(missing_ok=True)
                await store.delete_inbound_media(int(row["id"]))
                cleaned.append(int(row["id"]))
        return {"stats": stats, "cleanedRejected": cleaned}
    finally:
        await store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="QQF 表情素材维护")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--clean-rejected", action="store_true")
    parser.add_argument("--days", type=int, default=7, help="rejected 文件保留天数")
    args = parser.parse_args()
    if not args.stats and not args.clean_rejected:
        parser.error("至少指定 --stats 或 --clean-rejected")
    try:
        result = asyncio.run(run(args))
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"sticker maintenance failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

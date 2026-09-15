from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.providers.vision_openai_compatible import build_vision_provider
from app.agent.sticker_manifest import StickerManifestError, load_manifest
from app.agent.sticker_tagger import StickerTagger, StickerTagResult
from app.config import AppConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="使用独立视觉模型自动标注本地表情素材")
    parser.add_argument("--config", default="config.json", help="配置文件，默认 config.json")
    parser.add_argument("--id", action="append", dest="ids", default=[], help="素材 id，可重复指定")
    parser.add_argument("--all", action="store_true", help="标注 allowedIds 中所有 approved 素材")
    parser.add_argument("--force", action="store_true", help="覆盖人工标注或相同 hash")
    parser.add_argument("--dry-run", action="store_true", help="只调用模型并显示结果，不写 manifest")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if bool(args.ids) == bool(args.all):
        print("请且只指定 --id 或 --all", file=sys.stderr)
        return 2
    config = AppConfig.load(args.config)
    if not config.sticker.auto_tag.enabled:
        print("sticker.autoTag.enabled=false，未执行标注", file=sys.stderr)
        return 1
    library_dir = Path(config.sticker_dir)
    try:
        sticker_ids = _select_ids(config, library_dir) if args.all else _unique(args.ids)
    except StickerManifestError:
        print("读取 manifest 失败，请检查 stickerDir 与 manifest.yaml", file=sys.stderr)
        return 1
    if not sticker_ids:
        print("没有可标注的素材", file=sys.stderr)
        return 1
    try:
        provider = build_vision_provider(config.sticker.auto_tag)
    except ValueError:
        print("视觉 Provider 配置不可用，请检查 autoTag provider 与 apiKeyEnv", file=sys.stderr)
        return 1

    tagger = StickerTagger(
        library_dir,
        config.sticker.auto_tag,
        provider,
        dry_run=args.dry_run,
    )
    try:
        entries = asyncio.run(tagger.tag_many(sticker_ids, force=args.force))
    except StickerManifestError:
        print("标注命令未启动：同一素材目录已有标注任务在运行", file=sys.stderr)
        return 1
    del entries

    failed = [result for result in tagger.last_results if result.status == "failed"]
    for result in tagger.last_results:
        print(f"{result.sticker_id}: {_status_text(result)}")
    if failed:
        print("失败 id: " + ", ".join(result.sticker_id for result in failed), file=sys.stderr)
    if args.dry_run:
        print("dry-run 完成，未修改 manifest")
    else:
        print("manifest 已更新，请重启 QQF 使其生效")
    return 1 if failed else 0


def _select_ids(config: AppConfig, library_dir: Path) -> list[str]:
    allowed = set(config.sticker.allowed_ids)
    entries = load_manifest(library_dir / "manifest.yaml")
    return [entry.id for entry in entries if entry.approved and entry.id in allowed]


def _unique(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in result:
            result.append(item)
    return result


def _status_text(result: StickerTagResult) -> str:
    return {
        "updated": "updated",
        "skipped_manual": "skipped (manual)",
        "skipped_hash": "skipped (hash unchanged)",
        "failed": "failed",
    }[result.status]


if __name__ == "__main__":
    raise SystemExit(main())

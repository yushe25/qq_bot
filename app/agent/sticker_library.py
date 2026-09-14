from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.safety.media_guard import MediaGuard
from app.storage.db import StateStore


@dataclass(frozen=True)
class StickerFile:
    sticker_id: str
    name: str
    path: Path
    tags: tuple[str, ...]


class StickerLibrary:
    """Whitelisted local sticker library backed by manifest.yaml plus SQLite usage counts."""

    def __init__(
        self,
        library_dir: str | Path,
        enabled: bool = False,
        media_guard: MediaGuard | None = None,
        store: StateStore | None = None,
    ) -> None:
        self.library_dir = Path(library_dir).resolve()
        self.enabled = enabled
        self.media_guard = media_guard or MediaGuard(
            max_bytes=8 * 1024 * 1024,
            max_pixels=40_960_000,
        )
        self.store = store
        self.error: str | None = None
        self._entries: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        manifest_path = self.library_dir / "manifest.yaml"
        if not manifest_path.is_file():
            self.error = "manifest.yaml 不存在"
            return
        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            self.error = f"manifest.yaml 解析失败: {exc}"
            return
        if not isinstance(raw, list):
            self.error = "manifest.yaml 必须是条目列表"
            return
        entries: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                self.error = f"manifest.yaml 第 {index + 1} 条不是对象"
                return
            sticker_id = str(item.get("id", "")).strip()
            if not sticker_id:
                self.error = f"manifest.yaml 第 {index + 1} 条缺少 id"
                return
            if sticker_id in entries:
                self.error = f"manifest.yaml id 重复: {sticker_id}"
                return
            file_value = item.get("file")
            if not isinstance(file_value, str) or not file_value.strip():
                self.error = f"表情 {sticker_id} 缺少 file"
                return
            file_path = Path(file_value)
            if file_path.is_absolute() or ".." in file_path.parts:
                self.error = f"表情 {sticker_id} 的 file 路径不合法"
                return
            entries[sticker_id] = dict(item)
        self._entries = entries

    @property
    def available(self) -> bool:
        return self.enabled and self.error is None and bool(self._entries)

    def list_ids(self) -> list[str]:
        return list(self._entries) if self.available else []

    def resolve(self, sticker_id: str) -> StickerFile | None:
        if not self.enabled or self.error or sticker_id not in self._entries:
            return None
        entry = self._entries[sticker_id]
        if entry.get("approved") is not True:
            return None
        target = (self.library_dir / str(entry["file"])).resolve()
        try:
            target.relative_to(self.library_dir)
        except ValueError:
            return None
        if not target.is_file():
            return None
        ok, _ = self.media_guard.check_file(target)
        if not ok:
            return None
        tags = entry.get("tags") or []
        return StickerFile(
            sticker_id=sticker_id,
            name=str(entry.get("name") or sticker_id),
            path=target,
            tags=tuple(str(tag) for tag in tags),
        )

    async def usage_count(self, sticker_id: str) -> int:
        base = 0
        entry = self._entries.get(sticker_id)
        if entry is not None:
            base = int(entry.get("usage_count") or 0)
        if self.store is None:
            return base
        return base + await self.store.get_sticker_usage(sticker_id)

    async def record_usage(self, sticker_id: str) -> None:
        if self.store is not None:
            await self.store.increment_sticker_usage(sticker_id)

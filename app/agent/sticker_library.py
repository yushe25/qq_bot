from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.agent.sticker_manifest import StickerManifestEntry, StickerManifestError, load_manifest
from app.safety.media_guard import MediaGuard
from app.storage.db import StateStore


@dataclass(frozen=True)
class StickerFile:
    sticker_id: str
    name: str
    path: Path
    tags: tuple[str, ...]
    description: str = ""
    moods: tuple[str, ...] = ()
    scenes: tuple[str, ...] = ()
    avoid_scenes: tuple[str, ...] = ()


@dataclass(frozen=True)
class StickerCatalogEntry:
    sticker_id: str
    name: str
    description: str
    tags: tuple[str, ...]
    moods: tuple[str, ...]
    scenes: tuple[str, ...]
    avoid_scenes: tuple[str, ...]


class StickerLibrary:
    """Whitelisted local sticker library backed by manifest.yaml plus SQLite usage counts."""

    def __init__(
        self,
        library_dir: str | Path,
        enabled: bool = False,
        allowed_ids: Sequence[str] = (),
        media_guard: MediaGuard | None = None,
        store: StateStore | None = None,
        same_group_only: bool = True,
    ) -> None:
        self.library_dir = Path(library_dir).resolve()
        self.enabled = enabled
        self.allowed_ids = tuple(str(item).strip() for item in allowed_ids if str(item).strip())
        self.media_guard = media_guard or MediaGuard(
            max_bytes=8 * 1024 * 1024,
            max_pixels=40_960_000,
        )
        self.store = store
        self.same_group_only = same_group_only
        self.error: str | None = None
        self._entries: dict[str, StickerManifestEntry] = {}
        self._load()

    def _load(self) -> None:
        manifest_path = self.library_dir / "manifest.yaml"
        if not manifest_path.is_file():
            self.error = "manifest.yaml 不存在"
            return
        try:
            entries = load_manifest(manifest_path)
        except StickerManifestError as exc:
            self.error = str(exc)
            return
        self._entries = {entry.id: entry for entry in entries}

    @property
    def available(self) -> bool:
        return (
            self.enabled
            and bool(self.allowed_ids)
            and self.error is None
            and bool(self._entries)
        )

    def list_ids(self) -> list[str]:
        if not self.available:
            return []
        allowed = set(self.allowed_ids)
        return [sticker_id for sticker_id in self._entries if sticker_id in allowed]

    def resolve(self, sticker_id: str) -> StickerFile | None:
        if (
            not self.enabled
            or not self.allowed_ids
            or self.error
            or sticker_id not in self._entries
            or sticker_id not in set(self.allowed_ids)
        ):
            return None
        entry = self._entries[sticker_id]
        if not entry.approved:
            return None
        target = (self.library_dir / entry.file).resolve()
        try:
            target.relative_to(self.library_dir)
        except ValueError:
            return None
        if not target.is_file():
            return None
        ok, _ = self.media_guard.check_file(target)
        if not ok:
            return None
        return StickerFile(
            sticker_id=sticker_id,
            name=entry.name or sticker_id,
            path=target,
            tags=tuple(entry.tags),
            description=entry.description,
            moods=tuple(entry.moods),
            scenes=tuple(entry.scenes),
            avoid_scenes=tuple(entry.avoid_scenes),
        )

    def catalog(self) -> tuple[StickerCatalogEntry, ...]:
        entries: list[StickerCatalogEntry] = []
        for sticker_id in self.list_ids():
            sticker = self.resolve(sticker_id)
            if sticker is None:
                continue
            entries.append(
                StickerCatalogEntry(
                    sticker_id=sticker.sticker_id,
                    name=sticker.name,
                    description=sticker.description,
                    tags=sticker.tags,
                    moods=sticker.moods,
                    scenes=sticker.scenes,
                    avoid_scenes=sticker.avoid_scenes,
                )
            )
        return tuple(entries)

    def catalog_for_scene(self, scene_id: str) -> tuple[StickerCatalogEntry, ...]:
        return tuple(
            entry
            for entry in self.catalog()
            if self._scene_allowed(self._entries.get(entry.sticker_id), scene_id)
        )

    def catalog_text_for_scene(
        self,
        scene_id: str,
        max_chars: int = 4000,
        experience_by_id: dict[str, str] | None = None,
    ) -> str:
        return self._catalog_text(
            self.catalog_for_scene(scene_id),
            max_chars=max_chars,
            experience_by_id=experience_by_id,
        )

    def resolve_for_scene(self, sticker_id: str, scene_id: str) -> StickerFile | None:
        entry = self._entries.get(sticker_id)
        if not self._scene_allowed(entry, scene_id):
            return None
        return self.resolve(sticker_id)

    def catalog_text(self, max_chars: int = 4000) -> str:
        return self._catalog_text(self.catalog(), max_chars=max_chars)

    def _catalog_text(
        self,
        entries: tuple[StickerCatalogEntry, ...],
        *,
        max_chars: int,
        experience_by_id: dict[str, str] | None = None,
    ) -> str:
        limit = max(0, int(max_chars))
        if limit == 0:
            return ""
        lines: list[str] = []
        used = 0
        for entry in entries:
            fields = [entry.sticker_id, entry.name]
            if entry.tags:
                fields.append("标签：" + "、".join(entry.tags))
            if entry.description:
                fields.append("说明：" + entry.description)
            if entry.moods:
                fields.append("情绪：" + "、".join(entry.moods))
            if entry.scenes:
                fields.append("适合：" + "、".join(entry.scenes))
            if entry.avoid_scenes:
                fields.append("不适合：" + "、".join(entry.avoid_scenes))
            experience = (experience_by_id or {}).get(entry.sticker_id, "")
            if experience:
                fields.append("群内经验：" + experience)
            line = "- " + " | ".join(fields)
            separator = "\n" if lines else ""
            remaining = limit - used - len(separator)
            if remaining <= 0:
                break
            if len(line) <= remaining:
                lines.append(line)
                used += len(separator) + len(line)
                continue
            if remaining > 1:
                lines.append(line[: remaining - 1] + "…")
            break
        return "\n".join(lines)

    def media_id_for(self, sticker_id: str) -> int | None:
        entry = self._entries.get(sticker_id)
        return entry.media_id if entry is not None else None

    async def usage_count(self, sticker_id: str) -> int:
        base = 0
        entry = self._entries.get(sticker_id)
        if entry is not None:
            base = entry.usage_count
        if self.store is None:
            return base
        return base + await self.store.get_sticker_usage(sticker_id)

    async def record_usage(self, sticker_id: str) -> None:
        if self.store is not None:
            await self.store.increment_sticker_usage(sticker_id)

    def _scene_allowed(self, entry: StickerManifestEntry | None, scene_id: str) -> bool:
        if entry is None or not self.same_group_only or not entry.scope_groups:
            return True
        return scene_id in entry.scope_groups

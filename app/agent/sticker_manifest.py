from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.sticker_limits import MAX_STICKER_DESCRIPTION_CHARS, MAX_STICKER_TAGS


class StickerManifestError(ValueError):
    pass


class StickerManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1)
    name: str = Field(default="", max_length=80)
    file: str = Field(min_length=1)
    approved: bool = False
    usage_count: int = Field(default=0, ge=0)
    description: str = Field(default="", max_length=MAX_STICKER_DESCRIPTION_CHARS)
    tags: list[str] = Field(default_factory=list, max_length=MAX_STICKER_TAGS)
    moods: list[str] = Field(default_factory=list, max_length=MAX_STICKER_TAGS)
    scenes: list[str] = Field(default_factory=list, max_length=MAX_STICKER_TAGS)
    avoid_scenes: list[str] = Field(default_factory=list, max_length=MAX_STICKER_TAGS)
    tag_source: Literal["manual", "auto", "legacy"] = "legacy"
    tagged_at: str | None = None
    content_hash: str | None = None
    tag_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("id must not be blank")
        return value

    @field_validator("name", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("file must not be blank")
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or re.match(r"^[A-Za-z]:", normalized) or ".." in path.parts:
            raise ValueError("file path must stay inside the sticker library")
        return value

    @field_validator("tags", "moods", "scenes", "avoid_scenes")
    @classmethod
    def validate_string_list(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("semantic list entries must not be blank")
        return cleaned


def load_manifest(path: Path) -> list[StickerManifestEntry]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise StickerManifestError(f"manifest.yaml 解析失败: {exc}") from exc
    except OSError as exc:
        raise StickerManifestError(f"manifest.yaml 读取失败: {exc}") from exc
    if not isinstance(raw, list):
        raise StickerManifestError("manifest.yaml 必须是条目列表")

    entries: list[StickerManifestEntry] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise StickerManifestError(f"manifest.yaml 第 {index} 条不是对象")
        try:
            entry = StickerManifestEntry.model_validate(item)
        except ValidationError as exc:
            if any(error.get("loc") == ("file",) for error in exc.errors()):
                raise StickerManifestError(f"manifest.yaml 第 {index} 条 file 路径不合法: {exc}") from exc
            raise StickerManifestError(f"manifest.yaml 第 {index} 条字段不合法: {exc}") from exc
        if entry.id in seen:
            raise StickerManifestError(f"manifest.yaml id 重复: {entry.id}")
        seen.add(entry.id)
        entries.append(entry)
    return entries


def save_manifest_atomic(path: Path, entries: list[StickerManifestEntry]) -> None:
    payload = [entry.model_dump(mode="json", exclude_none=True) for entry in entries]
    text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))

    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def file_content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


@contextmanager
def manifest_lock(path: Path) -> Iterator[None]:
    lock_path = path.parent / ".tag.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise StickerManifestError(f"标注命令已在运行: {lock_path}") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
    finally:
        os.close(descriptor)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)

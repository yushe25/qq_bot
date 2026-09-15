from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
import yaml

from app.agent.sticker_manifest import (
    StickerManifestEntry,
    StickerManifestError,
    file_content_hash,
    load_manifest,
    manifest_lock,
    save_manifest_atomic,
)


def _entry(**overrides: object) -> StickerManifestEntry:
    values: dict[str, object] = {
        "id": "pilot-01",
        "name": "无语猫",
        "file": "images/pilot-01.gif",
        "approved": True,
        "usage_count": 0,
        "tags": ["无语"],
    }
    values.update(overrides)
    return StickerManifestEntry.model_validate(values)


def test_load_manifest_keeps_legacy_entries_compatible(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text(
        "- id: pilot-01\n"
        "  name: 无语猫\n"
        "  file: images/pilot-01.gif\n"
        "  approved: true\n"
        "  usage_count: 0\n"
        "  tags: [无语]\n",
        encoding="utf-8",
    )

    entries = load_manifest(path)

    assert len(entries) == 1
    assert entries[0].id == "pilot-01"
    assert entries[0].tag_source == "legacy"
    assert entries[0].description == ""


def test_save_manifest_atomic_writes_backup_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text("old: value\n", encoding="utf-8")
    entry = _entry(description="测试说明", scenes=["接梗时"], tag_source="auto")

    save_manifest_atomic(path, [entry])

    assert load_manifest(path)[0] == entry
    assert path.with_name("manifest.yaml.bak").read_text(encoding="utf-8") == "old: value\n"
    assert yaml.safe_load(path.read_text(encoding="utf-8"))[0]["tag_source"] == "auto"


def test_load_manifest_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text(
        "- id: pilot-01\n"
        "  name: one\n"
        "  file: images/one.gif\n"
        "  approved: true\n"
        "  usage_count: 0\n"
        "- id: pilot-01\n"
        "  name: two\n"
        "  file: images/two.gif\n"
        "  approved: true\n"
        "  usage_count: 0\n",
        encoding="utf-8",
    )

    with pytest.raises(StickerManifestError, match="id 重复"):
        load_manifest(path)


@pytest.mark.parametrize("value", ["../outside.gif", "C:/outside.gif", "/absolute.gif"])
def test_manifest_entry_rejects_escaping_file_paths(value: str) -> None:
    with pytest.raises(ValueError, match="file path"):
        _entry(file=value)


def test_manifest_entry_rejects_invalid_field_types() -> None:
    with pytest.raises(ValueError):
        _entry(usage_count="1")
    with pytest.raises(ValueError):
        _entry(tags=[1])


def test_save_manifest_atomic_preserves_original_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "manifest.yaml"
    original = "original: true\n"
    path.write_text(original, encoding="utf-8")

    def fail_replace(source: Path, target: Path) -> None:
        del source, target
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        save_manifest_atomic(path, [_entry()])

    assert path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.tmp"))


def test_file_content_hash_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "image.gif"
    data = b"GIF89a-test"
    path.write_bytes(data)

    assert file_content_hash(path) == f"sha256:{hashlib.sha256(data).hexdigest()}"


def test_manifest_lock_rejects_concurrent_writer(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"

    with manifest_lock(path):  # noqa: SIM117
        with pytest.raises(StickerManifestError, match="已在运行"):
            with manifest_lock(path):
                pass

    assert not (tmp_path / ".tag.lock").exists()

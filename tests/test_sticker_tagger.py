from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.agent.sticker_manifest import StickerManifestEntry, file_content_hash, load_manifest
from app.agent.sticker_tagger import StickerTagError, StickerTagger
from app.config import StickerAutoTagConfig, StickerAutoTagProviderConfig


class _SequenceVision:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def describe(self, image_path: Path, *, prompt: str) -> str:
        del image_path, prompt
        self.calls += 1
        return self.responses.pop(0)


def _gif_bytes() -> bytes:
    return b"GIF89a" + struct.pack("<HH", 1, 1) + b"\x00\x00\x00"


def _entry() -> StickerManifestEntry:
    return StickerManifestEntry.model_validate(
        {
            "id": "pilot-01",
            "name": "旧名称",
            "file": "images/pilot-01.gif",
            "approved": True,
            "usage_count": 3,
            "tag_source": "legacy",
        }
    )


def _setup(tmp_path: Path, entries: list[StickerManifestEntry]) -> Path:
    library_dir = tmp_path / "stickers"
    image_dir = library_dir / "images"
    image_dir.mkdir(parents=True)
    (image_dir / "pilot-01.gif").write_bytes(_gif_bytes())
    manifest_path = library_dir / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(
        [entry.model_dump(mode="json", exclude_none=True) for entry in entries],
        allow_unicode=True,
        sort_keys=False,
    ), encoding="utf-8")
    return library_dir


def _config() -> StickerAutoTagConfig:
    return StickerAutoTagConfig(
        enabled=True,
        provider="vision-main",
        providers={
            "vision-main": StickerAutoTagProviderConfig(
                base_url="https://vision.example/v1",
                api_key_env="QQF_VISION_API_KEY",
                model="vision-model",
            )
        },
        max_tags=3,
        max_description_chars=30,
    )


def _draft(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "name": "无语猫",
        "description": "白猫翻白眼表达无语",
        "tags": ["无语", "猫"],
        "moods": ["无语"],
        "scenes": ["被吐槽时"],
        "avoid_scenes": ["严肃道歉时"],
        "confidence": 0.84,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


async def test_tag_one_writes_semantic_metadata_and_preserves_approval(tmp_path: Path) -> None:
    library_dir = _setup(tmp_path, [_entry()])
    provider = _SequenceVision([_draft()])
    tagger = StickerTagger(library_dir, _config(), provider)

    result = await tagger.tag_one("pilot-01")

    assert result.name == "无语猫"
    assert result.description == "白猫翻白眼表达无语"
    assert result.tag_source == "auto"
    assert result.tag_confidence == 0.84
    assert result.content_hash == file_content_hash(library_dir / "images" / "pilot-01.gif")
    assert result.approved is True
    assert result.usage_count == 3
    assert provider.calls == 1
    assert tagger.last_results[0].status == "updated"
    assert load_manifest(library_dir / "manifest.yaml")[0] == result
    assert not (library_dir / ".tag.lock").exists()


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        _draft(name="x" * 81),
        _draft(description="x" * 31),
        _draft(tags=[f"tag-{index}" for index in range(4)]),
        _draft(description="[CQ:image,file=http://evil]"),
        _draft(description="联系 13800138000"),
    ],
)
async def test_tag_one_rejects_invalid_draft_without_overwriting(tmp_path: Path, raw: str) -> None:
    library_dir = _setup(tmp_path, [_entry()])
    manifest_path = library_dir / "manifest.yaml"
    before = manifest_path.read_text(encoding="utf-8")
    tagger = StickerTagger(library_dir, _config(), _SequenceVision([raw]))

    with pytest.raises(StickerTagError):
        await tagger.tag_one("pilot-01")

    assert manifest_path.read_text(encoding="utf-8") == before
    assert tagger.last_results[0].status == "failed"


async def test_tag_one_skips_manual_and_unchanged_hash(tmp_path: Path) -> None:
    library_dir = _setup(tmp_path, [_entry()])
    image_hash = file_content_hash(library_dir / "images" / "pilot-01.gif")
    entries = load_manifest(library_dir / "manifest.yaml")
    entries[0] = entries[0].model_copy(update={"tag_source": "manual", "content_hash": image_hash})
    (library_dir / "manifest.yaml").write_text(yaml.safe_dump(
        [entry.model_dump(mode="json", exclude_none=True) for entry in entries],
        allow_unicode=True,
        sort_keys=False,
    ), encoding="utf-8")
    provider = _SequenceVision([_draft()])
    tagger = StickerTagger(library_dir, _config(), provider)

    result = await tagger.tag_one("pilot-01")

    assert result.tag_source == "manual"
    assert provider.calls == 0
    assert tagger.last_results[0].status == "skipped_manual"

    entries[0] = entries[0].model_copy(update={"tag_source": "auto"})
    (library_dir / "manifest.yaml").write_text(yaml.safe_dump(
        [entry.model_dump(mode="json", exclude_none=True) for entry in entries],
        allow_unicode=True,
        sort_keys=False,
    ), encoding="utf-8")
    result = await tagger.tag_one("pilot-01")

    assert result.tag_source == "auto"
    assert provider.calls == 0
    assert tagger.last_results[0].status == "skipped_hash"


async def test_tag_one_force_overrides_manual_metadata(tmp_path: Path) -> None:
    library_dir = _setup(tmp_path, [_entry()])
    entries = load_manifest(library_dir / "manifest.yaml")
    entries[0] = entries[0].model_copy(update={"tag_source": "manual"})
    (library_dir / "manifest.yaml").write_text(yaml.safe_dump(
        [entry.model_dump(mode="json", exclude_none=True) for entry in entries],
        allow_unicode=True,
        sort_keys=False,
    ), encoding="utf-8")
    provider = _SequenceVision([_draft()])
    tagger = StickerTagger(library_dir, _config(), provider)

    result = await tagger.tag_one("pilot-01", force=True)

    assert result.tag_source == "auto"
    assert provider.calls == 1


async def test_tag_many_keeps_successes_and_reports_failures(tmp_path: Path) -> None:
    second = StickerManifestEntry.model_validate(
        {
            "id": "pilot-02",
            "name": "旧二",
            "file": "images/pilot-02.gif",
            "approved": True,
        }
    )
    library_dir = _setup(tmp_path, [_entry(), second])
    (library_dir / "images" / "pilot-02.gif").write_bytes(_gif_bytes())
    provider = _SequenceVision(["bad json", _draft(name="开心猫")])
    tagger = StickerTagger(library_dir, _config(), provider)

    entries = await tagger.tag_many(["pilot-01", "pilot-02"])

    assert [entry.id for entry in entries] == ["pilot-02"]
    assert [result.status for result in tagger.last_results] == ["failed", "updated"]
    persisted = {entry.id: entry for entry in load_manifest(library_dir / "manifest.yaml")}
    assert persisted["pilot-01"].tag_source == "legacy"
    assert persisted["pilot-02"].name == "开心猫"


async def test_dry_run_does_not_write_manifest(tmp_path: Path) -> None:
    library_dir = _setup(tmp_path, [_entry()])
    manifest_path = library_dir / "manifest.yaml"
    before = manifest_path.read_text(encoding="utf-8")
    tagger = StickerTagger(library_dir, _config(), _SequenceVision([_draft()]), dry_run=True)

    result = await tagger.tag_one("pilot-01")

    assert result.tag_source == "auto"
    assert manifest_path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "draft",
    [
        _draft(tags=[f"tag-{index}" for index in range(9)]),
        _draft(description="x" * 121),
    ],
)
async def test_tag_one_revalidates_manifest_limits_before_write(tmp_path: Path, draft: str) -> None:
    library_dir = _setup(tmp_path, [_entry()])
    manifest_path = library_dir / "manifest.yaml"
    before = manifest_path.read_text(encoding="utf-8")
    unsafe_config = _config().model_copy(update={"max_tags": 20, "max_description_chars": 500})
    tagger = StickerTagger(library_dir, unsafe_config, _SequenceVision([draft]))

    with pytest.raises(StickerTagError, match="manifest 上限"):
        await tagger.tag_one("pilot-01")

    assert manifest_path.read_text(encoding="utf-8") == before

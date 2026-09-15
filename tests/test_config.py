from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.config import AppConfig


def _auto_tag_payload() -> dict[str, Any]:
    return {
        "sticker": {
            "enabled": True,
            "allowed_ids": ["pilot-01"],
            "auto_tag": {
                "enabled": True,
                "provider": "vision-main",
                "fallbacks": ["vision-backup"],
                "providers": {
                    "vision-main": {
                        "base_url": "https://vision.example/v1",
                        "api_key_env": "QQF_VISION_API_KEY",
                        "model": "vision-model",
                    },
                    "vision-backup": {
                        "base_url": "http://127.0.0.1:9000/v1",
                        "api_key_env": "QQF_VISION_BACKUP_API_KEY",
                        "model": "backup-model",
                    },
                },
            },
        }
    }


def test_sticker_auto_tag_config_accepts_independent_provider_route() -> None:
    config = AppConfig.model_validate(_auto_tag_payload())

    assert config.sticker.auto_tag.enabled is True
    assert config.sticker.auto_tag.provider == "vision-main"
    assert config.sticker.auto_tag.fallbacks == ["vision-backup"]
    assert config.sticker.auto_tag.max_tags == 8
    assert config.sticker.auto_tag.providers["vision-main"].timeout_seconds == 120.0


def test_sticker_auto_tag_enabled_requires_provider() -> None:
    payload = _auto_tag_payload()
    payload["sticker"]["auto_tag"]["provider"] = ""

    with pytest.raises(ValidationError, match="requires provider"):
        AppConfig.model_validate(payload)


def test_sticker_auto_tag_rejects_duplicate_or_unknown_fallbacks() -> None:
    payload = _auto_tag_payload()
    payload["sticker"]["auto_tag"]["fallbacks"] = ["vision-backup", "vision-backup"]
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        AppConfig.model_validate(payload)

    payload = _auto_tag_payload()
    payload["sticker"]["auto_tag"]["fallbacks"] = ["missing"]
    with pytest.raises(ValidationError, match="references unknown provider"):
        AppConfig.model_validate(payload)


def test_sticker_auto_tag_rejects_plaintext_key() -> None:
    payload = _auto_tag_payload()
    payload["sticker"]["auto_tag"]["providers"]["vision-main"]["apiKey"] = "sk-secret"

    with pytest.raises(ValidationError):
        AppConfig.model_validate(payload)


def test_sticker_auto_tag_rejects_insecure_remote_url_and_reserved_header() -> None:
    payload = _auto_tag_payload()
    payload["sticker"]["auto_tag"]["providers"]["vision-main"]["base_url"] = "http://vision.example/v1"
    with pytest.raises(ValidationError, match="https"):
        AppConfig.model_validate(payload)

    payload = _auto_tag_payload()
    payload["sticker"]["auto_tag"]["providers"]["vision-main"]["extra_headers"] = {
        "Content-Type": "text/plain"
    }
    with pytest.raises(ValidationError, match="override"):
        AppConfig.model_validate(payload)


def test_config_example_parses_with_auto_tag_placeholders() -> None:
    path = Path(__file__).resolve().parents[1] / "config.example.json"

    config = AppConfig.load(path)

    assert config.sticker.auto_tag.provider == "vision-main"
    assert config.sticker.auto_tag.providers["vision-main"].api_key_env == "QQF_VISION_API_KEY"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_tags", 20),
        ("max_description_chars", 500),
    ],
)
def test_sticker_auto_tag_limits_cannot_exceed_manifest_hard_limits(field: str, value: int) -> None:
    payload = _auto_tag_payload()
    payload["sticker"]["auto_tag"][field] = value

    with pytest.raises(ValidationError):
        AppConfig.model_validate(payload)

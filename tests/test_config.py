from __future__ import annotations

import logging
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
    assert config.safety.min_segment_delay_ms == 400
    assert config.safety.max_segment_delay_ms == 1200


def test_segment_delay_range_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="minSegmentDelayMs"):
        AppConfig.model_validate(
            {
                "safety": {
                    "min_segment_delay_ms": 1200,
                    "max_segment_delay_ms": 400,
                }
            }
        )


def test_qq_transport_allows_loopback_by_default() -> None:
    config = AppConfig.model_validate(
        {
            "qq": {
                "ws_url": "ws://127.0.0.1:3001",
                "http_url": "http://localhost:3000",
            }
        }
    )

    assert config.qq.allow_remote is False


@pytest.mark.parametrize(
    "qq",
    [
        {"ws_url": "ws://192.168.1.20:3001"},
        {"http_url": "http://10.0.0.5:3000"},
    ],
)
def test_qq_transport_rejects_remote_by_default(qq: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="allowRemote=true"):
        AppConfig.model_validate({"qq": qq})


def test_qq_transport_allows_remote_only_with_explicit_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="qqf.config"):
        config = AppConfig.model_validate(
            {
                "qq": {
                    "ws_url": "wss://napcat.example/ws",
                    "http_url": "https://napcat.example",
                    "access_token": "must-not-appear",
                    "allow_remote": True,
                }
            }
        )

    assert config.qq.allow_remote is True
    assert "non-loopback OneBot transport" in caplog.text
    assert "must-not-appear" not in caplog.text


def test_qq_transport_rejects_invalid_scheme() -> None:
    with pytest.raises(ValidationError, match="absolute URL"):
        AppConfig.model_validate({"qq": {"ws_url": "http://127.0.0.1:3001"}})


def test_sticker_ingest_defaults_disabled_and_same_group_only() -> None:
    config = AppConfig()

    assert config.sticker.ingest.enabled is False
    assert config.sticker.ingest.same_group_only is True
    assert config.sticker.ingest.observation_before_messages == 8
    assert config.sticker.ingest.observation_after_messages == 8


def test_sticker_ingest_requires_group_in_safety_allowlist() -> None:
    with pytest.raises(ValidationError, match="safety.allowGroups"):
        AppConfig.model_validate(
            {
                "safety": {"allow_groups": ["100000"]},
                "sticker": {"ingest": {"groups": {"200000": {"enabled": True}}}},
            }
        )


def test_sticker_ingest_vision_requires_auto_tag() -> None:
    with pytest.raises(ValidationError, match="autoTag.enabled"):
        AppConfig.model_validate(
            {
                "safety": {"allow_groups": ["100000"]},
                "sticker": {
                    "ingest": {
                        "groups": {"100000": {"enabled": True, "vision_enabled": True}}
                    }
                },
            }
        )


@pytest.mark.parametrize(
    "group_config",
    [
        {"auto_approve_confidence": 1.1},
        {"max_items": 201},
        {"max_bytes": 2 * 1024 * 1024 * 1024 + 1},
    ],
)
def test_sticker_ingest_group_limits_are_bounded(group_config: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
                "safety": {"allow_groups": ["100000"]},
                "sticker": {"ingest": {"groups": {"100000": group_config}}},
            }
        )


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

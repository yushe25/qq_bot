from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.agent.providers.router import ErrorCode, ProviderRoutingError
from app.agent.providers.vision_openai_compatible import (
    OpenAICompatibleVisionProvider,
    VisionProviderRouter,
    build_vision_provider,
)
from app.config import StickerAutoTagConfig, StickerAutoTagProviderConfig


class _StaticVision:
    def __init__(self, result: str = "ok") -> None:
        self.result = result
        self.calls = 0

    async def describe(self, image_path: Path, *, prompt: str) -> str:
        del image_path, prompt
        self.calls += 1
        return self.result


class _FailingVision:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def describe(self, image_path: Path, *, prompt: str) -> str:
        del image_path, prompt
        self.calls += 1
        raise self.error


def _patch_http_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    original_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        del args, kwargs
        return original_client(transport=transport)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _gif_bytes() -> bytes:
    return b"GIF89a" + struct.pack("<HH", 1, 1) + b"\x00\x00\x00"


def _write_gif(path: Path) -> None:
    path.write_bytes(_gif_bytes())


def _provider_config(**overrides: Any) -> StickerAutoTagProviderConfig:
    values: dict[str, Any] = {
        "base_url": "https://vision.example/v1",
        "api_key_env": "QQF_VISION_API_KEY",
        "model": "vision-model",
        "max_retries": 0,
    }
    values.update(overrides)
    return StickerAutoTagProviderConfig.model_validate(values)


async def test_vision_provider_sends_local_image_without_logging_base64(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "pilot.gif"
    _write_gif(path)
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    _patch_http_client(monkeypatch, httpx.MockTransport(handler))
    provider = OpenAICompatibleVisionProvider("vision-main", _provider_config(), "test-key")

    result = await provider.describe(path, prompt="请描述图片")

    content = captured["body"]["messages"][0]["content"]
    assert result == "ok"
    assert content[0] == {"type": "text", "text": "请描述图片"}
    assert content[1]["image_url"]["url"].startswith("data:image/gif;base64,")
    encoded = base64.b64encode(_gif_bytes()).decode("ascii")
    assert encoded not in caplog.text
    assert "test-key" not in caplog.text


async def test_vision_provider_rejects_invalid_local_image(tmp_path: Path) -> None:
    path = tmp_path / "pilot.txt"
    path.write_text("not an image", encoding="utf-8")
    provider = OpenAICompatibleVisionProvider("vision-main", _provider_config(), "test-key")

    with pytest.raises(ValueError, match="vision image rejected"):
        await provider.describe(path, prompt="请描述图片")


async def test_vision_router_falls_back_only_for_transient_errors() -> None:
    auth = _FailingVision(ProviderRoutingError("AUTH", "vision-main"))
    fallback = _StaticVision("fallback")
    router = VisionProviderRouter(
        primary="vision-main",
        providers={"vision-main": auth, "vision-backup": fallback},
        fallbacks=["vision-backup"],
    )

    with pytest.raises(ProviderRoutingError, match="AUTH"):
        await router.describe(Path("unused.gif"), prompt="prompt")
    assert fallback.calls == 0

    primary = _FailingVision(ProviderRoutingError("RATE_LIMIT", "vision-main"))
    router = VisionProviderRouter(
        primary="vision-main",
        providers={"vision-main": primary, "vision-backup": fallback},
        fallbacks=["vision-backup"],
    )

    assert await router.describe(Path("unused.gif"), prompt="prompt") == "fallback"
    assert fallback.calls == 1


@pytest.mark.parametrize("code", ["RATE_LIMIT", "TIMEOUT", "PROVIDER_UNAVAILABLE"])
async def test_vision_router_falls_back_for_all_transient_codes(code: ErrorCode) -> None:
    primary = _FailingVision(ProviderRoutingError(code, "vision-main"))
    fallback = _StaticVision("fallback")
    router = VisionProviderRouter(
        primary="vision-main",
        providers={"vision-main": primary, "vision-backup": fallback},
        fallbacks=["vision-backup"],
    )

    assert await router.describe(Path("unused.gif"), prompt="prompt") == "fallback"
    assert fallback.calls == 1


async def test_vision_provider_retries_transient_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "pilot.gif"
    _write_gif(path)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, json={"error": "down"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    _patch_http_client(monkeypatch, httpx.MockTransport(handler))
    provider = OpenAICompatibleVisionProvider(
        "vision-main",
        _provider_config(max_retries=1),
        "test-key",
    )

    assert await provider.describe(path, prompt="prompt") == "ok"
    assert len(attempts) == 2


async def test_vision_provider_normalizes_malformed_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "pilot.gif"
    _write_gif(path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    _patch_http_client(monkeypatch, httpx.MockTransport(handler))
    provider = OpenAICompatibleVisionProvider("vision-main", _provider_config(), "test-key")
    router = VisionProviderRouter(primary="vision-main", providers={"vision-main": provider})

    with pytest.raises(ProviderRoutingError, match="INVALID_RESPONSE"):
        await router.describe(path, prompt="prompt")


async def test_vision_router_normalizes_bad_response() -> None:
    bad = _FailingVision(ValueError("not json"))
    router = VisionProviderRouter(primary="vision-main", providers={"vision-main": bad})

    with pytest.raises(ProviderRoutingError, match="INVALID_RESPONSE"):
        await router.describe(Path("unused.gif"), prompt="prompt")


def test_build_vision_provider_reads_only_environment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QQF_VISION_API_KEY", "test-key")
    provider = build_vision_provider(
        StickerAutoTagConfig(
            enabled=True,
            provider="vision-main",
            providers={
                "vision-main": _provider_config(),
            },
        )
    )

    assert isinstance(provider, VisionProviderRouter)
    assert provider.provider_ids == ["vision-main"]


def test_build_vision_provider_rejects_missing_environment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QQF_VISION_API_KEY", raising=False)
    config = StickerAutoTagConfig(
        enabled=True,
        provider="vision-main",
        providers={"vision-main": _provider_config()},
    )

    with pytest.raises(ValueError, match="QQF_VISION_API_KEY"):
        build_vision_provider(config)

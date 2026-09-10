from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.agent.llm_provider import ChatRequest
from app.agent.providers.mock import MockProvider
from app.agent.providers.openai_compatible import OpenAICompatibleProvider
from app.config import AppConfig, LLMConfig


def _patch_http_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    original_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return original_client(transport=transport)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def test_provider_sends_extra_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "ok",
                        }
                    }
                ]
            },
        )

    _patch_http_client(monkeypatch, httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        LLMConfig(
            base_url="http://test/v1",
            extra_headers={"x-opencode-session": "session-123"},
        )
    )

    result = await provider.complete(
        ChatRequest(
            system="system",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )
    )

    assert result.kind == "text"
    assert result.content == "ok"
    assert captured["headers"]["x-opencode-session"] == "session-123"
    assert "authorization" not in captured["headers"]
    assert captured["body"]["messages"][0]["role"] == "system"


async def test_provider_parses_token_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 123,
                    "completion_tokens": 7,
                    "total_tokens": 130,
                },
            },
        )

    _patch_http_client(monkeypatch, httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(LLMConfig(base_url="http://test/v1"))

    result = await provider.complete(
        ChatRequest(
            system="system",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )
    )

    assert result.prompt_tokens == 123
    assert result.completion_tokens == 7
    assert result.total_tokens == 130


async def test_mock_provider_reports_approximate_token_usage() -> None:
    provider = MockProvider(LLMConfig(provider="mock"))

    result = await provider.complete(
        ChatRequest(
            system="当前会话：group 100000",
            messages=[{"role": "user", "content": "测试"}],
            tools=[],
        )
    )

    assert result.prompt_tokens > 0
    assert result.completion_tokens > 0
    assert result.total_tokens == result.prompt_tokens + result.completion_tokens


async def test_provider_uses_full_chat_completions_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "ok",
                        }
                    }
                ]
            },
        )

    _patch_http_client(monkeypatch, httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        LLMConfig(base_url="https://opencode.ai/zen/go/v1/chat/completions")
    )

    result = await provider.complete(
        ChatRequest(
            system="system",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )
    )

    assert result.kind == "text"
    assert captured["url"] == "https://opencode.ai/zen/go/v1/chat/completions"


async def test_provider_retries_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    _patch_http_client(monkeypatch, httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        LLMConfig(
            base_url="http://test/v1",
            max_retries=2,
            retry_base_delay_seconds=0.0,
        )
    )

    result = await provider.complete(
        ChatRequest(
            system="system",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )
    )

    assert result.kind == "text"
    assert result.content == "ok"
    assert len(attempts) == 3


async def test_provider_raises_after_retries_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503, json={"error": "unavailable"})

    _patch_http_client(monkeypatch, httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        LLMConfig(
            base_url="http://test/v1",
            max_retries=2,
            retry_base_delay_seconds=0.0,
        )
    )

    with pytest.raises(httpx.HTTPStatusError):
        await provider.complete(
            ChatRequest(
                system="system",
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
            )
        )

    assert len(attempts) == 3


async def test_provider_does_not_retry_bad_request(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, json={"error": "bad request"})

    _patch_http_client(monkeypatch, httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        LLMConfig(
            base_url="http://test/v1",
            max_retries=2,
            retry_base_delay_seconds=0.0,
        )
    )

    with pytest.raises(httpx.HTTPStatusError):
        await provider.complete(
            ChatRequest(
                system="system",
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
            )
        )

    assert len(attempts) == 1


def test_llm_config_normalizes_extra_headers(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "llm": {
                    "baseUrl": "http://test/v1",
                    "extraHeaders": {"x-opencode-session": "session-123"},
                }
            }
        ),
        encoding="utf-8",
    )

    config = AppConfig.load(config_path)

    assert config.llm.extra_headers == {"x-opencode-session": "session-123"}

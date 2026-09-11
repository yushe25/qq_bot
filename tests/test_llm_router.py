from __future__ import annotations

import httpx
import pytest

from app.agent.llm_provider import ChatRequest, ChatResult
from app.agent.providers import create_provider
from app.agent.providers.openai_compatible import OpenAICompatibleProvider
from app.agent.providers.router import (
    LLMRouter,
    ProviderRoutingError,
    ProviderUnavailableError,
    build_llm_provider,
)
from app.config import LLMConfig


class _StaticProvider:
    def __init__(
        self,
        *,
        result: ChatResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or ChatResult(kind="text", content="ok")
        self.error = error
        self.calls = 0

    async def complete(self, req: ChatRequest) -> ChatResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def _request() -> ChatRequest:
    return ChatRequest(system="system", messages=[], tools=[])


async def test_router_uses_primary_without_calling_fallback() -> None:
    primary = _StaticProvider(result=ChatResult(kind="text", content="primary"))
    fallback = _StaticProvider(result=ChatResult(kind="text", content="fallback"))
    router = LLMRouter(primary="deepseek", providers={"deepseek": primary, "zhipu": fallback})

    result = await router.complete(_request())

    assert result.content == "primary"
    assert primary.calls == 1
    assert fallback.calls == 0


async def test_router_falls_back_on_transient_error() -> None:
    primary = _StaticProvider(error=ProviderRoutingError("RATE_LIMIT", "deepseek"))
    fallback = _StaticProvider(result=ChatResult(kind="text", content="fallback"))
    router = LLMRouter(
        primary="deepseek",
        providers={"deepseek": primary, "zhipu": fallback},
        fallbacks=["zhipu"],
    )

    result = await router.complete(_request())

    assert result.content == "fallback"
    assert primary.calls == 1
    assert fallback.calls == 1


async def test_router_does_not_fallback_on_auth_error() -> None:
    primary = _StaticProvider(error=ProviderRoutingError("AUTH", "deepseek"))
    fallback = _StaticProvider()
    router = LLMRouter(
        primary="deepseek",
        providers={"deepseek": primary, "zhipu": fallback},
        fallbacks=["zhipu"],
    )

    with pytest.raises(ProviderRoutingError, match="AUTH"):
        await router.complete(_request())

    assert fallback.calls == 0


async def test_router_can_disable_fallback() -> None:
    primary = _StaticProvider(error=ProviderRoutingError("RATE_LIMIT", "deepseek"))
    fallback = _StaticProvider()
    router = LLMRouter(
        primary="deepseek",
        providers={"deepseek": primary, "zhipu": fallback},
        fallbacks=["zhipu"],
        allow_fallback=False,
    )

    with pytest.raises(ProviderRoutingError, match="RATE_LIMIT"):
        await router.complete(_request())

    assert fallback.calls == 0


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"error": "bad key"}, "AUTH"),
        (403, {"error": "forbidden"}, "AUTH"),
        (429, {"error": "slow down"}, "RATE_LIMIT"),
        (400, {"error": "maximum context length exceeded"}, "CONTEXT_LENGTH"),
        (400, {"error": "bad tool schema"}, "INVALID_REQUEST"),
        (503, {"error": "down"}, "PROVIDER_UNAVAILABLE"),
    ],
)
async def test_router_normalizes_http_errors(
    status: int,
    body: dict[str, str],
    expected: str,
) -> None:
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(status, json=body, request=request)
    primary = _StaticProvider(
        error=httpx.HTTPStatusError("failed", request=request, response=response)
    )
    router = LLMRouter(primary="deepseek", providers={"deepseek": primary})

    with pytest.raises(ProviderRoutingError) as exc_info:
        await router.complete(_request())

    assert exc_info.value.code == expected


@pytest.mark.parametrize("error", [IndexError("missing choices"), AttributeError("bad message")])
async def test_router_normalizes_malformed_provider_responses(error: Exception) -> None:
    router = LLMRouter(
        primary="deepseek",
        providers={"deepseek": _StaticProvider(error=error)},
    )

    with pytest.raises(ProviderRoutingError) as exc_info:
        await router.complete(_request())

    assert exc_info.value.code == "INVALID_RESPONSE"


async def test_router_attempts_each_configured_provider_at_most_once() -> None:
    primary = _StaticProvider(error=ProviderRoutingError("PROVIDER_UNAVAILABLE", "deepseek"))
    first = _StaticProvider(error=ProviderRoutingError("RATE_LIMIT", "zhipu"))
    second = _StaticProvider(error=httpx.ConnectError("offline"))
    router = LLMRouter(
        primary="deepseek",
        providers={"deepseek": primary, "zhipu": first, "moonshot": second},
        fallbacks=["zhipu", "moonshot"],
    )

    with pytest.raises(ProviderUnavailableError, match="deepseek, zhipu, moonshot"):
        await router.complete(_request())

    assert primary.calls == 1
    assert first.calls == 1
    assert second.calls == 1


def test_build_llm_provider_builds_named_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("ZHIPU_API_KEY", "test-zhipu-key")
    config = LLMConfig(
        provider="deepseek",
        fallbacks=["zhipu"],
        providers={
            "deepseek": {
                "base_url": "https://api.deepseek.com/v1",
                "api_key_env": "DEEPSEEK_API_KEY",
                "model": "deepseek-chat",
                "temperature": 0.25,
                "max_tokens": 321,
            },
            "zhipu": {
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "api_key_env": "ZHIPU_API_KEY",
                "model": "glm-4-flash",
            },
        },
    )

    provider = build_llm_provider(config)
    package_provider = create_provider(config)

    assert isinstance(provider, LLMRouter)
    assert provider.provider_ids == ["deepseek", "zhipu"]
    primary = provider._providers["deepseek"]
    assert isinstance(primary, OpenAICompatibleProvider)
    assert primary.config.temperature == 0.25
    assert primary.config.max_tokens == 321
    assert isinstance(package_provider, LLMRouter)
    assert package_provider.provider_ids == ["deepseek", "zhipu"]


def test_llm_config_normalizes_primary_provider_id() -> None:
    config = LLMConfig(
        provider=" DeepSeek ",
        providers={
            "deepseek": {
                "base_url": "https://api.deepseek.com/v1",
                "api_key_env": "DEEPSEEK_API_KEY",
                "model": "deepseek-chat",
            }
        },
    )

    assert config.provider == "deepseek"


def test_build_llm_provider_rejects_missing_selected_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config = LLMConfig(
        provider="deepseek",
        providers={
            "deepseek": {
                "base_url": "https://api.deepseek.com/v1",
                "api_key_env": "DEEPSEEK_API_KEY",
                "model": "deepseek-chat",
            }
        },
    )

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        build_llm_provider(config)

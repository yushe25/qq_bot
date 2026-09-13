from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Sequence
from typing import Literal

import httpx

from app.agent.llm_provider import ChatRequest, ChatResult, LLMProvider
from app.agent.providers.mock import MockProvider
from app.agent.providers.openai_compatible import OpenAICompatibleProvider
from app.config import LLMConfig, LLMProviderConfig

logger = logging.getLogger("qqf.llm.router")

ErrorCode = Literal[
    "AUTH",
    "RATE_LIMIT",
    "TIMEOUT",
    "CONTEXT_LENGTH",
    "INVALID_REQUEST",
    "INVALID_RESPONSE",
    "PROVIDER_UNAVAILABLE",
    "UNKNOWN",
]

_FALLBACK_ERROR_CODES = {"RATE_LIMIT", "TIMEOUT", "PROVIDER_UNAVAILABLE"}
_CONTEXT_ERROR_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "too many tokens",
    "token limit",
    "上下文",
)


class ProviderRoutingError(RuntimeError):
    def __init__(self, code: ErrorCode, provider_id: str, detail: str = "") -> None:
        self.code: ErrorCode = code
        self.provider_id = provider_id
        self.detail = detail
        message = f"provider {provider_id!r} failed: {code}"
        if detail:
            message += f" ({detail})"
        super().__init__(message)


class ProviderUnavailableError(ProviderRoutingError):
    def __init__(self, provider_ids: Sequence[str], last_code: ErrorCode) -> None:
        self.provider_ids = list(provider_ids)
        self.last_code = last_code
        super().__init__(
            "PROVIDER_UNAVAILABLE",
            self.provider_ids[-1],
            f"tried {', '.join(self.provider_ids)}; last error {last_code}",
        )


class LLMRouter:
    def __init__(
        self,
        *,
        primary: str,
        providers: dict[str, LLMProvider],
        fallbacks: Sequence[str] | None = None,
        allow_fallback: bool = True,
    ) -> None:
        if primary not in providers:
            raise ValueError(f"primary provider {primary!r} is not configured")
        self._providers = dict(providers)
        self.provider_ids = [primary]
        if allow_fallback:
            for provider_id in fallbacks or []:
                if provider_id not in self._providers:
                    raise ValueError(f"fallback provider {provider_id!r} is not configured")
                if provider_id in self.provider_ids:
                    raise ValueError(f"provider {provider_id!r} is configured more than once")
                self.provider_ids.append(provider_id)
        self.allow_fallback = allow_fallback

    async def complete(self, req: ChatRequest) -> ChatResult:
        last_error: ProviderRoutingError | None = None
        attempted: list[str] = []
        for provider_id in self.provider_ids:
            attempted.append(provider_id)
            try:
                return await self._providers[provider_id].complete(req)
            except Exception as exc:  # noqa: BLE001 - provider boundary
                error = normalize_provider_error(provider_id, exc)
                if error.code not in _FALLBACK_ERROR_CODES:
                    raise error from None
                last_error = error
                logger.warning(
                    "LLM provider %s failed with %s",
                    provider_id,
                    error.code,
                )

        if last_error is None:
            raise ProviderRoutingError("UNKNOWN", self.provider_ids[0], "no provider attempted")
        if len(attempted) == 1:
            raise last_error
        raise ProviderUnavailableError(attempted, last_error.code) from None


def build_llm_provider(config: LLMConfig) -> LLMProvider:
    provider_id = config.provider.strip().lower()
    if provider_id == "mock":
        return MockProvider(config)
    if not config.providers:
        return OpenAICompatibleProvider(config)

    selected_ids = [provider_id]
    if config.allow_fallback:
        selected_ids.extend(config.fallbacks)
    providers: dict[str, LLMProvider] = {}
    for selected_id in selected_ids:
        provider_config = config.providers[selected_id]
        api_key = os.getenv(provider_config.api_key_env, "").strip()
        if not api_key:
            raise ValueError(
                f"LLM provider {selected_id!r} API key is not set in "
                f"environment variable {provider_config.api_key_env!r}"
            )
        providers[selected_id] = OpenAICompatibleProvider(
            _as_flat_config(provider_config, api_key=api_key)
        )

    return LLMRouter(
        primary=provider_id,
        providers=providers,
        fallbacks=config.fallbacks if config.allow_fallback else [],
        allow_fallback=config.allow_fallback,
    )


def normalize_provider_error(provider_id: str, exc: Exception) -> ProviderRoutingError:
    if isinstance(exc, ProviderRoutingError):
        return exc
    if isinstance(exc, httpx.HTTPStatusError):
        return ProviderRoutingError(_http_error_code(exc.response), provider_id)
    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
        return ProviderRoutingError("TIMEOUT", provider_id)
    if isinstance(exc, httpx.TransportError):
        return ProviderRoutingError("PROVIDER_UNAVAILABLE", provider_id)
    if isinstance(exc, (AttributeError, IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError)):
        return ProviderRoutingError("INVALID_RESPONSE", provider_id)
    return ProviderRoutingError("UNKNOWN", provider_id)


def _http_error_code(response: httpx.Response) -> ErrorCode:
    status = response.status_code
    if status in {401, 403}:
        return "AUTH"
    if status == 429:
        return "RATE_LIMIT"
    if status == 408:
        return "TIMEOUT"
    if status in {400, 413}:
        return "CONTEXT_LENGTH" if _looks_like_context_error(response) else "INVALID_REQUEST"
    if 500 <= status < 600:
        return "PROVIDER_UNAVAILABLE"
    return "INVALID_REQUEST"


def _looks_like_context_error(response: httpx.Response) -> bool:
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = response.text
    text = json.dumps(payload, ensure_ascii=False).lower()
    return any(marker in text for marker in _CONTEXT_ERROR_MARKERS)


def _as_flat_config(config: LLMProviderConfig, *, api_key: str) -> LLMConfig:
    return LLMConfig(
        provider="openai-compatible",
        base_url=config.base_url,
        api_key_env=config.api_key_env,
        api_key=api_key,
        extra_headers=config.extra_headers,
        model=config.model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout_seconds=config.timeout_seconds,
        max_retries=config.max_retries,
        retry_base_delay_seconds=config.retry_base_delay_seconds,
    )

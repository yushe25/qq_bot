from __future__ import annotations

import asyncio
import base64
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import httpx

from app.agent.providers.router import (
    ProviderRoutingError,
    ProviderUnavailableError,
    normalize_provider_error,
)
from app.config import StickerAutoTagConfig, StickerAutoTagProviderConfig
from app.safety.media_guard import MediaGuard

logger = logging.getLogger("qqf.vision")

_FALLBACK_ERROR_CODES = {"RATE_LIMIT", "TIMEOUT", "PROVIDER_UNAVAILABLE"}
_IMAGE_MIME_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
}


class VisionProvider(Protocol):
    async def describe(self, image_path: Path, *, prompt: str) -> str: ...


class OpenAICompatibleVisionProvider:
    def __init__(self, provider_id: str, config: StickerAutoTagProviderConfig, api_key: str) -> None:
        self.provider_id = provider_id
        self.config = config
        self.api_key = api_key
        self.base_url = config.base_url.rstrip("/")
        self.media_guard = MediaGuard(max_bytes=config.max_image_bytes, max_pixels=40_960_000)

    def _chat_completions_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    async def describe(self, image_path: Path, *, prompt: str) -> str:
        image_url = self._image_data_url(image_path)
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            "temperature": 0.1,
        }
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        headers.update(self.config.extra_headers)

        attempts = max(1, self.config.max_retries + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                    response = await client.post(self._chat_completions_url(), headers=headers, json=payload)
                    response.raise_for_status()
                    return _parse_response(response.json())
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status != 429 and not 500 <= status < 600:
                    raise
                last_error = exc
            except httpx.TransportError as exc:
                last_error = exc
            if attempt < attempts - 1:
                delay = min(8.0, float(2**attempt))
                logger.warning(
                    "vision provider %s attempt %s/%s failed with transient error; retrying in %.1fs",
                    self.provider_id,
                    attempt + 1,
                    attempts,
                    delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
        if last_error is None:
            raise RuntimeError("vision request failed without a captured error")
        raise last_error

    def _image_data_url(self, image_path: Path) -> str:
        path = Path(image_path)
        if not path.is_file():
            raise ValueError("vision image must be an existing local file")
        ok, reason = self.media_guard.check_file(path)
        if not ok:
            raise ValueError(f"vision image rejected: {reason}")
        mime_type = _IMAGE_MIME_TYPES.get(path.suffix.lower())
        if mime_type is None:
            raise ValueError("unsupported vision image type")
        raw = path.read_bytes()
        if len(raw) > self.config.max_image_bytes:
            raise ValueError("vision image is too large")
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"


class VisionProviderRouter:
    def __init__(
        self,
        *,
        primary: str,
        providers: dict[str, VisionProvider],
        fallbacks: Sequence[str] = (),
    ) -> None:
        if primary not in providers:
            raise ValueError(f"primary vision provider {primary!r} is not configured")
        self._providers = dict(providers)
        self.provider_ids = [primary]
        for provider_id in fallbacks:
            if provider_id not in self._providers:
                raise ValueError(f"fallback vision provider {provider_id!r} is not configured")
            if provider_id in self.provider_ids:
                raise ValueError(f"vision provider {provider_id!r} is configured more than once")
            self.provider_ids.append(provider_id)

    async def describe(self, image_path: Path, *, prompt: str) -> str:
        attempted: list[str] = []
        last_error: ProviderRoutingError | None = None
        for provider_id in self.provider_ids:
            attempted.append(provider_id)
            try:
                return await self._providers[provider_id].describe(image_path, prompt=prompt)
            except Exception as exc:  # noqa: BLE001 - provider boundary
                error = normalize_provider_error(provider_id, exc)
                if error.code not in _FALLBACK_ERROR_CODES:
                    raise error from None
                last_error = error
                logger.warning("vision provider %s failed with %s", provider_id, error.code)
        if last_error is None:
            raise ProviderRoutingError("UNKNOWN", self.provider_ids[0], "no provider attempted")
        if len(attempted) == 1:
            raise last_error
        raise ProviderUnavailableError(attempted, last_error.code) from None


def build_vision_provider(config: StickerAutoTagConfig) -> VisionProvider:
    if not config.enabled:
        raise ValueError("sticker.autoTag.enabled must be true")
    selected_ids = [config.provider, *config.fallbacks]
    providers: dict[str, VisionProvider] = {}
    for provider_id in selected_ids:
        provider_config = config.providers[provider_id]
        api_key = os.getenv(provider_config.api_key_env, "").strip()
        if not api_key:
            raise ValueError(
                f"vision provider {provider_id!r} API key is not set in "
                f"environment variable {provider_config.api_key_env!r}"
            )
        providers[provider_id] = OpenAICompatibleVisionProvider(provider_id, provider_config, api_key)
    return VisionProviderRouter(
        primary=config.provider,
        providers=providers,
        fallbacks=config.fallbacks,
    )


def _parse_response(body: object) -> str:
    if not isinstance(body, dict):
        raise TypeError("vision response is not an object")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("vision response is missing choices")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise TypeError("vision choice is not an object")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise TypeError("vision message is not an object")
    content = message.get("content")
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        text = "".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
    else:
        text = ""
    if not text:
        raise ValueError("vision response content is empty")
    return text

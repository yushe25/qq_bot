from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.sticker_limits import MAX_STICKER_DESCRIPTION_CHARS, MAX_STICKER_TAGS

_PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_BLOCKED_EXTRA_HEADERS = {"authorization", "content-type"}
logger = logging.getLogger("qqf.config")


class QQConfig(BaseModel):
    ws_url: str = "ws://127.0.0.1:3001"
    http_url: str = "http://127.0.0.1:3000"
    access_token: str = ""
    owner_qq: str = ""
    enabled: bool = True
    allow_remote: bool = False

    @model_validator(mode="after")
    def validate_transport_scope(self) -> QQConfig:
        remote_fields = [
            field
            for field, value, schemes in (
                ("qq.wsUrl", self.ws_url, {"ws", "wss"}),
                ("qq.httpUrl", self.http_url, {"http", "https"}),
            )
            if _validate_qq_transport_url(field, value, schemes)
        ]
        if remote_fields and not self.allow_remote:
            raise ValueError(
                "non-loopback qq transport requires qq.allowRemote=true: "
                + ", ".join(remote_fields)
            )
        if remote_fields:
            logger.warning(
                "qq.allowRemote=true permits non-loopback OneBot transport: %s",
                ", ".join(remote_fields),
            )
        return self


class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools: bool = True
    streaming: bool = False

    @model_validator(mode="after")
    def validate_supported_capabilities(self) -> ProviderCapabilities:
        if not self.tools:
            raise ValueError("provider capabilities.tools must be true")
        if self.streaming:
            raise ValueError("provider capabilities.streaming is not supported")
        return self


class LLMProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["openai-compatible"] = "openai-compatible"
    base_url: str
    api_key_env: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float = Field(default=0.8, ge=0.0, le=2.0)
    max_tokens: int = Field(default=200, ge=1, le=4096)
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=300.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_base_delay_seconds: float = Field(default=1.0, ge=0.0, le=30.0)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    capabilities: ProviderCapabilities = Field(default_factory=ProviderCapabilities)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _validate_https_base_url(value)

    @field_validator("extra_headers")
    @classmethod
    def validate_extra_headers(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_extra_headers(value)


class LLMConfig(BaseModel):
    provider: str = "openai-compatible"
    providers: dict[str, LLMProviderConfig] = Field(default_factory=dict)
    fallbacks: list[str] = Field(default_factory=list)
    allow_fallback: bool = True
    base_url: str = "http://127.0.0.1:8000/v1"
    api_key_env: str = "QQF_LLM_API_KEY"
    api_key: str = ""
    extra_headers: dict[str, str] = Field(default_factory=dict)
    model: str = "qwen2.5-32b"
    temperature: float = 0.8
    max_tokens: int = 200
    timeout_seconds: float = 60.0
    max_retries: int = 2
    retry_base_delay_seconds: float = 1.0

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("extra_headers")
    @classmethod
    def validate_extra_headers(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_extra_headers(value)

    @model_validator(mode="after")
    def validate_provider_routing(self) -> LLMConfig:
        if not self.providers:
            if self.fallbacks:
                raise ValueError("llm.fallbacks requires llm.providers")
            if self.provider not in {"mock", "openai-compatible"}:
                raise ValueError("llm.provider must be mock or openai-compatible without llm.providers")
            return self

        for provider_id in self.providers:
            if not _PROVIDER_ID_RE.fullmatch(provider_id):
                raise ValueError(f"invalid llm.providers key: {provider_id!r}")
        if self.provider != "mock" and self.provider not in self.providers:
            raise ValueError(f"llm.provider {self.provider!r} is not defined in llm.providers")
        if len(self.fallbacks) != len(set(self.fallbacks)):
            raise ValueError("llm.fallbacks must not contain duplicates")
        for fallback in self.fallbacks:
            if fallback == self.provider:
                raise ValueError("llm.fallbacks must not contain the primary provider")
            if fallback not in self.providers:
                raise ValueError(f"llm.fallbacks references unknown provider {fallback!r}")
        return self


class SchedulerConfig(BaseModel):
    batch_window_ms: int = 8000
    wake_probability: float = 0.04
    mention_dive_probability: float = Field(default=0.05, ge=0.0, le=1.0)
    names: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    max_wakes_per_minute: int = 4
    max_wakes_per_hour: int = 20
    max_concurrent: int = 4
    max_tool_steps: int = 6
    max_text_fallback: bool = True
    no_action_limit: int = 2
    pre_sleep_wait_minutes: int = 5
    min_sleep_ms: int = 60_000
    max_sleep_ms: int = 14_400_000
    cooldown_seconds: int = 90


class TokenBudgetConfig(BaseModel):
    wake_messages: int = Field(default=12, ge=1)
    history_messages: int = Field(default=28, ge=2)
    event_text_chars: int = Field(default=160, ge=1)
    tool_result_chars: int = Field(default=1600, ge=1)


class SafetyConfig(BaseModel):
    allow_groups: list[str] = Field(default_factory=list)
    allow_private: list[str] = Field(default_factory=list)
    max_group_members: int = 20
    max_sends_per_minute: int = 4
    max_sends_per_hour: int = 60
    max_sends_per_day: int = 200
    max_sends_per_wake: int = 3
    max_message_chars: int = 200
    max_image_bytes: int = 8 * 1024 * 1024
    max_image_pixels: int = 40_960_000


class ConsoleConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 3100
    token: str = ""


class StorageConfig(BaseModel):
    dir: str = "state"
    database_path: str = "state/qqf.db"


class LearningConfig(BaseModel):
    enabled: bool = True
    interval_minutes: float = 20.0
    min_new_messages: int = 10
    confidence_threshold: float = 0.7
    max_notes_per_session: int = 20
    notes_ttl_days: int = 7
    max_observations_per_session: int = 250
    exclude_members: list[str] = Field(default_factory=list)


class StickerAutoTagProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["openai-compatible"] = "openai-compatible"
    base_url: str
    api_key_env: str = Field(min_length=1)
    model: str = Field(min_length=1)
    timeout_seconds: float = Field(default=120.0, ge=1.0, le=300.0)
    max_retries: int = Field(default=1, ge=0, le=5)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    max_image_bytes: int = Field(default=8 * 1024 * 1024, ge=1)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _validate_https_base_url(value)

    @field_validator("extra_headers")
    @classmethod
    def validate_extra_headers(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_extra_headers(value)


class StickerAutoTagConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: str = ""
    fallbacks: list[str] = Field(default_factory=list)
    providers: dict[str, StickerAutoTagProviderConfig] = Field(default_factory=dict)
    max_tags: int = Field(default=MAX_STICKER_TAGS, ge=1, le=MAX_STICKER_TAGS)
    max_description_chars: int = Field(
        default=MAX_STICKER_DESCRIPTION_CHARS,
        ge=1,
        le=MAX_STICKER_DESCRIPTION_CHARS,
    )

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def validate_provider_routing(self) -> StickerAutoTagConfig:
        for provider_id in self.providers:
            if not _PROVIDER_ID_RE.fullmatch(provider_id):
                raise ValueError(f"invalid sticker.autoTag.providers key: {provider_id!r}")
        if not self.enabled:
            return self
        if not self.provider:
            raise ValueError("sticker.autoTag.enabled=true requires provider")
        if self.provider not in self.providers:
            raise ValueError("sticker.autoTag.provider must exist in providers")
        if len(self.fallbacks) != len(set(self.fallbacks)):
            raise ValueError("sticker.autoTag.fallbacks must not contain duplicates")
        for fallback in self.fallbacks:
            if fallback == self.provider:
                raise ValueError("sticker.autoTag.fallbacks must not contain the primary provider")
            if fallback not in self.providers:
                raise ValueError(f"sticker.autoTag.fallbacks references unknown provider {fallback!r}")
        return self


class StickerConfig(BaseModel):
    enabled: bool = False
    allowed_ids: list[str] = Field(default_factory=list)
    auto_tag: StickerAutoTagConfig = Field(default_factory=StickerAutoTagConfig)

    @model_validator(mode="after")
    def validate_allowed_ids(self) -> StickerConfig:
        if self.enabled and not self.allowed_ids:
            raise ValueError("sticker.enabled=true requires sticker.allowedIds")
        if self.auto_tag.enabled and not self.auto_tag.providers:
            raise ValueError("sticker.autoTag.enabled=true requires providers")
        return self


class AppConfig(BaseModel):
    qq: QQConfig = Field(default_factory=QQConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    token_budget: TokenBudgetConfig = Field(default_factory=TokenBudgetConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    learning: LearningConfig = Field(default_factory=LearningConfig)
    console: ConsoleConfig = Field(default_factory=ConsoleConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    sticker: StickerConfig = Field(default_factory=StickerConfig)
    persona_path: str = "personas/default.yaml"
    sticker_dir: str = "stickers"

    @classmethod
    def load(cls, path: str | Path | None = None) -> AppConfig:
        config = cls()
        if path:
            config_path = Path(path)
            if config_path.exists():
                with config_path.open("r", encoding="utf-8") as f:
                    raw: dict[str, Any] = json.load(f)
                config = cls.model_validate(_normalize_keys(raw))
        data_dir = os.getenv("QQF_DATA_DIR")
        if data_dir:
            config.storage.dir = data_dir
            config.storage.database_path = str(Path(data_dir) / "qqf.db")
        if os.getenv("QQF_LLM_API_KEY"):
            config.llm.api_key = os.environ["QQF_LLM_API_KEY"]
        elif config.llm.api_key_env and not config.llm.api_key:
            config.llm.api_key = os.getenv(config.llm.api_key_env, "")
        if os.getenv("QQF_ONEBOT_TOKEN"):
            config.qq.access_token = os.environ["QQF_ONEBOT_TOKEN"]
        if os.getenv("QQF_CONSOLE_TOKEN"):
            config.console.token = os.environ["QQF_CONSOLE_TOKEN"]
        Path(config.storage.dir).mkdir(parents=True, exist_ok=True)
        return config


def _normalize_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {_snake_key(str(key)): _normalize_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_keys(item) for item in value]
    return value


def _snake_key(key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def _validate_https_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("baseUrl must be an absolute http:// or https:// URL")
    if parsed.scheme == "http" and parsed.hostname not in _LOOPBACK_HOSTS:
        raise ValueError("remote provider baseUrl must use https://")
    return value.rstrip("/")


def _validate_extra_headers(value: dict[str, str]) -> dict[str, str]:
    for key in value:
        if key.lower() in _BLOCKED_EXTRA_HEADERS:
            raise ValueError(f"extraHeaders must not override {key!r}")
    return value


def _validate_qq_transport_url(field: str, value: str, schemes: set[str]) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme not in schemes or not parsed.hostname:
        allowed = ", ".join(sorted(schemes))
        raise ValueError(f"{field} must be an absolute URL using {allowed}")
    return parsed.hostname not in _LOOPBACK_HOSTS

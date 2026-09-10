from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class QQConfig(BaseModel):
    ws_url: str = "ws://127.0.0.1:3001"
    http_url: str = "http://127.0.0.1:3000"
    access_token: str = ""
    owner_qq: str = ""
    enabled: bool = True


class LLMConfig(BaseModel):
    provider: str = "openai-compatible"
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


class SchedulerConfig(BaseModel):
    batch_window_ms: int = 8000
    wake_probability: float = 0.04
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


class AppConfig(BaseModel):
    qq: QQConfig = Field(default_factory=QQConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    token_budget: TokenBudgetConfig = Field(default_factory=TokenBudgetConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    learning: LearningConfig = Field(default_factory=LearningConfig)
    console: ConsoleConfig = Field(default_factory=ConsoleConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
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

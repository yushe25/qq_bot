"""LLM provider implementations."""

from app.agent.providers.mock import MockProvider
from app.agent.providers.openai_compatible import OpenAICompatibleProvider
from app.config import LLMConfig


def create_provider(config: LLMConfig):
    if config.provider == "mock":
        return MockProvider(config)
    return OpenAICompatibleProvider(config)


__all__ = ["MockProvider", "OpenAICompatibleProvider", "create_provider"]

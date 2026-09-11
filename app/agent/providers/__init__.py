"""LLM provider implementations."""

from app.agent.providers.mock import MockProvider
from app.agent.providers.openai_compatible import OpenAICompatibleProvider
from app.config import LLMConfig


def create_provider(config: LLMConfig):
    from app.agent.providers.router import build_llm_provider

    return build_llm_provider(config)


__all__ = ["MockProvider", "OpenAICompatibleProvider", "create_provider"]

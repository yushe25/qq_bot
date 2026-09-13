from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.domain.types import ToolCall


class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class ChatRequest(BaseModel):
    system: str
    messages: Sequence[ChatMessage | dict[str, Any]]
    tools: list[dict[str, Any]]
    temperature: float | None = None
    max_tokens: int | None = None


class ChatResult(BaseModel):
    kind: Literal["text", "tool_calls"]
    content: str | None = None
    calls: list[ToolCall] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMProvider(Protocol):
    async def complete(self, req: ChatRequest) -> ChatResult: ...

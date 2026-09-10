from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.domain.types import ToolCall


class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[dict[str, object]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class ChatRequest(BaseModel):
    system: str
    messages: list[ChatMessage | dict[str, object]]
    tools: list[dict[str, object]]
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

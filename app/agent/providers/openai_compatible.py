from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from app.agent.llm_provider import ChatMessage, ChatRequest, ChatResult
from app.config import LLMConfig
from app.domain.types import ToolCall

logger = logging.getLogger("qqf.llm")


class OpenAICompatibleProvider:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/")

    def _chat_completions_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    async def complete(self, req: ChatRequest) -> ChatResult:
        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [{"role": "system", "content": req.system}, *[_to_openai(m) for m in req.messages]],
            "temperature": req.temperature if req.temperature is not None else self.config.temperature,
            "max_tokens": req.max_tokens if req.max_tokens is not None else self.config.max_tokens,
        }
        if req.tools:
            payload["tools"] = req.tools
        if req.reasoning_effort is not None:
            payload["reasoning_effort"] = req.reasoning_effort
        headers = {"content-type": "application/json"}
        if self.config.api_key:
            headers["authorization"] = f"Bearer {self.config.api_key}"
        headers.update(self.config.extra_headers)
        attempts = max(1, self.config.max_retries + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                    response = await client.post(self._chat_completions_url(), headers=headers, json=payload)
                    response.raise_for_status()
                    body = response.json()
                return self._parse_completion(body)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status != 429 and not 500 <= status < 600:
                    raise
                last_error = exc
            except httpx.TransportError as exc:
                last_error = exc
            if attempt < attempts - 1:
                delay = min(8.0, self.config.retry_base_delay_seconds * (2**attempt))
                logger.warning(
                    "LLM request failed (attempt %s/%s): %s; retrying in %.1fs",
                    attempt + 1,
                    attempts,
                    last_error,
                    delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
        if last_error is None:
            raise RuntimeError("LLM request failed without a captured error")
        raise last_error

    def _parse_completion(self, body: dict[str, object]) -> ChatResult:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("completion response is missing choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise TypeError("completion choice is not an object")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise TypeError("completion message is not an object")
        prompt_tokens, completion_tokens, total_tokens, reasoning_tokens = _parse_usage(body)
        finish_reason_value = choice.get("finish_reason")
        finish_reason = str(finish_reason_value) if finish_reason_value is not None else None
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            calls: list[ToolCall] = []
            for call in tool_calls:
                if not isinstance(call, dict):
                    raise TypeError("tool call is not an object")
                function = call.get("function")
                if not isinstance(function, dict):
                    raise TypeError("tool call function is not an object")
                raw_args = function.get("arguments")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else {}
                except json.JSONDecodeError:
                    args = {"_error": raw_args if isinstance(raw_args, str) else "invalid arguments"}
                if not isinstance(args, dict):
                    args = {"_error": args}
                calls.append(
                    ToolCall(
                        name=str(function.get("name", "")),
                        arguments=args,
                        id=str(call.get("id") or ""),
                    )
                )
            return ChatResult(
                kind="tool_calls",
                calls=calls,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                finish_reason=finish_reason,
                reasoning_tokens=reasoning_tokens,
            )
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
        return ChatResult(
            kind="text",
            content=str(content or ""),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            reasoning_tokens=reasoning_tokens,
        )


def _parse_usage(body: dict[str, object]) -> tuple[int, int, int, int]:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return 0, 0, 0, 0
    prompt_tokens = _as_non_negative_int(usage.get("prompt_tokens"))
    completion_tokens = _as_non_negative_int(usage.get("completion_tokens"))
    total_tokens = _as_non_negative_int(usage.get("total_tokens"))
    details = usage.get("completion_tokens_details")
    reasoning_tokens = (
        _as_non_negative_int(details.get("reasoning_tokens"))
        if isinstance(details, dict)
        else 0
    )
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens, reasoning_tokens


def _as_non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(value))
        except ValueError:
            return 0
    return 0


def _to_openai(message: ChatMessage | dict[str, Any]) -> dict[str, object]:
    if isinstance(message, ChatMessage):
        item: dict[str, object] = {"role": message.role}
        if message.content is not None:
            item["content"] = message.content
        if message.tool_calls:
            item["tool_calls"] = message.tool_calls
        if message.tool_call_id:
            item["tool_call_id"] = message.tool_call_id
        if message.name:
            item["name"] = message.name
        return item
    return message

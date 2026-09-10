from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.domain.types import NormalizedEvent, now_ms

EventHandler = Callable[[NormalizedEvent], Awaitable[None] | None]


class QQGateway(Protocol):
    async def connect(self) -> None: ...
    def on_event(self, handler: EventHandler) -> None: ...
    async def disconnect(self) -> None: ...
    async def get_message(self, message_id: str) -> dict[str, object]: ...
    async def send_group_message(self, group_id: str, text: str, reply_to_message_id: str | None = None) -> dict[str, object]: ...
    async def send_private_message(self, user_id: str, text: str, reply_to_message_id: str | None = None) -> dict[str, object]: ...
    async def send_group_sticker(self, group_id: str, file_path: str, reply_to_message_id: str | None = None) -> dict[str, object]: ...
    async def send_private_sticker(self, user_id: str, file_path: str, reply_to_message_id: str | None = None) -> dict[str, object]: ...
    async def get_group_history(self, group_id: str, limit: int = 20, message_seq: int | None = None) -> list[dict[str, object]]: ...
    async def get_group_members(self, group_id: str) -> list[dict[str, object]]: ...


@dataclass
class NullGateway:
    sent: list[dict[str, object]] = field(default_factory=list)
    messages: dict[str, dict[str, object]] = field(default_factory=dict)
    members: list[dict[str, object]] = field(
        default_factory=lambda: [{"group_id": None, "nickname": "mock-member"}]
    )

    def add_message(self, message_id: str, **fields: object) -> None:
        self.messages[message_id] = {"message_id": message_id, **fields}

    async def connect(self) -> None:
        return None

    def on_event(self, handler: EventHandler) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def get_message(self, message_id: str) -> dict[str, object]:
        return self.messages.get(message_id, {})

    async def send_group_message(self, group_id: str, text: str, reply_to_message_id: str | None = None) -> dict[str, object]:
        item = {"group_id": group_id, "text": text, "reply_to": reply_to_message_id, "ts": now_ms()}
        self.sent.append(item)
        return {"message_id": f"mock-{len(self.sent)}"}

    async def send_private_message(self, user_id: str, text: str, reply_to_message_id: str | None = None) -> dict[str, object]:
        item = {"user_id": user_id, "text": text, "reply_to": reply_to_message_id, "ts": now_ms()}
        self.sent.append(item)
        return {"message_id": f"mock-{len(self.sent)}"}

    async def send_group_sticker(self, group_id: str, file_path: str, reply_to_message_id: str | None = None) -> dict[str, object]:
        item = {"type": "sticker", "group_id": group_id, "file": file_path, "reply_to": reply_to_message_id, "ts": now_ms()}
        self.sent.append(item)
        return {"message_id": f"mock-{len(self.sent)}"}

    async def send_private_sticker(self, user_id: str, file_path: str, reply_to_message_id: str | None = None) -> dict[str, object]:
        item = {"type": "sticker", "user_id": user_id, "file": file_path, "reply_to": reply_to_message_id, "ts": now_ms()}
        self.sent.append(item)
        return {"message_id": f"mock-{len(self.sent)}"}

    async def get_group_history(self, group_id: str, limit: int = 20, message_seq: int | None = None) -> list[dict[str, object]]:
        return [{"group_id": group_id, "message_seq": message_seq, "limit": limit}]

    async def get_group_members(self, group_id: str) -> list[dict[str, object]]:
        return self.members

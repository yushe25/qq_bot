from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

COMMON_FACE_NAMES = {"178": "抠鼻"}


class SessionState(StrEnum):
    PAUSED = "paused"
    IDLE = "idle"
    WAKING = "waking"
    ACTIVE = "active"
    DIVING = "diving"
    COOLDOWN = "cooldown"


class TriggerCategory(StrEnum):
    FORCE = "force"
    MENTION = "mention"
    NAME = "name"
    QUESTION = "question"
    KEYWORD = "keyword"
    POKE = "poke"
    NORMAL = "normal"
    SCHEDULED = "scheduled"


def now_ms() -> float:
    from time import time

    return time() * 1000


def scene_session_id(scene_type: str, scene_id: str) -> str:
    return f"{scene_type}:{scene_id}"


@dataclass(frozen=True)
class MessageSegment:
    kind: str
    data: dict[str, Any]


def segment_text(segment: MessageSegment) -> str:
    if segment.kind == "text":
        return str(segment.data.get("text", ""))
    if segment.kind == "at":
        return f"@{segment.data.get('name', segment.data.get('qq', ''))}"
    if segment.kind == "face":
        face_id = str(segment.data.get("id", ""))
        name = str(segment.data.get("name", "")) or COMMON_FACE_NAMES.get(face_id, "")
        return f"[表情:{face_id}]" if not name else f"[表情:{face_id}:{name}]"
    if segment.kind == "image":
        return "[图片]"
    if segment.kind == "record":
        return "[语音]"
    if segment.kind == "file":
        filename = str(segment.data.get("name", ""))
        return f"[文件:{filename}]" if filename else "[文件]"
    if segment.kind == "forward":
        return "[分享卡片]"
    return ""


def is_media_segment(segment: MessageSegment) -> bool:
    return segment.kind in {"image", "record", "file", "face", "forward"}


@dataclass(frozen=True)
class NormalizedEvent:
    event_id: str
    session_id: str
    timestamp: float
    sender_id: str
    sender_name: str
    kind: str
    segments: tuple[MessageSegment, ...]
    text: str
    raw: dict[str, Any]
    bot_id: str = ""


@dataclass
class Session:
    id: str
    scene_type: str
    scene_id: str
    state: SessionState = SessionState.IDLE
    persona_id: str = "default"
    next_wake_at: float = 0.0
    cooldown_until: float = 0.0
    last_wake_at: float | None = None
    last_message_at: float = 0.0
    wake_count: int = 0
    conversation: list[dict[str, Any]] = field(default_factory=list)
    pending_output: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scene_type": self.scene_type,
            "scene_id": self.scene_id,
            "state": str(self.state),
            "persona_id": self.persona_id,
            "next_wake_at": self.next_wake_at,
            "cooldown_until": self.cooldown_until,
            "last_wake_at": self.last_wake_at,
            "last_message_at": self.last_message_at,
            "wake_count": self.wake_count,
            "conversation": self.conversation,
            "pending_output": self.pending_output,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        return cls(
            id=str(data["id"]),
            scene_type=str(data["scene_type"]),
            scene_id=str(data["scene_id"]),
            state=SessionState(str(data.get("state", SessionState.IDLE))),
            persona_id=str(data.get("persona_id", "default")),
            next_wake_at=float(data.get("next_wake_at", 0.0)),
            cooldown_until=float(data.get("cooldown_until", 0.0)),
            last_wake_at=float(data["last_wake_at"]) if data.get("last_wake_at") is not None else None,
            last_message_at=float(data.get("last_message_at", 0.0)),
            wake_count=int(data.get("wake_count", 0)),
            conversation=list(data.get("conversation", [])),
            pending_output=list(data.get("pending_output", [])),
        )


@dataclass(frozen=True)
class WakeInput:
    session_id: str
    category: TriggerCategory
    reason: str
    events: tuple[NormalizedEvent, ...] = ()
    probability: float | None = None
    deferred: bool = False
    defer_until_active: bool = False
    phase_ready: bool = False


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = ""


@dataclass(frozen=True)
class ToolResult:
    success: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    command: str | None = None

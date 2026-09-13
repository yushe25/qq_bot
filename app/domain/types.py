from __future__ import annotations

import json
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


def wake_input_to_json(wake: WakeInput) -> str:
    payload = {
        "category": wake.category.value,
        "reason": wake.reason,
        "events": [
            {
                "event_id": event.event_id,
                "session_id": event.session_id,
                "timestamp": event.timestamp,
                "sender_id": event.sender_id,
                "sender_name": event.sender_name,
                "kind": event.kind,
                "segments": [
                    {"kind": segment.kind, "data": segment.data}
                    for segment in event.segments
                ],
                "text": event.text,
                "bot_id": event.bot_id,
            }
            for event in wake.events
        ],
        "probability": wake.probability,
        "deferred": wake.deferred,
        "defer_until_active": wake.defer_until_active,
        "phase_ready": wake.phase_ready,
    }
    return json.dumps(payload, ensure_ascii=False)


def wake_input_from_json(session_id: str, payload: str) -> WakeInput | None:
    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            return None
        category = TriggerCategory(str(data["category"]))
        events_data = data.get("events", [])
        if not isinstance(events_data, list):
            return None
        events: list[NormalizedEvent] = []
        for item in events_data:
            if not isinstance(item, dict):
                return None
            segments_data = item.get("segments", [])
            if not isinstance(segments_data, list):
                return None
            segments: list[MessageSegment] = []
            for segment in segments_data:
                if not isinstance(segment, dict):
                    return None
                segment_data = segment.get("data", {})
                if not isinstance(segment_data, dict):
                    return None
                segments.append(
                    MessageSegment(
                        kind=str(segment.get("kind", "")),
                        data=dict(segment_data),
                    )
                )
            event_session_id = str(item.get("session_id", ""))
            if event_session_id and event_session_id != session_id:
                return None
            events.append(
                NormalizedEvent(
                    event_id=str(item.get("event_id", "")),
                    session_id=session_id,
                    timestamp=float(item.get("timestamp", 0.0)),
                    sender_id=str(item.get("sender_id", "")),
                    sender_name=str(item.get("sender_name", "")),
                    kind=str(item.get("kind", "")),
                    segments=tuple(segments),
                    text=str(item.get("text", "")),
                    raw={},
                    bot_id=str(item.get("bot_id", "")),
                )
            )
        probability = data.get("probability")
        return WakeInput(
            session_id=session_id,
            category=category,
            reason=str(data["reason"]),
            events=tuple(events),
            probability=float(probability) if probability is not None else None,
            deferred=bool(data.get("deferred", False)),
            defer_until_active=bool(data.get("defer_until_active", False)),
            phase_ready=bool(data.get("phase_ready", False)),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


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

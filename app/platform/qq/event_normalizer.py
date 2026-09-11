from __future__ import annotations

import unicodedata
from typing import Any

from app.domain.types import MessageSegment, NormalizedEvent, is_media_segment, scene_session_id, segment_text


def _segments(raw_message: Any) -> tuple[MessageSegment, ...]:
    if isinstance(raw_message, str):
        return (MessageSegment(kind="text", data={"text": raw_message}),)
    if not isinstance(raw_message, list):
        return ()
    segments: list[MessageSegment] = []
    for item in raw_message:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type", "text"))
        data = dict(item.get("data", {}))
        segments.append(MessageSegment(kind=kind, data=data))
    return tuple(segments)


def _text(segments: tuple[MessageSegment, ...], raw_text: str) -> str:
    if raw_text and not any(is_media_segment(segment) for segment in segments):
        return raw_text
    if segments:
        return "".join(segment_text(segment) for segment in segments)
    return raw_text


def normalize_message(raw: dict[str, Any]) -> NormalizedEvent:
    message_type = str(raw.get("message_type", "group"))
    scene_id = str(raw.get("group_id") or raw.get("user_id") or "")
    segments = _segments(raw.get("message") or raw.get("raw_message"))
    text = _text(segments, str(raw.get("raw_message") or ""))
    sender = raw.get("sender")
    if not isinstance(sender, dict):
        sender = {}
    return NormalizedEvent(
        event_id=f"{raw.get('message_id', raw.get('self_id', 'msg'))}:{scene_id}:{raw.get('user_id', '')}",
        session_id=scene_session_id(message_type, scene_id),
        timestamp=float(raw.get("time") or 0),
        sender_id=str(raw.get("user_id") or ""),
        sender_name=visible_name(
            sender.get("card"),
            sender.get("nickname"),
            fallback=str(raw.get("user_id") or ""),
        ),
        kind=message_type,
        segments=segments,
        text=text,
        raw=raw,
        bot_id=_bot_id(raw),
    )


def normalize_poke(raw: dict[str, Any]) -> NormalizedEvent:
    message_type = "group" if raw.get("group_id") else "private"
    scene_id = str(raw.get("group_id") or raw.get("user_id") or "")
    segments = (MessageSegment(kind="text", data={"text": "[拍一拍]"}),)
    return NormalizedEvent(
        event_id=f"poke:{scene_id}:{raw.get('user_id', '')}:{raw.get('time', 0)}",
        session_id=scene_session_id(message_type, scene_id),
        timestamp=float(raw.get("time") or 0),
        sender_id=str(raw.get("user_id") or ""),
        sender_name=str(raw.get("user_id") or ""),
        kind="poke",
        segments=segments,
        text="[拍一拍]",
        raw=raw,
        bot_id=_bot_id(raw),
    )


def normalize(raw: dict[str, Any]) -> NormalizedEvent:
    post_type = str(raw.get("post_type") or "")
    if post_type == "message":
        return normalize_message(raw)
    if post_type == "notice" and raw.get("notice_type") == "notify" and raw.get("sub_type") == "poke":
        return normalize_poke(raw)
    raise ValueError(f"unsupported event: {post_type}")


def _bot_id(raw: dict[str, Any]) -> str:
    return str(raw.get("self_id") or raw.get("self", {}).get("user_id") or "")


def visible_name(*candidates: Any, fallback: str = "?") -> str:
    """Return the first display name that contains a letter or number."""
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text and any(unicodedata.category(char)[0] in {"L", "N"} for char in text):
            return text
    return fallback

from __future__ import annotations

from app.domain.types import MessageSegment, segment_text


def escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("[", "&#91;").replace("]", "&#93;")


def encode_segments(segments: list[MessageSegment]) -> str:
    parts: list[str] = []
    for segment in segments:
        if segment.kind == "text":
            parts.append(escape_text(str(segment.data.get("text", ""))))
        elif segment.kind == "at":
            parts.append(f"[CQ:at,qq={escape_text(str(segment.data.get('qq', '')))}]")
        elif segment.kind == "image":
            parts.append(f"[CQ:image,file={escape_text(str(segment.data.get('file', '')))}]")
        elif segment.kind == "face":
            parts.append(f"[CQ:face,id={escape_text(str(segment.data.get('id', '')))}]")
        elif segment.kind == "quote":
            parts.append(f"[CQ:reply,id={escape_text(str(segment.data.get('id', '')))}]")
        else:
            parts.append(escape_text(segment_text(segment)))
    return "".join(parts)


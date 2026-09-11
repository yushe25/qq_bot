from __future__ import annotations

import re

_SPLIT_RE = re.compile(r"[，,。、；：]+")
_DECIMAL_PLACEHOLDER = "\u0002"
_ELLIPSIS_PLACEHOLDER = "\u0001"
_ALLOWED_PUNCT = set("！？!?…@")


def normalize_reply_segments(text: str) -> list[str]:
    """把出站消息整理成 QQ 短句：逗号/句号断成多条，保留情绪标点、省略号和字面 @。"""
    text = text.replace("...", _ELLIPSIS_PLACEHOLDER).replace("……", _ELLIPSIS_PLACEHOLDER)
    text = re.sub(r"(?<=\d)\.(?=\d)", _DECIMAL_PLACEHOLDER, text)

    segments: list[str] = []
    for raw in _SPLIT_RE.split(text):
        cleaned = _clean_segment(raw)
        if cleaned:
            segments.append(cleaned)
    return segments


def _clean_segment(segment: str) -> str:
    out: list[str] = []
    for char in segment:
        if char == _ELLIPSIS_PLACEHOLDER:
            out.append("...")
        elif char == _DECIMAL_PLACEHOLDER:
            out.append(".")
        elif char.isalnum() or char.isspace() or char in _ALLOWED_PUNCT:
            out.append(char)
    return re.sub(r"\s+", " ", "".join(out)).strip()

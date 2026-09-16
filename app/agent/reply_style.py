from __future__ import annotations

import re

_SPLIT_CHARS = set("，,。、；：")
_DECIMAL_PLACEHOLDER = "\u0002"
_ELLIPSIS_PLACEHOLDER = "\u0001"
_ALLOWED_PUNCT = set("！？!?…@")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_PROTECTED_PATTERNS = (
    re.compile(r"`[^`]*`"),
    re.compile(r"https?://[^\s，,。、；：]+"),
    re.compile(r"[A-Za-z]:\\[^\s，,。、；：]+"),
    re.compile(r"(?:\.{0,2}/|/)[^\s，,。、；：]+"),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b\d+(?:\.\d+)+\b"),
    re.compile(r"\b\w+(?:\.\w+)+\b"),
)
_SPACE_SPLIT_BLOCK_RE = re.compile(
    r"`|https?://|[A-Za-z]:\\|(?:\.{0,2}/|/)|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|\b\d+(?:\.\d+)+\b"
)


def normalize_reply_segments(text: str, *, max_segments: int | None = None) -> list[str]:
    """把出站消息整理成安全、非空、不过量的 QQ 短句。"""
    if max_segments is not None and max_segments <= 0:
        return []
    block_pause_split = bool(_SPACE_SPLIT_BLOCK_RE.search(text))
    text = text.replace("...", _ELLIPSIS_PLACEHOLDER).replace("……", _ELLIPSIS_PLACEHOLDER)
    text = re.sub(r"(?<=\d)\.(?=\d)", _DECIMAL_PLACEHOLDER, text)
    protected = _protected_mask(text)

    segments: list[tuple[str, list[bool]]] = []
    start = 0
    for index, char in enumerate(text):
        if protected[index] or char not in _SPLIT_CHARS:
            continue
        segments.extend(
            _split_pause_spaces(
                text[start:index],
                protected[start:index],
                block_pause_split=block_pause_split,
            )
        )
        start = index + 1
    segments.extend(
        _split_pause_spaces(
            text[start:],
            protected[start:],
            block_pause_split=block_pause_split,
        )
    )

    cleaned_segments: list[str] = []
    for raw, raw_protected in segments:
        cleaned = _clean_segment(raw, raw_protected)
        if cleaned:
            cleaned_segments.append(cleaned)
    if max_segments is not None and len(cleaned_segments) > max_segments:
        tail = "".join(cleaned_segments[max_segments - 1 :])
        cleaned_segments = [*cleaned_segments[: max_segments - 1], tail]
    return cleaned_segments


def _protected_mask(text: str) -> list[bool]:
    mask = [False] * len(text)
    for pattern in _PROTECTED_PATTERNS:
        for match in pattern.finditer(text):
            for index in range(match.start(), match.end()):
                mask[index] = True
    return mask


def _split_pause_spaces(
    text: str,
    protected: list[bool],
    *,
    block_pause_split: bool,
) -> list[tuple[str, list[bool]]]:
    if not text or "@" in text or block_pause_split:
        return [(text, protected)]
    parts: list[tuple[str, list[bool]]] = []
    start = 0
    index = 0
    while index < len(text):
        if not text[index].isspace() or protected[index]:
            index += 1
            continue
        end = index + 1
        while end < len(text) and text[end].isspace() and not protected[end]:
            end += 1
        left = text[start:index]
        right = text[end:]
        if _is_pause_boundary(left, right):
            parts.append((left, protected[start:index]))
            start = end
        index = end
    parts.append((text[start:], protected[start:]))
    return parts


def _is_pause_boundary(left: str, right: str) -> bool:
    left_clean = _clean_segment(left, [False] * len(left))
    right_clean = _clean_segment(right, [False] * len(right))
    if len(left_clean) < 2 or len(right_clean) < 2:
        return False
    return bool(_CJK_RE.search(left_clean) or _CJK_RE.search(right_clean))


def _clean_segment(segment: str, protected: list[bool]) -> str:
    out: list[str] = []
    has_content = False
    for index, char in enumerate(segment):
        if char == _ELLIPSIS_PLACEHOLDER:
            out.append("...")
            continue
        if char == _DECIMAL_PLACEHOLDER:
            out.append(".")
            continue
        if protected[index]:
            out.append(char)
            if not char.isspace():
                has_content = True
            continue
        if char.isalnum():
            out.append(char)
            has_content = True
        elif char.isspace():
            out.append(" ")
        elif char in _ALLOWED_PUNCT:
            out.append(char)
    if not has_content:
        return ""
    return re.sub(r"\s+", " ", "".join(out)).strip()

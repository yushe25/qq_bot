from __future__ import annotations

import re

DEFAULT_PATTERNS = [
    r"(?i)\b(api[_-]?key|access[_-]?token|secret|password|passwd|private[_-]?key)\b\s*[:=]\s*\S+",
    r"(?i)\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    r"[A-Za-z]:\\[^ \n]{2,}",
    r"/home/[^\s]+|/root/[^\s]+|/etc/[^\s]+",
    r"\[CQ:[^\]\[]*\]",
    r"1[3-9]\d{9}",
    r"\d{17}[\dXx]",
]


class ContentFilter:
    def __init__(self, extra_patterns: list[str] | None = None) -> None:
        patterns = list(DEFAULT_PATTERNS)
        if extra_patterns:
            patterns.extend(extra_patterns)
        self.patterns = [re.compile(p) for p in patterns]

    def check(self, text: str) -> tuple[bool, str]:
        for pattern in self.patterns:
            match = pattern.search(text)
            if match:
                return False, f"命中敏感模式: {pattern.pattern}"
        return True, "ok"

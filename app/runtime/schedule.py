from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from app.domain.types import TriggerCategory

DEFAULT_ACTIVE_WINDOWS = ((9, 12), (19, 24))
DEFAULT_QUIET_WINDOW = (1, 8)
FIXED_UTC_OFFSETS = {
    "Asia/Shanghai": 8,
    "Asia/Hong_Kong": 8,
    "Asia/Tokyo": 9,
    "UTC": 0,
}


@dataclass(frozen=True)
class DelayRange:
    min_seconds: float
    max_seconds: float


@dataclass(frozen=True)
class ScheduleProfile:
    timezone: str = "Asia/Shanghai"
    active_windows: tuple[tuple[int, int], ...] = DEFAULT_ACTIVE_WINDOWS
    quiet_window: tuple[int, int] | None = DEFAULT_QUIET_WINDOW
    reply_delay: DelayRange = DelayRange(3.0, 15.0)
    mention_delay: DelayRange = DelayRange(4.0, 12.0)
    sticker_only_probability: float = 0.15
    sticker_with_text_probability: float = 0.30


def load_schedule_profile(path: str | Path) -> ScheduleProfile:
    """Load schedule from persona YAML, falling back to defaults on any malformed input."""
    persona_path = Path(path)
    if not persona_path.is_file():
        return ScheduleProfile()
    try:
        data: Any = yaml.safe_load(persona_path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return ScheduleProfile()
    if not isinstance(data, dict):
        return ScheduleProfile()
    raw = data.get("schedule")
    if not isinstance(raw, dict):
        return ScheduleProfile()

    active = _parse_windows(raw.get("active_windows"), DEFAULT_ACTIVE_WINDOWS)
    quiet = _parse_quiet_window(raw.get("quiet_window"))
    reply_delay = _delay_range(raw.get("reply_delay"), DelayRange(3.0, 15.0))
    mention_delay = _delay_range(raw.get("mention_delay"), DelayRange(4.0, 12.0))
    only_probability = _probability(raw.get("sticker_only_probability"), 0.15)
    with_text_probability = _probability(raw.get("sticker_with_text_probability"), 0.30)
    total = only_probability + with_text_probability
    if total > 1.0:
        only_probability /= total
        with_text_probability /= total
    return ScheduleProfile(
        timezone=str(raw.get("timezone") or "Asia/Shanghai"),
        active_windows=active,
        quiet_window=quiet,
        reply_delay=reply_delay,
        mention_delay=mention_delay,
        sticker_only_probability=only_probability,
        sticker_with_text_probability=with_text_probability,
    )


def window_phase(profile: ScheduleProfile, when: datetime) -> str:
    hour = when.hour + when.minute / 60.0 + when.second / 3600.0
    if profile.quiet_window is not None and _in_window(hour, *profile.quiet_window):
        return "quiet"
    if any(_in_window(hour, start, end) for start, end in profile.active_windows):
        return "active"
    return "inactive"


def now_local(profile: ScheduleProfile) -> datetime:
    return datetime.now(_timezone(profile))


def response_delay_seconds(profile: ScheduleProfile, category: TriggerCategory, is_owner: bool = False) -> float:
    mentioned = category in {TriggerCategory.MENTION, TriggerCategory.NAME, TriggerCategory.POKE} or is_owner
    delay = profile.mention_delay if mentioned else profile.reply_delay
    return round(random.uniform(delay.min_seconds, delay.max_seconds), 2)


def should_run_in_phase(phase: str, category: TriggerCategory, is_owner: bool) -> bool:
    if phase == "active":
        return True
    if phase == "quiet":
        return category == TriggerCategory.FORCE or is_owner
    if category == TriggerCategory.FORCE:
        return True
    return category in {TriggerCategory.MENTION, TriggerCategory.NAME, TriggerCategory.POKE}


def next_active_start_ms(profile: ScheduleProfile, when: datetime) -> float:
    """Return milliseconds from ``when`` until the next active window starts."""
    windows = sorted(profile.active_windows)
    if not windows:
        return when.timestamp() * 1000 + 3600_000
    for day_offset in range(2):
        day = when.date() + timedelta(days=day_offset)
        for start, _end in windows:
            candidate = datetime.combine(day, time(start), tzinfo=when.tzinfo)
            if candidate > when:
                return (candidate - when).total_seconds() * 1000
    fallback = datetime.combine(
        when.date() + timedelta(days=2),
        time(windows[0][0]),
        tzinfo=when.tzinfo,
    )
    return (fallback - when).total_seconds() * 1000


def _parse_windows(raw: Any, default: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    if not isinstance(raw, list):
        return default
    parsed: list[tuple[int, int]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        try:
            start, end = int(item[0]), int(item[1])
        except (TypeError, ValueError):
            continue
        if 0 <= start < 24 and start < end <= 24:
            parsed.append((start, end))
    return tuple(sorted(parsed)) if parsed else default


def _parse_quiet_window(raw: Any) -> tuple[int, int] | None:
    if raw is None:
        return DEFAULT_QUIET_WINDOW
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        start, end = int(raw[0]), int(raw[1])
    except (TypeError, ValueError):
        return None
    if 0 <= start < 24 and start < end <= 24:
        return start, end
    return None


def _delay_range(raw: Any, default: DelayRange) -> DelayRange:
    if not isinstance(raw, dict):
        return default
    try:
        min_seconds = float(raw.get("min_seconds", default.min_seconds))
        max_seconds = float(raw.get("max_seconds", default.max_seconds))
    except (TypeError, ValueError):
        return default
    if min_seconds < 0 or max_seconds < 0:
        return default
    max_seconds = min(max_seconds, 60.0)
    min_seconds = min(min_seconds, max_seconds)
    return DelayRange(min_seconds, max_seconds)


def _probability(raw: Any, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, value))


def _in_window(hour: float, start: int, end: int) -> bool:
    return start <= hour < end


def _timezone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - zoneinfo may be missing tzdata on Windows
        offset = FIXED_UTC_OFFSETS.get(name)
        if offset is not None:
            return timezone(timedelta(hours=offset))
        local = datetime.now(UTC).astimezone()
        local_offset = local.utcoffset()
        if local_offset is not None:
            return timezone(local_offset)
        return UTC

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from app.runtime.schedule import ScheduleProfile, load_schedule_profile, next_active_start_ms, window_phase


def _local(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 9, hour, minute, tzinfo=timezone(timedelta(hours=8)))


def _write_persona(path: Path, schedule: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"id": "default", "name": "小皮", "schedule": schedule}, allow_unicode=True),
        encoding="utf-8",
    )


def test_schedule_profile_defaults_without_persona(tmp_path: Path) -> None:
    profile = load_schedule_profile(tmp_path / "missing.yaml")

    assert profile.active_windows == ((9, 12), (19, 24))
    assert profile.quiet_window == (1, 8)
    assert profile.reply_delay.min_seconds == 3.0
    assert profile.reply_delay.max_seconds == 15.0
    assert profile.mention_delay.min_seconds == 4.0
    assert profile.mention_delay.max_seconds == 12.0
    assert profile.sticker_only_probability + profile.sticker_with_text_probability <= 1.0


def test_schedule_profile_loads_and_clamps_invalid_values(tmp_path: Path) -> None:
    path = tmp_path / "persona.yaml"
    _write_persona(
        path,
        {
            "timezone": "Asia/Shanghai",
            "active_windows": [[19, 24], [9, 12]],
            "quiet_window": [1, 8],
            "reply_delay": {"min_seconds": 10, "max_seconds": 5},
            "mention_delay": {"min_seconds": 2, "max_seconds": 120},
            "sticker_only_probability": 0.8,
            "sticker_with_text_probability": 0.5,
        },
    )

    profile = load_schedule_profile(path)

    assert profile.reply_delay.min_seconds <= profile.reply_delay.max_seconds
    assert profile.mention_delay.max_seconds <= 60.0
    assert profile.sticker_only_probability + profile.sticker_with_text_probability <= 1.0


def test_window_phase_classifies_active_quiet_and_inactive() -> None:
    profile = ScheduleProfile()

    assert window_phase(profile, _local(20, 0)) == "active"
    assert window_phase(profile, _local(1, 30)) == "quiet"
    assert window_phase(profile, _local(14, 0)) == "inactive"


def test_empty_quiet_window_disables_quiet_phase(tmp_path: Path) -> None:
    path = tmp_path / "persona.yaml"
    _write_persona(
        path,
        {
            "active_windows": [[0, 24]],
            "quiet_window": [],
            "reply_delay": {"min_seconds": 0, "max_seconds": 0},
            "mention_delay": {"min_seconds": 0, "max_seconds": 0},
            "sticker_only_probability": 0,
            "sticker_with_text_probability": 0,
        },
    )

    profile = load_schedule_profile(path)

    assert profile.quiet_window is None
    assert window_phase(profile, _local(2, 0)) == "active"


def test_next_active_start_is_relative_to_given_wall_clock() -> None:
    profile = ScheduleProfile()

    delay_ms = next_active_start_ms(profile, _local(2, 0))

    assert delay_ms == 7 * 3600_000

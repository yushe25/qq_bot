from __future__ import annotations

from app.config import AppConfig, LLMConfig, QQConfig, SafetyConfig, SchedulerConfig, StorageConfig
from app.main import build_runtime


def test_build_runtime_keeps_wake_and_send_limiters_separate(tmp_path) -> None:
    config = AppConfig(
        qq=QQConfig(enabled=False),
        llm=LLMConfig(provider="mock"),
        safety=SafetyConfig(
            allow_groups=["100000"],
            allow_private=[],
            max_sends_per_minute=10,
            max_sends_per_hour=100,
        ),
        scheduler=SchedulerConfig(
            max_wakes_per_minute=1,
            max_wakes_per_hour=10,
        ),
        storage=StorageConfig(dir=str(tmp_path), database_path=str(tmp_path / "qqf.db")),
        persona_path=str(tmp_path / "persona.yaml"),
    )

    runtime = build_runtime(config)

    assert runtime.wake_limiter is not runtime.send_limiter
    assert runtime.scheduler.wake_limiter is runtime.wake_limiter
    assert runtime.safety.send_limiter is runtime.send_limiter

    assert runtime.wake_limiter.allow("group:100000")
    assert not runtime.wake_limiter.allow("group:100000")
    assert runtime.send_limiter.allow("group:100000")

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from app.agent.providers.mock import MockProvider
from app.agent.sticker_library import StickerLibrary
from app.config import AppConfig, LLMConfig, QQConfig, SafetyConfig, SchedulerConfig, StorageConfig
from app.memory.store import MemoryStore
from app.platform.gateway import NullGateway
from app.runtime.bus import EventBus
from app.runtime.learning import LearningRunner
from app.runtime.orchestrator import Orchestrator
from app.runtime.safety_pipeline import SafetyPipeline
from app.runtime.schedule import ScheduleProfile
from app.runtime.scheduler import Scheduler
from app.runtime.session_manager import SessionManager
from app.safety.audit import AuditLog
from app.safety.content_filter import ContentFilter
from app.safety.media_guard import MediaGuard
from app.safety.rate_limiter import RateLimiter
from app.safety.whitelist import Whitelist
from app.storage.db import StateStore


@dataclass
class TestStack:
    __test__ = False

    config: AppConfig
    store: StateStore
    sessions: SessionManager
    memory: MemoryStore
    whitelist: Whitelist
    wake_limiter: RateLimiter
    send_limiter: RateLimiter
    audit: AuditLog
    bus: EventBus
    safety: SafetyPipeline
    gateway: NullGateway
    orchestrator: Orchestrator
    scheduler: Scheduler
    learning: LearningRunner
    sticker_library: StickerLibrary


@pytest.fixture
async def stack(tmp_path) -> AsyncIterator[TestStack]:
    db_path = tmp_path / "qqf.db"
    store = StateStore(str(db_path))
    await store.init()

    config = AppConfig(
        qq=QQConfig(enabled=False),
        llm=LLMConfig(provider="mock"),
        safety=SafetyConfig(
            allow_groups=["100000"],
            allow_private=[],
            max_sends_per_minute=50,
            max_sends_per_hour=500,
            max_message_chars=500,
        ),
        scheduler=SchedulerConfig(
            batch_window_ms=0,
            wake_probability=0.0,
            names=["小皮"],
            keywords=["开黑"],
            max_wakes_per_minute=50,
            max_wakes_per_hour=500,
            min_sleep_ms=1000,
            max_sleep_ms=60000,
        ),
        storage=StorageConfig(dir=str(tmp_path), database_path=str(db_path)),
        persona_path=str(tmp_path / "persona.yaml"),
    )

    sessions = SessionManager(store)
    await sessions.load()
    memory = MemoryStore(store)
    whitelist = Whitelist(config.safety, owner_qq=config.qq.owner_qq)
    wake_limiter = RateLimiter(config.scheduler.max_wakes_per_minute, config.scheduler.max_wakes_per_hour)
    send_limiter = RateLimiter(config.safety.max_sends_per_minute, config.safety.max_sends_per_hour)
    content_filter = ContentFilter()
    audit = AuditLog(store)
    bus = EventBus()
    gateway = NullGateway()
    sticker_dir = tmp_path / "stickers"
    image_dir = sticker_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / "mock-01.gif").write_bytes(b"GIF89a\x01\x00\x01\x00\x00\x00\x00")
    (sticker_dir / "manifest.yaml").write_text(
        "- id: mock-01\n"
        "  name: 无语猫\n"
        "  file: images/mock-01.gif\n"
        "  tags: [无语, 猫]\n"
        "  approved: true\n"
        "  usage_count: 0\n",
        encoding="utf-8",
    )
    sticker_library = StickerLibrary(
        sticker_dir,
        enabled=True,
        allowed_ids=["mock-01"],
        media_guard=MediaGuard(max_bytes=1024, max_pixels=10_000),
        store=store,
    )
    safety = SafetyPipeline(
        config.safety,
        whitelist,
        send_limiter,
        content_filter,
        audit,
        bus,
        sticker_library,
        store=store,
    )
    orchestrator = Orchestrator(
        provider=MockProvider(config.llm),
        sessions=sessions,
        safety=safety,
        memory=memory,
        config=config,
        gateway=gateway,
        whitelist=whitelist,
        bus=bus,
        audit=audit,
        sticker_library=sticker_library,
    )
    orchestrator.schedule_profile = ScheduleProfile(
        sticker_only_probability=0.0,
        sticker_with_text_probability=0.0,
    )
    learning = LearningRunner(
        config=config,
        provider=MockProvider(config.llm),
        memory=memory,
        sessions=sessions,
        store=store,
        audit=audit,
        content_filter=content_filter,
        bus=bus,
    )
    scheduler = Scheduler(
        config,
        sessions,
        orchestrator,
        whitelist,
        store,
        bus,
        wake_limiter,
        learning=learning,
    )

    yield TestStack(
        config=config,
        store=store,
        sessions=sessions,
        memory=memory,
        whitelist=whitelist,
        wake_limiter=wake_limiter,
        send_limiter=send_limiter,
        audit=audit,
        bus=bus,
        safety=safety,
        gateway=gateway,
        orchestrator=orchestrator,
        scheduler=scheduler,
        learning=learning,
        sticker_library=sticker_library,
    )

    await store.close()

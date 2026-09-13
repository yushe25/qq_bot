from __future__ import annotations

import argparse
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from app.agent.providers.router import build_llm_provider
from app.agent.sticker_library import StickerLibrary
from app.config import AppConfig
from app.console.router import build_console_router
from app.logging_setup import configure_logging
from app.memory.store import MemoryStore
from app.platform.gateway import NullGateway
from app.platform.qq.onebot_client import OneBotV11Gateway
from app.runtime.bus import EventBus
from app.runtime.learning import LearningRunner
from app.runtime.orchestrator import Orchestrator
from app.runtime.safety_pipeline import SafetyPipeline
from app.runtime.scheduler import Scheduler
from app.runtime.session_manager import SessionManager
from app.safety.audit import AuditLog
from app.safety.content_filter import ContentFilter
from app.safety.instance_lock import InstanceLock, InstanceLockError
from app.safety.rate_limiter import RateLimiter
from app.safety.whitelist import Whitelist
from app.storage.db import StateStore

logger = logging.getLogger("qqf")


@dataclass
class AppRuntime:
    config: AppConfig
    store: StateStore
    sessions: SessionManager
    memory: MemoryStore
    whitelist: Whitelist
    wake_limiter: RateLimiter
    send_limiter: RateLimiter
    content_filter: ContentFilter
    audit: AuditLog
    bus: EventBus
    safety: SafetyPipeline
    learning: LearningRunner
    orchestrator: Orchestrator
    scheduler: Scheduler
    gateway: OneBotV11Gateway | NullGateway
    started_at: float = field(default_factory=time.time)
    _scheduler_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    async def start(self) -> None:
        await self.store.init()
        await self.sessions.load()
        await self.scheduler.start()
        if isinstance(self.gateway, OneBotV11Gateway):
            self.gateway.on_event(self.scheduler.ingest)
            await self.gateway.connect()
        self._scheduler_task = asyncio.create_task(self.scheduler.run(), name="qqf-scheduler")
        logger.info("QQF started with %s LLM provider", self.config.llm.provider)

    async def stop(self) -> None:
        await self.scheduler.stop()
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning("scheduler task stopped with error", exc_info=True)
            self._scheduler_task = None
        await self.gateway.disconnect()
        await self.store.close()
        logger.info("QQF stopped")


def build_runtime(config: AppConfig) -> AppRuntime:
    store = StateStore(config.storage.database_path)
    sessions = SessionManager(store)
    memory = MemoryStore(store)
    whitelist = Whitelist(config.safety, owner_qq=config.qq.owner_qq)
    wake_limiter = RateLimiter(config.scheduler.max_wakes_per_minute, config.scheduler.max_wakes_per_hour)
    send_limiter = RateLimiter(config.safety.max_sends_per_minute, config.safety.max_sends_per_hour)
    content_filter = ContentFilter()
    audit = AuditLog(store)
    bus = EventBus()

    provider = build_llm_provider(config.llm)

    if config.qq.enabled:
        gateway: OneBotV11Gateway | NullGateway = OneBotV11Gateway(config.qq)
    else:
        gateway = NullGateway()

    sticker_library = StickerLibrary(config.sticker_dir, store=store)
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
    orchestrator = Orchestrator(provider, sessions, safety, memory, config, gateway, whitelist, bus, audit, sticker_library)
    learning = LearningRunner(
        config=config,
        provider=provider,
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
        memory=memory,
    )
    return AppRuntime(
        config=config,
        store=store,
        sessions=sessions,
        memory=memory,
        whitelist=whitelist,
        wake_limiter=wake_limiter,
        send_limiter=send_limiter,
        content_filter=content_filter,
        audit=audit,
        bus=bus,
        safety=safety,
        learning=learning,
        orchestrator=orchestrator,
        scheduler=scheduler,
        gateway=gateway,
    )


def create_app(config: AppConfig | None = None) -> FastAPI:
    cfg = config or AppConfig.load()
    runtime = build_runtime(cfg)
    app = FastAPI(title="QQF", version="0.1.0", lifespan=_lifespan)
    app.state.runtime = runtime
    app.include_router(build_console_router(cfg.console))
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI):
    runtime: AppRuntime = app.state.runtime
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="qqf", description="QQ Friend Simulator runtime")
    parser.add_argument("--config", default="config.json", help="JSON config path (default: config.json)")
    parser.add_argument("--host", default=None, help="console listen host")
    parser.add_argument("--port", type=int, default=None, help="console listen port")
    parser.add_argument("--mock", action="store_true", help="use the offline mock LLM provider")
    args = parser.parse_args()

    configure_logging()
    config_path = Path(args.config)
    config = AppConfig.load(config_path if config_path.exists() else None)
    if args.mock:
        config.llm.provider = "mock"
    host = args.host or config.console.host
    port = args.port or config.console.port
    lock_path = Path(config.storage.dir) / "qqf.lock"
    try:
        with InstanceLock(lock_path):
            uvicorn.run(create_app(config), host=host, port=port, log_level="info")
    except InstanceLockError as exc:
        logger.error("另一个 QQF 实例已启动（%s），拒绝重复启动", lock_path)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import replace
from typing import Any

from app.config import AppConfig
from app.domain.types import NormalizedEvent, Session, SessionState, TriggerCategory, WakeInput, now_ms
from app.memory.store import MemoryStore
from app.runtime.bus import EventBus
from app.runtime.learning import LearningRunner
from app.runtime.message_dedupe import MessageDedupe
from app.runtime.orchestrator import Orchestrator
from app.runtime.schedule import (
    load_schedule_profile,
    next_active_start_ms,
    now_local,
    response_delay_seconds,
    should_run_in_phase,
    window_phase,
)
from app.runtime.session_manager import SessionManager
from app.safety.rate_limiter import RateLimiter
from app.safety.whitelist import Whitelist
from app.storage.db import StateStore

logger = logging.getLogger("qqf.scheduler")

HIGH_PRIORITY = {
    TriggerCategory.MENTION,
    TriggerCategory.NAME,
    TriggerCategory.QUESTION,
    TriggerCategory.KEYWORD,
    TriggerCategory.POKE,
}


class Scheduler:
    def __init__(
        self,
        config: AppConfig,
        sessions: SessionManager,
        orchestrator: Orchestrator,
        whitelist: Whitelist,
        store: StateStore,
        bus: EventBus,
        wake_limiter: RateLimiter,
        learning: LearningRunner | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        self.config = config
        self.sessions = sessions
        self.orchestrator = orchestrator
        self.whitelist = whitelist
        self.store = store
        self.bus = bus
        self.wake_limiter = wake_limiter
        self.learning = learning
        self.memory = memory
        self.dedupe = MessageDedupe()
        self._inbox: dict[str, list[NormalizedEvent]] = {}
        self._deadlines: dict[str, float] = {}
        self._pending_wakes: dict[str, WakeInput] = {}
        self._pending_observations: dict[str, list[NormalizedEvent]] = {}
        self._running: set[str] = set()
        self._stopped = False
        self._started = False
        self._question_re = re.compile(r"[?？]+|吗|呢|谁|怎么|为什么|啥|哪|几")
        self._schedule = load_schedule_profile(config.persona_path)

    async def start(self) -> None:
        if self._started:
            return
        pending = await self.store.load_pending_wakes()
        for session_id, next_wake_at, wake in pending:
            session = self.sessions.get(session_id)
            if session is None or session_id in self._running:
                continue
            self._pending_wakes[session_id] = wake
            session.next_wake_at = next_wake_at
        self._started = True

    async def ingest(self, event: NormalizedEvent) -> None:
        scene_type, scene_id = _split_session(event.session_id)
        if not self.whitelist.is_allowed(scene_type, scene_id, event.sender_id):
            self._publish(
                "incoming-skipped",
                {"session_id": event.session_id, "reason": "scene not in whitelist", "text": event.text[:80]},
            )
            return
        if event.bot_id and event.sender_id and event.sender_id == event.bot_id:
            return
        if not self.dedupe.add(event):
            return
        self._collect_observation(event)
        session = self.sessions.ensure_session(scene_type, scene_id)
        session.last_message_at = now_ms()
        batch = self._inbox.setdefault(session.id, [])
        batch.append(event)
        self._deadlines[session.id] = max(
            self._deadlines.get(session.id, 0.0),
            now_ms() + self.config.scheduler.batch_window_ms,
        )
        if len(batch) >= 20:
            self._deadlines[session.id] = now_ms()

    async def run(self) -> None:
        while not self._stopped:
            try:
                await self.tick()
            except Exception:
                logger.exception("scheduler tick failed")
            await asyncio.sleep(1.0)

    async def tick(self) -> None:
        now = now_ms()
        await self._flush_observations()
        await self._dispatch_learning(now)
        for session_id, events in list(self._inbox.items()):
            if not events:
                continue
            if now < self._deadlines.get(session_id, 0.0) and len(events) < 20:
                continue
            self._inbox.pop(session_id, None)
            self._deadlines.pop(session_id, None)
            session = self.sessions.get(session_id)
            if session is None:
                continue
            category, reason = self._classify(events)
            wake = WakeInput(
                session_id=session.id,
                category=category,
                reason=reason,
                events=tuple(events),
                probability=self.config.scheduler.wake_probability if category == TriggerCategory.NORMAL else None,
            )
            await self._decide_and_run(session, wake)

        for session in list(self.sessions.all()):
            if session.state == SessionState.PAUSED:
                continue
            if session.state == SessionState.COOLDOWN:
                if now >= session.cooldown_until:
                    await self.sessions.set_state(session, SessionState.IDLE)
                continue
            if session.state not in (SessionState.IDLE, SessionState.DIVING):
                continue
            if not session.next_wake_at or now < session.next_wake_at:
                continue
            session.next_wake_at = 0.0
            await self.sessions.save(session)
            wake = self._pending_wakes.pop(session.id, None) or WakeInput(
                session_id=session.id,
                category=TriggerCategory.SCHEDULED,
                reason="定时唤醒到期",
            )
            await self._decide_and_run(session, wake)

    async def force_wake(self, session_id: str, reason: str = "console") -> dict[str, Any]:
        session = self.sessions.get(session_id)
        if session is None:
            return {"ok": False, "reason": "session not found"}
        wake = WakeInput(session_id=session.id, category=TriggerCategory.FORCE, reason=reason)
        await self._decide_and_run(session, wake)
        return {"ok": True}

    async def pause(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if session is None:
            return False
        session.state = SessionState.PAUSED
        session.next_wake_at = 0.0
        await self.sessions.save(session)
        self._publish("session-updated", {"session": session.to_dict()})
        return True

    async def resume(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if session is None:
            return False
        session.state = SessionState.IDLE
        session.next_wake_at = 0.0
        await self.sessions.save(session)
        self._publish("session-updated", {"session": session.to_dict()})
        return True

    async def reset_memory(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if session is None:
            return False
        await self.store.delete_memory(session_id)
        await self.store.append_audit(
            action="reset_memory",
            detail=f"console reset memory for {session_id}",
            result="ok",
            session_id=session_id,
            actor_type="console",
        )
        return True

    async def delete_learning_note(self, session_id: str, kind: str, key: str) -> bool:
        if self.memory is None or self.sessions.get(session_id) is None:
            return False
        notes = await self.store.list_learning_notes(session_id)
        existed = any(str(item["kind"]) == kind and str(item["key"]) == key for item in notes)
        await self.memory.delete_learning_note(session_id, kind, key)
        if not existed:
            return False
        await self.store.append_audit(
            action="learning_note_deleted",
            detail=f"{kind}/{key}",
            result="ok",
            session_id=session_id,
            actor_type="console",
        )
        self._publish("learning-updated", {"session_id": session_id, "deleted_note": key})
        return True

    async def delete_member_data(self, session_id: str, member_id: str) -> bool:
        if self.memory is None or self.sessions.get(session_id) is None:
            return False
        await self.memory.delete_member_data(session_id, member_id)
        await self.store.append_audit(
            action="learning_member_deleted",
            detail=f"member={member_id}",
            result="ok",
            session_id=session_id,
            actor_type="console",
        )
        self._publish("learning-updated", {"session_id": session_id, "deleted_member": member_id})
        return True

    async def clear_learning(self, session_id: str) -> bool:
        if self.memory is None or self.sessions.get(session_id) is None:
            return False
        await self.memory.clear_learning(session_id)
        await self.store.append_audit(
            action="learning_cleared",
            detail="console cleared learning data",
            result="ok",
            session_id=session_id,
            actor_type="console",
        )
        await self.store.update_learning_state(session_id, last_attempt_at=0.0, last_learning_at=0.0)
        self._publish("learning-updated", {"session_id": session_id, "cleared": True})
        return True

    async def stop(self) -> None:
        self._stopped = True
        if self.learning is not None:
            await self.learning.wait_idle()
        await self.sessions.save_all()

    def _collect_observation(self, event: NormalizedEvent) -> None:
        config = self.config.learning
        if not config.enabled or event.kind != "group":
            return
        if event.sender_id and event.sender_id in set(config.exclude_members):
            return
        if not event.segments or any(segment.kind != "text" for segment in event.segments):
            return
        if not event.text.strip():
            return
        self._pending_observations.setdefault(event.session_id, []).append(event)

    async def _flush_observations(self) -> None:
        if not self._pending_observations:
            return
        pending = self._pending_observations
        self._pending_observations = {}
        max_per_session = self.config.learning.max_observations_per_session
        for session_id, events in pending.items():
            for event in events:
                await self.store.append_observation(
                    observation_id=event.event_id or f"obs-{event.timestamp:.0f}",
                    session_id=session_id,
                    sender_id=event.sender_id,
                    text=event.text.replace("\n", " ").strip()[:240],
                    kind="group_message",
                    ts=event.timestamp,
                    max_per_session=max_per_session,
                )

    async def _dispatch_learning(self, now: float) -> None:
        config = self.config.learning
        if self.learning is None or not config.enabled:
            return
        interval_ms = config.interval_minutes * 60_000.0
        for session in list(self.sessions.all()):
            if session.state in (SessionState.PAUSED, SessionState.ACTIVE, SessionState.COOLDOWN):
                continue
            state = await self.store.get_learning_state(session.id)
            if interval_ms > 0 and now - float(state["last_attempt_at"]) < interval_ms:
                continue
            count = await self.store.count_observations(
                session.id,
                after_ts=float(state["last_learning_at"]),
            )
            if count < config.min_new_messages:
                continue
            if not self.wake_limiter.has_capacity(session.id):
                continue
            self.learning.dispatch(session)

    def _classify(self, events: list[NormalizedEvent]) -> tuple[TriggerCategory, str]:
        for event in events:
            for segment in event.segments:
                if segment.kind != "at":
                    continue
                qq = str(segment.data.get("qq", ""))
                if qq in {event.bot_id, "all"}:
                    return TriggerCategory.MENTION, "消息中包含 @"
        joined = " ".join(event.text for event in events)
        if any(_split_session(event.session_id)[0] == "private" for event in events):
            return TriggerCategory.MENTION, "私聊消息直接唤醒"
        for name in self.config.scheduler.names:
            if name and name in joined:
                return TriggerCategory.NAME, f"提到昵称：{name}"
        if self._question_re.search(joined):
            return TriggerCategory.QUESTION, "检测到提问"
        for keyword in self.config.scheduler.keywords:
            if keyword and keyword in joined:
                return TriggerCategory.KEYWORD, f"命中关键词：{keyword}"
        if any(event.kind == "poke" for event in events):
            return TriggerCategory.POKE, "收到拍一拍"
        return TriggerCategory.NORMAL, "普通消息概率唤醒"

    async def _decide_and_run(self, session: Session, wake: WakeInput) -> None:
        if session.state == SessionState.PAUSED:
            await self.store.delete_pending_wake(session.id)
            await self._log_wake(wake, "paused")
            return
        if len(self._running) >= self.config.scheduler.max_concurrent:
            await self._defer(session, 5_000, "全局并发已满", wake)
            await self._log_wake(wake, "deferred", session.next_wake_at)
            return
        if session.state == SessionState.COOLDOWN and wake.category != TriggerCategory.FORCE:
            await self._defer(session, max(1000.0, session.cooldown_until - now_ms()), "冷却中", wake)
            await self._log_wake(wake, "deferred", session.next_wake_at)
            return

        phase_now = now_local(self._schedule)
        phase = window_phase(self._schedule, phase_now)
        is_owner = (
            session.scene_type == "private" and session.scene_id == self.whitelist.owner_qq
        ) or any(event.sender_id == self.whitelist.owner_qq for event in wake.events)
        if not should_run_in_phase(phase, wake.category, is_owner):
            delay_ms = max(1000.0, next_active_start_ms(self._schedule, phase_now))
            await self._defer(
                session,
                delay_ms,
                f"作息{phase}时段延后",
                replace(wake, deferred=True, defer_until_active=True),
            )
            await self._log_wake(wake, "deferred", session.next_wake_at)
            return
        if phase != "active" and wake.category != TriggerCategory.FORCE and not wake.phase_ready:
            delay_ms = response_delay_seconds(self._schedule, wake.category, is_owner) * 1000
            await self._defer(
                session,
                delay_ms,
                f"作息{phase}时段慢响应",
                replace(wake, deferred=True, phase_ready=True),
            )
            await self._log_wake(wake, "deferred", session.next_wake_at)
            return

        category = wake.category
        probability = None
        if category == TriggerCategory.FORCE:
            decided = "wake"
        elif category == TriggerCategory.SCHEDULED or category in HIGH_PRIORITY:
            decided = "wake" if self.wake_limiter.allow(session.id) else "rate_limited"
        else:
            probability = self.config.scheduler.wake_probability
            decided = (
                "wake"
                if random.random() <= probability and self.wake_limiter.allow(session.id)
                else "no_action"
            )

        if decided == "wake":
            await self._log_wake(wake, "wake", None, probability)
            self._dispatch(session, wake)
        elif decided == "rate_limited":
            delay_ms = max(1000.0, (self.wake_limiter.next_available_at(session.id) - now_ms() / 1000.0) * 1000.0)
            await self._defer(session, delay_ms, "限流延时重试", wake)
            await self._log_wake(wake, "deferred", session.next_wake_at, probability)
        else:
            await self.store.delete_pending_wake(session.id)
            await self._log_wake(wake, "no_action", None, probability)

    def _dispatch(self, session: Session, wake: WakeInput) -> None:
        if session.id in self._running:
            return
        self._running.add(session.id)
        self._publish("wake", {"session_id": session.id, "category": wake.category.value, "reason": wake.reason})
        task = asyncio.create_task(self._run_wake(session, wake))
        task.add_done_callback(self._task_done)

    async def _run_wake(self, session: Session, wake: WakeInput) -> None:
        try:
            outcome = await self.orchestrator.run_wake(session, wake)
            try:
                await self.store.update_latest_wake_log_usage(
                    session_id=session.id,
                    prompt_tokens=outcome.prompt_tokens,
                    completion_tokens=outcome.completion_tokens,
                    total_tokens=outcome.total_tokens,
                )
            except Exception:
                logger.exception("failed to record token usage for %s", session.id)
            await self.store.delete_pending_wake(session.id)
        except Exception:
            logger.exception("wake failed for %s", session.id)
            attempts = await self.store.increment_pending_wake_attempts(session.id)
            if attempts <= 0:
                await self.store.delete_pending_wake(session.id)
            elif attempts >= 3:
                await self.store.delete_pending_wake(session.id)
                await self.store.append_audit(
                    action="pending_wake_failed",
                    detail=f"attempts={attempts}",
                    result="deleted",
                    session_id=session.id,
                    actor_type="scheduler",
                )
            else:
                session.state = SessionState.IDLE
                session.next_wake_at = now_ms() + min(300_000, 1_000 * (2**attempts))
                self._pending_wakes[session.id] = wake
                await self.sessions.save(session)
                await self.store.append_audit(
                    action="pending_wake_retry",
                    detail=f"attempts={attempts}",
                    result="scheduled",
                    session_id=session.id,
                    actor_type="scheduler",
                )
        finally:
            self._running.discard(session.id)
            current = self.sessions.get(session.id)
            if current is not None:
                self._publish("session-updated", {"session": current.to_dict()})

    def _task_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception() if not task.cancelled() else None
        if exc is not None:
            logger.error("wake task failed: %s", exc)

    async def _defer(self, session: Session, delay_ms: float, reason: str, wake: WakeInput | None = None) -> None:
        session.next_wake_at = now_ms() + max(1000.0, float(delay_ms))
        if session.state == SessionState.COOLDOWN and now_ms() >= session.cooldown_until:
            session.state = SessionState.IDLE
        if wake is not None:
            self._pending_wakes[session.id] = wake
            await self.store.save_deferred_wake(session, wake)
        else:
            await self.sessions.save(session)
        logger.info("session %s deferred (%s) until %.0f", session.id, reason, session.next_wake_at)

    async def _log_wake(
        self,
        wake: WakeInput,
        decided: str,
        next_wake_at: float | None = None,
        probability: float | None = None,
    ) -> None:
        await self.store.append_wake_log(
            session_id=wake.session_id,
            category=wake.category.value,
            reason=wake.reason,
            decided=decided,
            probability=probability if probability is not None else wake.probability,
            next_wake_at=next_wake_at,
        )
        self._publish(
            "wake-log",
            {
                "session_id": wake.session_id,
                "category": wake.category.value,
                "reason": wake.reason,
                "decided": decided,
                "next_wake_at": next_wake_at,
            },
        )

    def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        self.bus.publish({"type": event_type, **payload})


def _split_session(session_id: str) -> tuple[str, str]:
    scene_type, _, scene_id = session_id.partition(":")
    return scene_type or "group", scene_id or session_id

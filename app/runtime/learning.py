from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.agent.llm_provider import ChatRequest, ChatResult, LLMProvider
from app.config import AppConfig
from app.domain.types import Session, SessionState, now_ms
from app.memory.store import LEARNING_KINDS, MemoryStore
from app.runtime.bus import EventBus
from app.runtime.session_manager import SessionManager
from app.safety.audit import AuditLog
from app.safety.content_filter import ContentFilter
from app.storage.db import StateStore

logger = logging.getLogger("qqf.learning")

_LEARNING_SYSTEM = (
    "你是群聊风格观察器，只做内部学习，不对外发言。"
    "只提炼表达方式、群内习惯和话题趋势，不学身份、账号、隐私。"
    "输出严格 JSON，不要解释，不要 Markdown。"
)
_MAX_EVIDENCE = 10
_MAX_EVIDENCE_TEXT = 160
_MAX_KEY_CHARS = 40
_MAX_VALUE_CHARS = 200


class LearningRunner:
    """Independent, non-speaking LLM pass that distils group style notes."""

    def __init__(
        self,
        config: AppConfig,
        provider: LLMProvider,
        memory: MemoryStore,
        sessions: SessionManager,
        store: StateStore,
        audit: AuditLog,
        content_filter: ContentFilter,
        bus: EventBus | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.memory = memory
        self.sessions = sessions
        self.store = store
        self.audit = audit
        self.content_filter = content_filter
        self.bus = bus
        self._active: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def dispatch(self, session: Session) -> bool:
        """Schedule one learning pass; returns False when it is already running."""
        if session.id in self._active:
            return False
        self._active.add(session.id)
        task = asyncio.create_task(self._run_dispatch(session))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def run_once(self, session: Session) -> dict[str, Any]:
        if session.id in self._active:
            return _result(ok=False, reason="learning already running")
        self._active.add(session.id)
        try:
            return await self._run(session)
        finally:
            self._active.discard(session.id)

    async def _run_dispatch(self, session: Session) -> None:
        try:
            await self._run(session)
        except Exception as exc:
            logger.exception("learning dispatch failed for %s", session.id)
            await self._record_failure(session.id, exc)
        finally:
            self._active.discard(session.id)

    async def _run(self, session: Session) -> dict[str, Any]:
        config = self.config.learning
        if not config.enabled:
            return _result(ok=False, reason="learning disabled")
        if session.state in (SessionState.ACTIVE, SessionState.COOLDOWN, SessionState.PAUSED):
            return _result(ok=False, reason=f"session {session.state} blocks learning")

        now = now_ms()
        state = await self.store.get_learning_state(session.id)
        interval_ms = config.interval_minutes * 60_000.0
        if interval_ms > 0 and now - float(state["last_attempt_at"]) < interval_ms:
            return _result(ok=False, reason="learning interval not elapsed")

        # Advance the attempt clock before the model call so a stuck provider
        # cannot be retriggered on every scheduler tick.
        await self.store.update_learning_state(session.id, last_attempt_at=now)
        observations = await self.store.list_observations(
            session.id,
            after_ts=float(state["last_learning_at"]),
            limit=config.max_observations_per_session,
        )
        if not observations:
            return _result(ok=False, reason="no observations")

        try:
            raw_notes = await self._request_notes(session, observations)
        except Exception as exc:  # noqa: BLE001 - model boundary; audit every failure
            await self._record_failure(session.id, exc)
            return _result(ok=False, reason=str(exc))

        accepted = 0
        for raw_note in raw_notes:
            if not isinstance(raw_note, dict):
                await self._reject(session.id, "笔记不是 JSON 对象", "invalid-note")
                continue
            if await self._commit_note(session.id, raw_note):
                accepted += 1

        learned_at = now_ms()
        await self.store.update_learning_state(session.id, last_learning_at=learned_at)
        await self.memory.prune_learning_notes(session.id, config.max_notes_per_session)
        await self.store.delete_expired_learning(session.id)
        await self.audit.record(
            action="learning",
            detail=f"observations={len(observations)} accepted={accepted}",
            result=f"notes={accepted}",
            session_id=session.id,
            actor_type="agent",
        )
        self._publish(
            "learning",
            {"session_id": session.id, "observed": len(observations), "notes": accepted},
        )
        return _result(ok=True, accepted=accepted, reason="ok")

    async def _request_notes(
        self,
        session: Session,
        observations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        prompt = _build_learning_prompt(observations)
        last_error: Exception | None = None
        for _ in range(2):
            result = await self.provider.complete(
                ChatRequest(
                    system=_LEARNING_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                    tools=[],
                    temperature=0.2,
                    max_tokens=256,
                )
            )
            try:
                return _parse_notes(result)
            except (TypeError, ValueError) as exc:
                last_error = exc
        raise ValueError(f"JSON 解析失败: {last_error}")

    async def _commit_note(self, session_id: str, raw_note: dict[str, Any]) -> bool:
        kind = str(raw_note.get("kind", "")).strip()
        if kind not in LEARNING_KINDS:
            await self._reject(session_id, f"不支持的 kind: {kind or '(empty)'}", "kind")
            return False
        key = str(raw_note.get("key", "")).replace("\n", " ").strip()[:_MAX_KEY_CHARS]
        value = str(raw_note.get("value", "")).replace("\n", " ").strip()[:_MAX_VALUE_CHARS]
        if not key or not value:
            await self._reject(session_id, "笔记 key/value 为空", "empty")
            return False
        try:
            confidence = float(raw_note.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < self.config.learning.confidence_threshold:
            await self._reject(session_id, f"低置信度: {confidence:.2f}", "confidence")
            return False
        ok, reason = self.content_filter.check(f"{key} {value}")
        if not ok:
            await self._reject(session_id, f"敏感内容: {reason}", "sensitive")
            return False

        evidence = _normalize_evidence(raw_note.get("evidence"))
        source_members = sorted({str(item["sender_id"]) for item in evidence if item.get("sender_id")})
        await self.memory.commit_learning_note(
            session_id=session_id,
            kind=kind,
            key=key,
            value=value,
            confidence=confidence,
            source_members=source_members,
            evidence=evidence,
            ttl_days=self.config.learning.notes_ttl_days,
        )
        return True

    async def _reject(self, session_id: str, reason: str, category: str) -> None:
        await self.audit.record(
            action="learning_note_rejected",
            detail=reason,
            result=f"rejected: {category}",
            session_id=session_id,
            actor_type="agent",
        )
        self._publish("learning-note-rejected", {"session_id": session_id, "reason": reason})

    async def _record_failure(self, session_id: str, exc: Exception) -> None:
        await self.audit.record(
            action="learning_failed",
            detail=f"学习失败: {exc}",
            result=f"failed: {exc}",
            session_id=session_id,
            actor_type="agent",
        )

    def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.bus:
            self.bus.publish({"type": event_type, **payload})


def _result(ok: bool, accepted: int = 0, reason: str = "") -> dict[str, Any]:
    return {"ok": ok, "accepted": accepted, "notes_written": accepted, "reason": reason}


def _parse_notes(result: ChatResult) -> list[dict[str, Any]]:
    content = (result.content or "").strip()
    if result.kind != "text" or not content:
        raise ValueError("empty learning response")
    if content.startswith("```"):
        content = content.strip("`")
        content = content.removeprefix("json")
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        content = content[start : end + 1]
    data = json.loads(content)
    if not isinstance(data, dict):
        raise TypeError("learning response is not a JSON object")
    notes = data.get("notes")
    if not isinstance(notes, list):
        raise TypeError("learning response has no notes list")
    return [note for note in notes if isinstance(note, dict)]


def _normalize_evidence(raw: Any) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return evidence
    for item in raw[:_MAX_EVIDENCE]:
        if not isinstance(item, dict):
            continue
        sender_id = str(item.get("sender_id", "")).strip()[:64]
        message_id = str(item.get("message_id", "")).strip()[:64]
        text = str(item.get("text", "")).replace("\n", " ").strip()[:_MAX_EVIDENCE_TEXT]
        if not text and not sender_id:
            continue
        evidence.append({"sender_id": sender_id, "message_id": message_id, "text": text})
    return evidence


def _build_learning_prompt(observations: list[dict[str, Any]]) -> str:
    rows = "\n".join(
        f"- {item.get('sender_id', '?')}: {str(item.get('text', '')).replace(chr(10), ' ')[:160]}"
        for item in observations[-80:]
    )
    return f"""请观察以下群消息，提炼 1 到 3 条学习笔记，只学表达方式、群内习惯和话题趋势。

只输出严格 JSON，格式如下：
{{"notes": [{{"kind": "speech_style|group_norm|topic_trend", "key": "短标题", "value": "可直接注入的短句", "confidence": 0.0, "evidence": [{{"sender_id": "10001", "message_id": "m1", "text": "原始证据短句"}}]}}]}}

不要输出身份、账号、手机号、隐私、路径、链接、CQ 码或任何密钥。

观察样本：
{rows}
""".strip()

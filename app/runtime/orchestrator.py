from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from typing import Any

from app.agent.llm_provider import ChatRequest, LLMProvider
from app.agent.prompt_builder import build_invocation_context, build_system_prompt, load_persona
from app.agent.sticker_library import StickerLibrary
from app.agent.tool_schemas import build_tool_schemas
from app.agent.tools import ToolContext, execute_tool
from app.config import AppConfig
from app.domain.types import Session, SessionState, ToolCall, TriggerCategory, WakeInput, now_ms
from app.memory.sticker_preferences import StickerPreferenceService
from app.memory.store import MemoryStore
from app.platform.gateway import QQGateway
from app.platform.qq.event_normalizer import visible_name
from app.runtime.bus import EventBus
from app.runtime.safety_pipeline import SafetyPipeline
from app.runtime.session_manager import SessionManager, trim_conversation
from app.safety.audit import AuditLog
from app.safety.whitelist import Whitelist

logger = logging.getLogger("qqf.orchestrator")
_CANCELLED_TOOL_RESULT = "已取消：机器人已安排潜水，本批次后续工具未执行"


@dataclass
class WakeOutcome:
    session_id: str
    sent: int = 0
    dived: bool = False
    next_wake_at: float | None = None
    steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error: str | None = None
    timings: dict[str, float] = field(default_factory=dict)


class Orchestrator:
    def __init__(
        self,
        provider: LLMProvider,
        sessions: SessionManager,
        safety: SafetyPipeline,
        memory: MemoryStore,
        config: AppConfig,
        gateway: QQGateway,
        whitelist: Whitelist,
        bus: EventBus | None = None,
        audit: AuditLog | None = None,
        sticker_library: StickerLibrary | None = None,
        sticker_preferences: StickerPreferenceService | None = None,
    ) -> None:
        self.provider = provider
        self.sessions = sessions
        self.safety = safety
        self.memory = memory
        self.config = config
        self.gateway = gateway
        self.whitelist = whitelist
        self.bus = bus
        self.audit = audit
        self.sticker_library = sticker_library
        self.sticker_preferences = sticker_preferences
        self._no_action_counts: dict[str, int] = {}

    async def run_wake(
        self,
        session: Session,
        wake_input: WakeInput,
        timings: dict[str, float] | None = None,
    ) -> WakeOutcome:
        lock = self.sessions.lock(session.id)
        if lock.locked() or session.state == SessionState.ACTIVE:
            return WakeOutcome(session.id, error="session busy", timings=timings or {})
        async with lock:
            return await self._run_locked(session, wake_input, timings)

    async def _run_locked(
        self,
        session: Session,
        wake_input: WakeInput,
        timings: dict[str, float] | None = None,
    ) -> WakeOutcome:
        timing_data = timings if timings is not None else {}
        timing_data.setdefault("dispatch_at", now_ms())
        outcome = WakeOutcome(session.id, timings=timing_data)
        await self.sessions.set_state(session, SessionState.ACTIVE)
        started_ms = now_ms()
        persona = load_persona(self.config.persona_path)
        memory_snapshot = await self.memory.snapshot(session.id)
        learning_notes = await self.memory.learning_notes_text(
            session.id,
            limit=self.config.learning.max_notes_per_session,
        )
        history_limit = self.config.token_budget.history_messages
        invocation = build_invocation_context(
            wake_input,
            memory_snapshot,
            "",
            max_messages=self.config.token_budget.wake_messages,
            max_event_chars=self.config.token_budget.event_text_chars,
        )
        conversation: list[dict[str, Any]] = trim_conversation(
            session.conversation,
            max(1, history_limit - 1),
        )
        conversation.append({"role": "user", "content": invocation})
        ctx = ToolContext(
            session=session,
            gateway=self.gateway,
            config=self.config,
            memory=self.memory,
            whitelist=self.whitelist,
            sticker_library=self.sticker_library,
            wake_category=wake_input.category,
            mention_dive_allowed=(
                wake_input.category == TriggerCategory.MENTION
                and random.random() < self.config.scheduler.mention_dive_probability
            ),
        )
        dive_ms: int | None = None
        seen_tool_results: set[tuple[str, str]] = set()
        provider_temperature = None if self.config.llm.providers else self.config.llm.temperature
        provider_max_tokens = None if self.config.llm.providers else self.config.llm.max_tokens
        library = self.sticker_library
        sticker_ids = (
            [entry.sticker_id for entry in library.catalog_for_scene(session.scene_id)]
            if library is not None and library.available
            else []
        )
        experience_by_id: dict[str, str] = {}
        if (
            library is not None
            and self.sticker_preferences is not None
            and session.scene_type == "group"
        ):
            media_to_sticker = {
                media_id: entry.sticker_id
                for entry in library.catalog_for_scene(session.scene_id)
                if (media_id := library.media_id_for(entry.sticker_id)) is not None
            }
            experience_by_id = await self.sticker_preferences.experience_for_group(
                session.scene_id,
                media_to_sticker,
            )
        sticker_catalog = (
            library.catalog_text_for_scene(
                session.scene_id,
                experience_by_id=experience_by_id,
            )
            if library is not None and sticker_ids
            else ""
        )

        for step in range(self.config.scheduler.max_tool_steps):
            outcome.steps = step + 1
            final_step = step >= self.config.scheduler.max_tool_steps - 1
            try:
                timing_data.setdefault("provider_started_at", now_ms())
                result = await self.provider.complete(
                    ChatRequest(
                        system=build_system_prompt(
                            session,
                            persona,
                            learning_notes,
                            final_step=final_step,
                            wake_category=wake_input.category,
                            sticker_catalog=sticker_catalog,
                        ),
                        messages=trim_conversation(conversation, history_limit),
                        tools=build_tool_schemas(
                            allow_tools=not final_step,
                            sticker_enabled=bool(sticker_ids),
                            sticker_ids=sticker_ids,
                        ),
                        temperature=provider_temperature,
                        max_tokens=provider_max_tokens,
                        reasoning_effort=(
                            self.config.scheduler.initial_reasoning_effort
                            if step == 0
                            else self.config.scheduler.tool_reasoning_effort
                        ),
                    )
                )
                _accumulate_usage(outcome, result)
            except Exception as exc:  # noqa: BLE001 - provider boundary; surface any failure as a wake error
                timing_data["provider_finished_at"] = now_ms()
                outcome.error = str(exc)
                logger.warning("LLM failed for %s: %s", session.id, exc)
                if self.audit:
                    await self.audit.record(
                        action="llm_complete_failed",
                        detail=f"step={step + 1}",
                        result=f"failed after retries: {exc}",
                        session_id=session.id,
                        actor_type="agent",
                    )
                self._publish("agent-error", {"session_id": session.id, "error": str(exc), "step": step + 1})
                break
            timing_data["provider_finished_at"] = now_ms()

            if result.kind == "tool_calls":
                conversation.append({"role": "assistant", "content": result.content})
                call_ids = [
                    call.id or f"call-{step}-{index}"
                    for index, call in enumerate(result.calls)
                ]
                conversations = [
                    _tool_call_payload(call, call_ids[index])
                    for index, call in enumerate(result.calls)
                ]
                conversation[-1]["tool_calls"] = conversations
                for index, call in enumerate(result.calls):
                    timing_data.setdefault("tool_started_at", now_ms())
                    call_result = await execute_tool(ctx, call)
                    timing_data["tool_finished_at"] = now_ms()
                    call_id = call_ids[index]
                    result_text = _tool_result_text(
                        call.name,
                        call_result,
                        max_chars=self.config.token_budget.tool_result_chars,
                        event_text_chars=self.config.token_budget.event_text_chars,
                    )
                    dedupe_key = (call.name, result_text)
                    if dedupe_key in seen_tool_results:
                        result_text = f"重复工具结果：本次唤醒内已执行过相同的 {call.name} 调用，结果已省略。"
                    else:
                        seen_tool_results.add(dedupe_key)
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": result_text,
                        }
                    )
                    self._publish(
                        "agent-step",
                        {
                            "session_id": session.id,
                            "tool": call.name,
                            "ok": call_result.success,
                            "message": call_result.message,
                            "step": step + 1,
                        },
                    )
                    if call_result.command == "dive":
                        dive_ms = int(call_result.data.get("next_wake_ms", self.config.scheduler.min_sleep_ms))
                        for pending_index, pending_call in enumerate(result.calls[index + 1 :], start=index + 1):
                            conversation.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": call_ids[pending_index],
                                    "content": _CANCELLED_TOOL_RESULT,
                                }
                            )
                            self._publish(
                                "agent-step",
                                {
                                    "session_id": session.id,
                                    "tool": pending_call.name,
                                    "ok": False,
                                    "message": _CANCELLED_TOOL_RESULT,
                                    "step": step + 1,
                                },
                            )
                        break
                    if call_result.command == "wait":
                        duration = float(call_result.data.get("duration_ms", 0)) / 1000.0
                        await asyncio.sleep(min(duration, 120.0))
                if dive_ms is not None:
                    break
                continue

            text = (result.content or "").strip()
            conversation.append({"role": "assistant", "content": text})
            if (
                text
                and self.config.scheduler.max_text_fallback
                and len(ctx.outbox) < self.config.safety.max_sends_per_wake
            ):
                ctx.outbox.append(self._plain_send(session, text))
            break

        send_results = await self.safety.execute_outbox(ctx, self.gateway, timing_data)
        sent = sum(1 for item in send_results if item.get("ok"))
        outcome.sent = sent

        session.last_wake_at = started_ms
        session.wake_count += 1
        session.conversation = trim_conversation(conversation, history_limit)

        if sent > 0:
            session.next_wake_at = 0.0
            session.cooldown_until = started_ms + self.config.scheduler.cooldown_seconds * 1000
            session.state = SessionState.COOLDOWN
            self._no_action_counts[session.id] = 0
        elif dive_ms is not None:
            outcome.dived = True
            clamped = self._clamp_dive_ms(dive_ms, session.id)
            outcome.next_wake_at = started_ms + clamped
            session.next_wake_at = outcome.next_wake_at
            session.state = SessionState.DIVING
            self._no_action_counts[session.id] = 0
        else:
            count = self._no_action_counts.get(session.id, 0) + 1
            self._no_action_counts[session.id] = count
            if count >= max(2, self.config.scheduler.no_action_limit):
                delay_ms = min(2 * 60_000, self.config.scheduler.max_sleep_ms)
                logger.info("force shorter dive for %s (no-action count=%s)", session.id, count)
                self._no_action_counts[session.id] = 0
            else:
                delay_ms = self.config.scheduler.pre_sleep_wait_minutes * 60_000
            outcome.next_wake_at = started_ms + delay_ms
            session.next_wake_at = outcome.next_wake_at
            session.state = SessionState.DIVING

        await self.sessions.save(session)
        if self.audit:
            await self.audit.record(
                action="agent_wake",
                detail=f"steps={outcome.steps}, sent={sent}, dive={dive_ms is not None}",
                result=outcome.error or "ok",
                session_id=session.id,
                actor_type="agent",
            )
        self._publish("session-updated", {"session": session.to_dict()})
        return outcome

    def _clamp_dive_ms(self, requested_ms: int, session_id: str) -> int:
        config = self.config.scheduler
        min_ms = max(1000, config.min_sleep_ms)
        max_ms = config.max_sleep_ms
        count = self._no_action_counts.get(session_id, 0)
        if count >= max(2, config.no_action_limit) - 1:
            max_ms = min(5 * 60_000, max_ms)
        return max(min_ms, min(int(requested_ms), max_ms))

    def _plain_send(self, session: Session, text: str) -> dict[str, Any]:
        if session.scene_type == "group":
            return {"type": "send_group", "group_id": session.scene_id, "message": text}
        return {"type": "send_private", "user_id": session.scene_id, "message": text}

    def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.bus:
            self.bus.publish({"type": event_type, **payload})

def _tool_call_payload(call: ToolCall, call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(call.arguments, ensure_ascii=False),
        },
    }


def _tool_result_text(
    tool_name: str,
    result: Any,
    *,
    max_chars: int = 1600,
    event_text_chars: int = 160,
) -> str:
    text = str(result.message)
    if getattr(result, "data", None):
        data = result.data
        if tool_name == "qq_get_group_history" and isinstance(data.get("messages"), list):
            lines = [
                _format_message_line(item, max_chars=event_text_chars)
                for item in data["messages"]
            ]
            text += "\n" + "\n".join(" - " + line for line in lines)
        elif tool_name == "qq_get_members" and isinstance(data.get("members"), list):
            lines = [_format_member_line(item) for item in data["members"]]
            text += "\n" + "\n".join(" - " + line for line in lines)
        else:
            text += " " + json.dumps(data, ensure_ascii=False)
    return _clip_text(text, max_chars)


def _format_message_line(message: dict[str, Any], *, max_chars: int) -> str:
    message_id = str(message.get("message_id") or message.get("message_seq") or "?")
    sender = message.get("sender")
    if isinstance(sender, dict):
        user_id = str(sender.get("user_id") or "")
        nickname = visible_name(sender.get("card"), sender.get("nickname"), fallback=user_id or "?")
    else:
        user_id = str(message.get("user_id") or "")
        nickname = visible_name(message.get("nickname"), fallback=user_id or "?")
    raw = message.get("raw_message")
    if not isinstance(raw, str):
        raw = message.get("message")
    if not isinstance(raw, str):
        raw = json.dumps(raw, ensure_ascii=False)
    content = _clip_text(str(raw).replace("\n", " "), max_chars)
    return f"[{message_id}] {nickname}({user_id}): {content}"


def _format_member_line(member: dict[str, Any]) -> str:
    user_id = str(member.get("user_id") or "?")
    nickname = visible_name(member.get("card"), member.get("nickname"), fallback=user_id)
    role = member.get("role")
    if role:
        return f"{nickname}({user_id}) [{role}]"
    return f"{nickname}({user_id})"


def _accumulate_usage(outcome: WakeOutcome, result: Any) -> None:
    prompt_tokens = max(0, int(getattr(result, "prompt_tokens", 0) or 0))
    completion_tokens = max(0, int(getattr(result, "completion_tokens", 0) or 0))
    reported_total = max(0, int(getattr(result, "total_tokens", 0) or 0))
    outcome.prompt_tokens += prompt_tokens
    outcome.completion_tokens += completion_tokens
    outcome.total_tokens += reported_total or prompt_tokens + completion_tokens


def _clip_text(text: str, max_chars: int) -> str:
    limit = max(1, int(max_chars))
    if len(text) <= limit:
        return text
    if limit == 1:
        return "…"
    return text[: limit - 1] + "…"

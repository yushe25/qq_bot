from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.agent.reply_style import normalize_reply_segments
from app.agent.sticker_library import StickerLibrary
from app.agent.tools import ToolContext
from app.config import SafetyConfig
from app.domain.types import now_ms
from app.platform.gateway import QQGateway
from app.runtime.bus import EventBus
from app.safety.audit import AuditLog
from app.safety.content_filter import ContentFilter
from app.safety.rate_limiter import RateLimiter
from app.safety.whitelist import Whitelist
from app.storage.db import StateStore


@dataclass(frozen=True)
class SafetyVerdict:
    allowed: bool
    reason: str = "ok"


class SafetyPipeline:
    def __init__(
        self,
        config: SafetyConfig,
        whitelist: Whitelist,
        send_limiter: RateLimiter,
        content_filter: ContentFilter,
        audit: AuditLog,
        bus: EventBus | None = None,
        sticker_library: StickerLibrary | None = None,
        store: StateStore | None = None,
    ) -> None:
        self.config = config
        self.whitelist = whitelist
        self.send_limiter = send_limiter
        self.content_filter = content_filter
        self.audit = audit
        self.bus = bus
        self.sticker_library = sticker_library
        self.store = store

    async def check_outgoing(
        self,
        session_id: str,
        scene_type: str,
        scene_id: str,
        text: str,
        action: str = "send_message",
        detail: str = "",
    ) -> SafetyVerdict:
        if not self.whitelist.is_allowed(scene_type, scene_id):
            return await self._blocked(action, session_id, "会话不在白名单中", text, detail)
        if len(text) > self.config.max_message_chars:
            return await self._blocked(action, session_id, "消息超长", text, detail)
        ok, reason = self.content_filter.check(text)
        if not ok:
            return await self._blocked(action, session_id, reason, text, detail)
        if self.store is not None:
            count = await self.store.get_daily_send_count(session_id, _daily_day())
            if count >= self.config.max_sends_per_day:
                return await self._blocked(action, session_id, "每日发送量已达上限", text, detail)
        if not self.send_limiter.allow(session_id):
            return await self._blocked(action, session_id, "发送频率超限", text, detail)
        await self.audit.record(action=action, detail=detail or text[:200], result="allowed", session_id=session_id)
        return SafetyVerdict(allowed=True)

    async def reset_daily_sends(self, session_id: str) -> bool:
        if self.store is None:
            return False
        await self.store.reset_daily_send_count(session_id)
        await self.audit.record(
            action="reset_daily_sends",
            detail=session_id,
            result="ok",
            session_id=session_id,
            actor_type="console",
        )
        return True

    async def execute_outbox(self, ctx: ToolContext, gateway: QQGateway) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        sent_count = 0
        member_counts: dict[str, int] = {}
        member_ids: dict[str, set[str]] = {}
        for raw_item in ctx.outbox:
            if str(raw_item.get("type", "")) == "send_sticker":
                result = await self._execute_sticker(
                    ctx,
                    gateway,
                    raw_item,
                    sent_count,
                    member_counts,
                    member_ids,
                )
                results.append(result)
                if result.get("ok"):
                    sent_count += 1
                continue
            raw_text = str(raw_item.get("message", ""))
            filter_ok, filter_reason = self.content_filter.check(raw_text)
            if not filter_ok:
                reason = f"命中敏感模式: {filter_reason}"
                await self._blocked("send_message", ctx.session.id, reason, raw_text, "send_message")
                results.append({"ok": False, "action": str(raw_item.get("type", "")), "reason": reason})
                continue
            action_type = str(raw_item.get("type", ""))
            mention_ids: tuple[str, ...] = ()
            if action_type == "send_group":
                try:
                    mention_ids = _parse_mention_ids(raw_item.get("mention_user_ids", ()))
                except (TypeError, ValueError) as exc:
                    reason = str(exc)
                    await self._blocked(
                        "send_group_message",
                        ctx.session.id,
                        reason,
                        raw_text,
                        "群消息 mention 参数不合法",
                    )
                    results.append({"ok": False, "action": action_type, "reason": reason})
                    continue
                if mention_ids:
                    mention_ok, mention_reason = await self._check_mentions(
                        ctx,
                        gateway,
                        str(raw_item.get("group_id", "")),
                        mention_ids,
                        member_counts,
                        member_ids,
                    )
                    if not mention_ok:
                        results.append({"ok": False, "action": action_type, "reason": mention_reason})
                        continue
            for segment_index, text in enumerate(normalize_reply_segments(raw_text)):
                item = dict(raw_item)
                item["message"] = text
                action_type = str(item.get("type", ""))
                segment_mentions = mention_ids if segment_index == 0 else ()
                if action_type == "send_group":
                    scene_id = str(item["group_id"])
                    if sent_count >= self.config.max_sends_per_wake:
                        results.append(
                            {
                                "ok": False,
                                "action": action_type,
                                "reason": f"单次唤醒最多发送 {self.config.max_sends_per_wake} 条",
                            }
                        )
                        continue
                    if scene_id not in member_counts and self.config.max_group_members > 0:
                        member_ok, member_reason = await self._check_group_members(
                            ctx, gateway, scene_id, member_counts, member_ids, text
                        )
                        if not member_ok:
                            results.append({"ok": False, "action": action_type, "reason": member_reason})
                            continue
                    detail = f"群 {scene_id} 消息"
                    if segment_mentions:
                        detail += f" mentions={len(segment_mentions)}"
                    verdict = await self.check_outgoing(
                        ctx.session.id,
                        "group",
                        scene_id,
                        text,
                        action="send_group_message",
                        detail=detail,
                    )
                    if not verdict.allowed:
                        results.append({"ok": False, "action": action_type, "reason": verdict.reason})
                        continue
                    sent_count += 1
                    try:
                        reply_to = item.get("reply_to_message_id")
                        await gateway.send_group_message(
                            scene_id,
                            text,
                            str(reply_to) if reply_to is not None else None,
                            segment_mentions,
                        )
                    except Exception as exc:  # noqa: BLE001 - gateway boundary; audit every send failure
                        await self.audit.record(
                            action="send_group_message",
                            detail=f"群 {scene_id} 发送失败",
                            result=f"failed: {exc}",
                            session_id=ctx.session.id,
                        )
                        results.append({"ok": False, "action": action_type, "reason": str(exc)})
                        continue
                    await self._increment_daily_send(ctx.session.id)
                    self._publish("outgoing", {"session_id": ctx.session.id, "scene_id": scene_id, "text": text})
                    results.append({"ok": True, "action": action_type, "group_id": scene_id})
                elif action_type == "send_private":
                    scene_id = str(item["user_id"])
                    if sent_count >= self.config.max_sends_per_wake:
                        results.append(
                            {
                                "ok": False,
                                "action": action_type,
                                "reason": f"单次唤醒最多发送 {self.config.max_sends_per_wake} 条",
                            }
                        )
                        continue
                    verdict = await self.check_outgoing(
                        ctx.session.id,
                        "private",
                        scene_id,
                        text,
                        action="send_private_message",
                        detail=f"私聊 {scene_id} 消息",
                    )
                    if not verdict.allowed:
                        results.append({"ok": False, "action": action_type, "reason": verdict.reason})
                        continue
                    sent_count += 1
                    try:
                        reply_to = item.get("reply_to_message_id")
                        await gateway.send_private_message(
                            scene_id,
                            text,
                            str(reply_to) if reply_to is not None else None,
                        )
                    except Exception as exc:  # noqa: BLE001 - gateway boundary; audit every send failure
                        await self.audit.record(
                            action="send_private_message",
                            detail=f"私聊 {scene_id} 发送失败",
                            result=f"failed: {exc}",
                            session_id=ctx.session.id,
                        )
                        results.append({"ok": False, "action": action_type, "reason": str(exc)})
                        continue
                    await self._increment_daily_send(ctx.session.id)
                    self._publish("outgoing", {"session_id": ctx.session.id, "scene_id": scene_id, "text": text})
                    results.append({"ok": True, "action": action_type, "user_id": scene_id})
                else:
                    results.append({"ok": False, "action": action_type, "reason": "未知动作类型"})
        return results

    async def _execute_sticker(
        self,
        ctx: ToolContext,
        gateway: QQGateway,
        item: dict[str, Any],
        sent_count: int,
        member_counts: dict[str, int],
        member_ids: dict[str, set[str]],
    ) -> dict[str, Any]:
        action = "sticker_send"
        if item.get("group_id") is not None:
            scene_type = "group"
            scene_id = str(item["group_id"])
        elif item.get("user_id") is not None:
            scene_type = "private"
            scene_id = str(item["user_id"])
        else:
            await self.audit.record(action=action, detail="缺少目标会话", result="blocked", session_id=ctx.session.id)
            return {"ok": False, "action": "send_sticker", "reason": "缺少目标会话"}
        if not self.whitelist.is_allowed(scene_type, scene_id):
            reason = "会话不在白名单中"
            await self._blocked(action, ctx.session.id, reason, str(item.get("sticker_id", "")), f"{scene_type} {scene_id} 表情")
            return {"ok": False, "action": "send_sticker", "reason": reason}
        if sent_count >= self.config.max_sends_per_wake:
            return {"ok": False, "action": "send_sticker", "reason": f"单次唤醒最多发送 {self.config.max_sends_per_wake} 条"}
        if scene_type == "group" and self.config.max_group_members > 0:
            ok, reason = await self._check_group_members(
                ctx,
                gateway,
                scene_id,
                member_counts,
                member_ids,
                f"[表情:{item.get('sticker_id', '')}]",
            )
            if not ok:
                return {"ok": False, "action": "send_sticker", "reason": reason}
        if self.sticker_library is None or not self.sticker_library.available:
            reason = "表情库不可用"
            await self._blocked(action, ctx.session.id, reason, str(item.get("sticker_id", "")), f"{scene_type} {scene_id} 表情")
            return {"ok": False, "action": "send_sticker", "reason": reason}
        sticker_id = str(item.get("sticker_id", ""))
        sticker = self.sticker_library.resolve(sticker_id)
        if sticker is None:
            reason = f"表情未在白名单中或不可用: {sticker_id}"
            await self._blocked(action, ctx.session.id, reason, sticker_id, f"{scene_type} {scene_id} 表情")
            return {"ok": False, "action": "send_sticker", "reason": reason}
        if not self.send_limiter.allow(ctx.session.id):
            reason = "发送频率超限"
            await self._blocked(action, ctx.session.id, reason, sticker_id, f"{scene_type} {scene_id} 表情")
            return {"ok": False, "action": "send_sticker", "reason": reason}
        if self.store is not None:
            count = await self.store.get_daily_send_count(ctx.session.id, _daily_day())
            if count >= self.config.max_sends_per_day:
                reason = "每日发送量已达上限"
                await self._blocked(action, ctx.session.id, reason, sticker_id, f"{scene_type} {scene_id} 表情")
                return {"ok": False, "action": "send_sticker", "reason": reason}
        await self.audit.record(
            action=action,
            detail=f"{scene_type} {scene_id} 表情 {sticker_id}",
            result="allowed",
            session_id=ctx.session.id,
        )
        try:
            if scene_type == "group":
                await gateway.send_group_sticker(scene_id, str(sticker.path))
            else:
                await gateway.send_private_sticker(scene_id, str(sticker.path))
        except Exception as exc:  # noqa: BLE001 - gateway boundary; audit every send failure
            await self.audit.record(
                action=action,
                detail=f"{scene_type} {scene_id} 表情 {sticker_id} 发送失败",
                result=f"failed: {exc}",
                session_id=ctx.session.id,
            )
            return {"ok": False, "action": "send_sticker", "reason": str(exc)}
        await self.sticker_library.record_usage(sticker_id)
        await self._increment_daily_send(ctx.session.id)
        self._publish(
            "outgoing",
            {
                "session_id": ctx.session.id,
                "scene_id": scene_id,
                "sticker": sticker_id,
                "text": f"[表情:{sticker_id}:{sticker.name}]",
            },
        )
        return {"ok": True, "action": "send_sticker", "sticker_id": sticker_id, "scene_id": scene_id}

    async def _check_group_members(
        self,
        ctx: ToolContext,
        gateway: QQGateway,
        scene_id: str,
        member_counts: dict[str, int],
        member_ids: dict[str, set[str]],
        text: str,
    ) -> tuple[bool, str]:
        if scene_id not in member_counts:
            try:
                members = await gateway.get_group_members(scene_id)
                member_counts[scene_id] = len(members)
                member_ids[scene_id] = {
                    str(member.get("user_id"))
                    for member in members
                    if member.get("user_id") is not None
                }
            except Exception as exc:  # noqa: BLE001 - member guard fails closed
                reason = f"成员数校验失败: {exc}"
                await self._blocked(
                    "send_group_message",
                    ctx.session.id,
                    reason,
                    text,
                    f"群 {scene_id} 消息",
                )
                return False, reason
        if self.config.max_group_members > 0 and member_counts.get(scene_id, 0) > self.config.max_group_members:
            reason = f"群成员数超过上限 {self.config.max_group_members}"
            await self._blocked(
                "send_group_message",
                ctx.session.id,
                reason,
                text,
                f"群 {scene_id} 消息",
            )
            return False, reason
        return True, ""

    async def _check_mentions(
        self,
        ctx: ToolContext,
        gateway: QQGateway,
        scene_id: str,
        mention_user_ids: tuple[str, ...],
        member_counts: dict[str, int],
        member_ids: dict[str, set[str]],
    ) -> tuple[bool, str]:
        if not mention_user_ids:
            return True, ""
        member_ok, member_reason = await self._check_group_members(
            ctx,
            gateway,
            scene_id,
            member_counts,
            member_ids,
            f"mentions={len(mention_user_ids)}",
        )
        if not member_ok:
            return False, member_reason
        known_ids = member_ids.get(scene_id, set())
        if any(user_id not in known_ids for user_id in mention_user_ids):
            reason = "mention 目标不在当前群成员列表中"
            await self._blocked(
                "send_group_message",
                ctx.session.id,
                reason,
                "",
                f"群 {scene_id} mentions={len(mention_user_ids)}",
            )
            return False, reason
        return True, ""

    async def _blocked(self, action: str, session_id: str, reason: str, text: str, detail: str) -> SafetyVerdict:
        await self.audit.record(
            action=action,
            detail=detail or text[:200],
            result=f"blocked: {reason}",
            session_id=session_id,
            actor_type="agent",
        )
        self._publish(
            "safety-block",
            {"session_id": session_id, "action": action, "reason": reason, "text": text[:200], "ts": now_ms()},
        )
        return SafetyVerdict(allowed=False, reason=reason)

    def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.bus:
            self.bus.publish({"type": event_type, **payload})

    async def _increment_daily_send(self, session_id: str) -> None:
        if self.store is not None:
            await self.store.increment_daily_send_count(session_id, _daily_day())


def _daily_day() -> str:
    return datetime.now(UTC).astimezone().strftime("%Y-%m-%d")


def _parse_mention_ids(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise TypeError("mentionUserIds 必须是数组")
    if len(value) > 5:
        raise ValueError("单条消息最多 @ 5 人")
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError("mentionUserIds 必须是数字 QQ 号")
        user_id = item.strip()
        if not user_id:
            raise ValueError("mentionUserIds 不能包含空值")
        if not user_id.isdigit():
            raise ValueError("mentionUserIds 必须是数字 QQ 号")
        if user_id in cleaned:
            raise ValueError("mentionUserIds 不能重复")
        cleaned.append(user_id)
    return tuple(cleaned)

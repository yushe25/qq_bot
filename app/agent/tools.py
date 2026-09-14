from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.agent.sticker_library import StickerLibrary
from app.config import AppConfig
from app.domain.types import Session, ToolCall, ToolResult, TriggerCategory
from app.memory.store import MemoryStore
from app.platform.gateway import QQGateway
from app.safety.whitelist import Whitelist


class SendGroupArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    group_id: str = Field(alias="groupId")
    message: str = Field(min_length=1, max_length=500)


class ReplyArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    message: str = Field(min_length=1, max_length=500)
    target_message_id: str = Field(alias="targetMessageId", min_length=1)


class GetHistoryArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


class WaitArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    duration_ms: int = Field(alias="durationMs", ge=1, le=120_000)


class DiveArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    next_wake_ms: int = Field(alias="nextWakeMs", ge=1000)
    reason: str = ""


class MemoryUpdateArgs(BaseModel):
    kind: Literal["active_topic", "pending_thought", "impression"]
    key: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=300)

    @field_validator("key", "value")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("不能为空")
        return stripped


class SendPrivateArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    user_id: str = Field(alias="userId", min_length=1)
    message: str = Field(min_length=1, max_length=500)


class StickerArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    sticker_id: str = Field(alias="stickerId", min_length=1)


@dataclass
class ToolContext:
    session: Session
    gateway: QQGateway
    config: AppConfig
    memory: MemoryStore
    whitelist: Whitelist
    outbox: list[dict[str, object]] = field(default_factory=list)
    sticker_library: StickerLibrary | None = None
    wake_category: TriggerCategory | None = None
    mention_dive_allowed: bool = False


async def execute_tool(ctx: ToolContext, call: ToolCall) -> ToolResult:
    try:
        return await _dispatch(ctx, call)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.get('loc', []))}: {error.get('msg', 'invalid')}"
            for error in exc.errors()
        )
        return ToolResult(success=False, message=f"参数校验失败: {details}")
    except Exception as exc:  # noqa: BLE001 - tool boundary; normalize any failure for the model
        return ToolResult(success=False, message=f"工具执行失败: {exc}")


async def _dispatch(ctx: ToolContext, call: ToolCall) -> ToolResult:
    name = call.name
    if name == "qq_get_group_history":
        args = GetHistoryArgs.model_validate(call.arguments)
        history = await ctx.gateway.get_group_history(ctx.session.scene_id, limit=args.limit)
        return ToolResult(success=True, message="读取成功", data={"messages": history})
    if name == "qq_get_members":
        members = await ctx.gateway.get_group_members(ctx.session.scene_id)
        return ToolResult(success=True, message="读取成功", data={"members": members})
    if name == "qq_send_group_message":
        args = SendGroupArgs.model_validate(call.arguments)
        if ctx.session.scene_type != "group" or args.group_id != ctx.session.scene_id:
            return ToolResult(success=False, message="只能向当前会话群发送消息")
        if not ctx.whitelist.is_allowed("group", args.group_id):
            return ToolResult(success=False, message="群不在白名单中")
        if len(ctx.outbox) >= ctx.config.safety.max_sends_per_wake:
            return ToolResult(success=False, message=f"单次唤醒最多发送 {ctx.config.safety.max_sends_per_wake} 条")
        ctx.outbox.append({"type": "send_group", "group_id": args.group_id, "message": args.message})
        return ToolResult(success=True, message="已排队")
    if name == "qq_reply":
        args = ReplyArgs.model_validate(call.arguments)
        if not ctx.whitelist.is_allowed(ctx.session.scene_type, ctx.session.scene_id):
            return ToolResult(success=False, message="会话不在白名单中")
        if len(ctx.outbox) >= ctx.config.safety.max_sends_per_wake:
            return ToolResult(success=False, message=f"单次唤醒最多发送 {ctx.config.safety.max_sends_per_wake} 条")
        try:
            target = await ctx.gateway.get_message(args.target_message_id)
        except Exception as exc:  # noqa: BLE001 - gateway boundary; reply validation must fail gracefully
            return ToolResult(success=False, message=f"无法校验目标消息: {exc}")
        owner_key = "group_id" if ctx.session.scene_type == "group" else "user_id"
        if str(target.get(owner_key, "")) != str(ctx.session.scene_id):
            return ToolResult(success=False, message="目标消息不属于当前会话，拒绝回复")
        ctx.outbox.append(
            {
                "type": "send_group" if ctx.session.scene_type == "group" else "send_private",
                "group_id": ctx.session.scene_id if ctx.session.scene_type == "group" else None,
                "user_id": ctx.session.scene_id if ctx.session.scene_type == "private" else None,
                "message": args.message,
                "reply_to_message_id": args.target_message_id,
            }
        )
        return ToolResult(success=True, message="已排队")
    if name == "qq_send_private_message":
        args = SendPrivateArgs.model_validate(call.arguments)
        if ctx.session.scene_type == "private":
            if args.user_id != ctx.session.scene_id:
                return ToolResult(success=False, message="私聊只能回复当前会话")
        elif args.user_id != ctx.whitelist.owner_qq:
            return ToolResult(success=False, message="群聊中只能向 owner 发私聊")
        if not ctx.whitelist.is_allowed("private", args.user_id):
            return ToolResult(success=False, message="私聊目标不在白名单中")
        if len(ctx.outbox) >= ctx.config.safety.max_sends_per_wake:
            return ToolResult(success=False, message=f"单次唤醒最多发送 {ctx.config.safety.max_sends_per_wake} 条")
        ctx.outbox.append(
            {
                "type": "send_private",
                "user_id": args.user_id,
                "group_id": None,
                "message": args.message,
            }
        )
        return ToolResult(success=True, message="已排队")
    if name == "qq_wait":
        args = WaitArgs.model_validate(call.arguments)
        return ToolResult(success=True, message="等待中", command="wait", data={"duration_ms": args.duration_ms})
    if name == "qq_dive":
        if (
            ctx.wake_category == TriggerCategory.MENTION
            and not ctx.outbox
            and not ctx.mention_dive_allowed
        ):
            return ToolResult(success=False, message="被 @ 或收到直接问候时必须先回复，不能潜水")
        args = DiveArgs.model_validate(call.arguments)
        return ToolResult(
            success=True,
            message="安排潜水",
            command="dive",
            data={"next_wake_ms": args.next_wake_ms, "reason": args.reason},
        )
    if name == "memory_update":
        args = MemoryUpdateArgs.model_validate(call.arguments)
        await ctx.memory.update(ctx.session.id, args.kind, args.key, args.value)
        return ToolResult(success=True, message="记忆已更新")
    if name == "sticker_send":
        args = StickerArgs.model_validate(call.arguments)
        library = ctx.sticker_library
        if library is None:
            return ToolResult(success=False, message="表情库未配置")
        if not library.available:
            return ToolResult(success=False, message="表情功能未启用")
        sticker = library.resolve(args.sticker_id)
        if sticker is None:
            return ToolResult(success=False, message=f"表情未在白名单中或不可用: {args.sticker_id}")
        if len(ctx.outbox) >= ctx.config.safety.max_sends_per_wake:
            return ToolResult(success=False, message=f"单次唤醒最多发送 {ctx.config.safety.max_sends_per_wake} 条")
        item: dict[str, object] = {
            "type": "send_sticker",
            "sticker_id": sticker.sticker_id,
        }
        if ctx.session.scene_type == "group":
            item["group_id"] = ctx.session.scene_id
            item["user_id"] = None
        else:
            item["user_id"] = ctx.session.scene_id
            item["group_id"] = None
        ctx.outbox.append(item)
        return ToolResult(success=True, message="表情已排队")
    return ToolResult(success=False, message=f"未知工具: {name}")

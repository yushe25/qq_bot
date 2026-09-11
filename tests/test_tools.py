from __future__ import annotations

from app.agent.tools import ToolContext, execute_tool
from app.domain.types import ToolCall, TriggerCategory
from tests.conftest import TestStack


def _reply_call(message: str = "收到", target_message_id: str = "m-1") -> ToolCall:
    return ToolCall(
        name="qq_reply",
        arguments={"message": message, "targetMessageId": target_message_id},
    )


def _private_send_call(user_id: str = "100001", message: str = "私聊回复") -> ToolCall:
    return ToolCall(
        name="qq_send_private_message",
        arguments={"userId": user_id, "message": message},
    )


def _dive_call() -> ToolCall:
    return ToolCall(
        name="qq_dive",
        arguments={"nextWakeMs": 60_000, "reason": "先潜水"},
    )


async def test_qq_reply_accepts_message_from_current_session(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    stack.gateway.add_message("m-1", group_id="100000", user_id="10001", message="你好")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )

    result = await execute_tool(context, _reply_call())

    assert result.success is True
    assert context.outbox == [
        {
            "type": "send_group",
            "group_id": "100000",
            "user_id": None,
            "message": "收到",
            "reply_to_message_id": "m-1",
        }
    ]


async def test_qq_reply_rejects_message_from_another_session(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    stack.gateway.add_message("m-2", group_id="999999", user_id="10001", message="别处消息")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )

    result = await execute_tool(context, _reply_call(target_message_id="m-2"))

    assert result.success is False
    assert "不属于当前会话" in result.message
    assert context.outbox == []


async def test_qq_reply_rejects_when_message_fetch_fails(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")

    class FailingGateway:
        async def get_message(self, message_id: str) -> dict[str, object]:
            raise RuntimeError("OneBot get_msg failed: HTTP 500: unavailable")

    context = ToolContext(
        session=session,
        gateway=FailingGateway(),  # type: ignore[arg-type]
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )

    result = await execute_tool(context, _reply_call())

    assert result.success is False
    assert "无法校验目标消息" in result.message
    assert context.outbox == []


async def test_qq_send_group_message_rejects_over_wake_limit(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    context.outbox = [
        {"type": "send_group", "group_id": "100000", "message": "1"},
        {"type": "send_group", "group_id": "100000", "message": "2"},
        {"type": "send_group", "group_id": "100000", "message": "3"},
    ]

    result = await execute_tool(
        context,
        ToolCall(name="qq_send_group_message", arguments={"groupId": "100000", "message": "第四条"}),
    )

    assert result.success is False
    assert "最多发送" in result.message
    assert len(context.outbox) == 3


async def test_qq_send_private_message_queues_current_private_session(stack: TestStack) -> None:
    stack.whitelist.owner_qq = "100001"
    session = stack.sessions.ensure_session("private", "100001")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )

    result = await execute_tool(context, _private_send_call())

    assert result.success is True
    assert context.outbox == [
        {
            "type": "send_private",
            "user_id": "100001",
            "group_id": None,
            "message": "私聊回复",
        }
    ]


async def test_qq_send_private_message_rejects_other_private_target(stack: TestStack) -> None:
    stack.whitelist.owner_qq = "100001"
    session = stack.sessions.ensure_session("private", "100001")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )

    result = await execute_tool(context, _private_send_call(user_id="100002"))

    assert result.success is False
    assert context.outbox == []


async def test_qq_send_private_message_from_group_is_owner_only(stack: TestStack) -> None:
    stack.whitelist.owner_qq = "100001"
    session = stack.sessions.ensure_session("group", "100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )

    allowed = await execute_tool(context, _private_send_call(user_id="100001", message="owner 通知"))
    denied = await execute_tool(context, _private_send_call(user_id="100002", message="陌生私聊"))

    assert allowed.success is True
    assert denied.success is False
    assert context.outbox == [
        {
            "type": "send_private",
            "user_id": "100001",
            "group_id": None,
            "message": "owner 通知",
        }
    ]


async def test_sticker_send_queues_approved_sticker(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
        sticker_library=stack.sticker_library,
    )

    result = await execute_tool(
        context,
        ToolCall(name="sticker_send", arguments={"stickerId": "mock-01"}),
    )

    assert result.success is True
    assert context.outbox == [
        {
            "type": "send_sticker",
            "sticker_id": "mock-01",
            "group_id": "100000",
            "user_id": None,
        }
    ]


async def test_sticker_send_rejects_unknown_sticker(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
        sticker_library=stack.sticker_library,
    )

    result = await execute_tool(
        context,
        ToolCall(name="sticker_send", arguments={"stickerId": "not-here"}),
    )

    assert result.success is False
    assert context.outbox == []


async def test_sticker_send_fails_when_library_missing(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
        sticker_library=None,
    )

    result = await execute_tool(
        context,
        ToolCall(name="sticker_send", arguments={"stickerId": "mock-01"}),
    )

    assert result.success is False
    assert "表情库未配置" in result.message


async def test_qq_dive_is_rejected_for_mention_before_reply(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
        wake_category=TriggerCategory.MENTION,
        mention_dive_allowed=False,
    )

    result = await execute_tool(context, _dive_call())

    assert result.success is False
    assert "必须先回复" in result.message
    assert "直接问候" in result.message
    assert result.command is None


async def test_qq_dive_is_allowed_for_scheduled_wake(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
        wake_category=TriggerCategory.SCHEDULED,
    )

    result = await execute_tool(context, _dive_call())

    assert result.success is True
    assert result.command == "dive"


async def test_qq_dive_is_allowed_for_mention_when_probability_hits(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
        wake_category=TriggerCategory.MENTION,
        mention_dive_allowed=True,
    )

    result = await execute_tool(context, _dive_call())

    assert result.success is True
    assert result.command == "dive"

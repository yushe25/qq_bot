from __future__ import annotations

import pytest

from app.agent.tools import ToolContext
from app.domain.types import Session
from app.platform.gateway import NullGateway
from app.safety.content_filter import ContentFilter
from app.safety.rate_limiter import RateLimiter
from tests.conftest import TestStack


def test_content_filter_blocks_sensitive_text() -> None:
    filter = ContentFilter()
    ok, _ = filter.check("正常聊天内容")
    assert ok
    blocked, _ = filter.check("api_key = 123456")
    assert not blocked
    blocked, _ = filter.check("[CQ:image,file=1.jpg]")
    assert not blocked
    blocked, _ = filter.check("13800138000")
    assert not blocked


def test_rate_limiter_enforces_window() -> None:
    limiter = RateLimiter(max_per_minute=2, max_per_hour=100)
    assert limiter.allow("s1")
    assert limiter.allow("s1")
    assert not limiter.allow("s1")


def test_rate_limiter_reset_clears_minute_and_hour_windows() -> None:
    limiter = RateLimiter(max_per_minute=100, max_per_hour=2)
    assert limiter.allow("s1")
    assert limiter.allow("s2")
    assert not limiter.allow("s3")

    limiter.reset()

    assert limiter.allow("s4")


def test_rate_limiter_next_available_at_returns_window_end() -> None:
    limiter = RateLimiter(max_per_minute=1, max_per_hour=10)
    assert limiter.allow("s1", now=0.0)
    assert not limiter.allow("s1", now=1.0)

    assert limiter.next_available_at("s1", now=5.0) == 60.0
    assert limiter.next_available_at(None, now=5.0) == 60.0

    assert limiter.allow("s1", now=61.0)
    assert not limiter.allow("s1", now=59.0)


def test_rate_limiter_has_capacity_checks_without_consuming() -> None:
    limiter = RateLimiter(max_per_minute=2, max_per_hour=1)
    assert limiter.has_capacity("s1")
    assert limiter.allow("s1")
    assert not limiter.has_capacity("s1")
    assert not limiter.allow("s1")

    limiter2 = RateLimiter(max_per_minute=10, max_per_hour=1)
    # has_capacity only observes; allow() is the operation that consumes quota.
    assert limiter2.has_capacity("s1", now=0.0)
    assert limiter2.allow("s1", now=0.0)
    assert not limiter2.has_capacity("s1", now=0.0)
    assert not limiter2.allow("s1", now=0.0)


def test_rate_limiter_enforces_separate_session_hour_bucket() -> None:
    limiter = RateLimiter(
        max_per_minute=10,
        max_per_hour=10,
        max_per_session_per_hour=2,
    )

    assert limiter.allow("s1", now=0.0)
    assert limiter.allow("s1", now=1.0)
    assert not limiter.allow("s1", now=2.0)
    assert limiter.allow("s2", now=2.0)

    assert limiter.next_available_at("s1", now=10.0) == 3600.0


def test_rate_limiter_reset_clears_session_hour_bucket() -> None:
    limiter = RateLimiter(
        max_per_minute=10,
        max_per_hour=10,
        max_per_session_per_hour=1,
    )

    assert limiter.allow("s1", now=0.0)
    assert not limiter.allow("s1", now=1.0)

    limiter.reset()

    assert limiter.allow("s1", now=2.0)


async def test_safety_pipeline_sends_allowed_message(stack: TestStack) -> None:
    session = Session(id="group:100000", scene_type="group", scene_id="100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    context.outbox.append({"type": "send_group", "group_id": "100000", "message": "在的"})

    results = await stack.safety.execute_outbox(context, stack.gateway)

    assert results[0]["ok"] is True
    assert len(stack.gateway.sent) == 1


async def test_safety_pipeline_sends_mentions_only_on_first_segment(stack: TestStack) -> None:
    stack.config.safety.max_group_members = 0
    stack.gateway.members = [
        {"user_id": 10001, "nickname": "甲"},
        {"user_id": 10002, "nickname": "乙"},
    ]
    session = Session(id="group:100000", scene_type="group", scene_id="100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    context.outbox.append(
        {
            "type": "send_group",
            "group_id": "100000",
            "message": "在吗，帮我看看",
            "mention_user_ids": ["10001"],
        }
    )

    results = await stack.safety.execute_outbox(context, stack.gateway)

    assert [item["ok"] for item in results] == [True, True]
    assert [item["text"] for item in stack.gateway.sent] == ["在吗", "帮我看看"]
    assert stack.gateway.sent[0]["mention_user_ids"] == ["10001"]
    assert stack.gateway.sent[1]["mention_user_ids"] == []
    audits = await stack.store.list_audit(limit=20)
    assert any(item["detail"] == "群 100000 消息 mentions=1" for item in audits)


async def test_safety_pipeline_rejects_mention_outside_current_group(stack: TestStack) -> None:
    stack.gateway.members = [{"user_id": 10001, "nickname": "甲"}]
    session = Session(id="group:100000", scene_type="group", scene_id="100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    context.outbox.append(
        {
            "type": "send_group",
            "group_id": "100000",
            "message": "叫一下",
            "mention_user_ids": ["10002"],
        }
    )

    results = await stack.safety.execute_outbox(context, stack.gateway)

    assert results[0]["ok"] is False
    assert "不在当前群成员列表" in results[0]["reason"]
    assert stack.gateway.sent == []


async def test_safety_pipeline_fails_closed_when_mention_member_query_errors(stack: TestStack) -> None:
    class BrokenMembersGateway(NullGateway):
        async def get_group_members(self, group_id: str) -> list[dict[str, object]]:
            raise RuntimeError("member api down")

    gateway = BrokenMembersGateway()
    session = Session(id="group:100000", scene_type="group", scene_id="100000")
    context = ToolContext(
        session=session,
        gateway=gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    context.outbox.append(
        {
            "type": "send_group",
            "group_id": "100000",
            "message": "叫一下",
            "mention_user_ids": ["10001"],
        }
    )

    results = await stack.safety.execute_outbox(context, gateway)

    assert results[0]["ok"] is False
    assert "成员数校验失败" in results[0]["reason"]
    assert gateway.sent == []


@pytest.mark.parametrize(
    "mention_user_ids",
    [
        ["10001", "10001"],
        [""],
        ["abc"],
        [1],
        [str(10000 + index) for index in range(6)],
    ],
)
async def test_safety_pipeline_rejects_malformed_mention_arrays(
    stack: TestStack,
    mention_user_ids: list[object],
) -> None:
    session = Session(id="group:100000", scene_type="group", scene_id="100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    context.outbox.append(
        {
            "type": "send_group",
            "group_id": "100000",
            "message": "测试",
            "mention_user_ids": mention_user_ids,
        }
    )

    results = await stack.safety.execute_outbox(context, stack.gateway)

    assert results[0]["ok"] is False
    assert stack.gateway.sent == []


async def test_safety_pipeline_sends_allowed_owner_private_message(stack: TestStack) -> None:
    stack.whitelist.owner_qq = "100001"
    session = Session(id="private:100001", scene_type="private", scene_id="100001")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    context.outbox.append({"type": "send_private", "user_id": "100001", "message": "收到"})

    results = await stack.safety.execute_outbox(context, stack.gateway)

    assert results == [{"ok": True, "action": "send_private", "user_id": "100001"}]
    assert len(stack.gateway.sent) == 1
    sent = stack.gateway.sent[0]
    assert sent["user_id"] == "100001"
    assert sent["text"] == "收到"
    audits = await stack.store.list_audit(limit=20)
    assert any(item["action"] == "send_private_message" and item["result"] == "allowed" for item in audits)


async def test_safety_pipeline_blocks_private_message_outside_whitelist(stack: TestStack) -> None:
    session = Session(id="private:100002", scene_type="private", scene_id="100002")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    context.outbox.append({"type": "send_private", "user_id": "100002", "message": "你好"})

    results = await stack.safety.execute_outbox(context, stack.gateway)

    assert results[0]["ok"] is False
    assert results[0]["reason"] == "会话不在白名单中"
    assert stack.gateway.sent == []
    audits = await stack.store.list_audit(limit=20)
    assert any(
        item["action"] == "send_private_message" and "blocked: 会话不在白名单中" in item["result"]
        for item in audits
    )


async def test_safety_pipeline_blocks_sensitive_private_message(stack: TestStack) -> None:
    stack.whitelist.owner_qq = "100001"
    session = Session(id="private:100001", scene_type="private", scene_id="100001")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    context.outbox.append({"type": "send_private", "user_id": "100001", "message": "api_key = 123456"})

    results = await stack.safety.execute_outbox(context, stack.gateway)

    assert results[0]["ok"] is False
    assert "敏感" in str(results[0]["reason"])
    assert stack.gateway.sent == []


async def test_safety_pipeline_rate_limits_private_messages(stack: TestStack) -> None:
    stack.whitelist.owner_qq = "100001"
    stack.safety.send_limiter = RateLimiter(max_per_minute=1, max_per_hour=100)
    session = Session(id="private:100001", scene_type="private", scene_id="100001")

    def context() -> ToolContext:
        ctx = ToolContext(
            session=session,
            gateway=stack.gateway,
            config=stack.config,
            memory=stack.memory,
            whitelist=stack.whitelist,
        )
        ctx.outbox.append({"type": "send_private", "user_id": "100001", "message": "收到"})
        return ctx

    first = await stack.safety.execute_outbox(context(), stack.gateway)
    second = await stack.safety.execute_outbox(context(), stack.gateway)

    assert first[0]["ok"] is True
    assert second[0]["ok"] is False
    assert second[0]["reason"] == "发送频率超限"
    assert len(stack.gateway.sent) == 1


async def test_safety_pipeline_splits_comma_messages(stack: TestStack) -> None:
    session = Session(id="group:100000", scene_type="group", scene_id="100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    context.outbox.append({"type": "send_group", "group_id": "100000", "message": "在吗，帮我看看"})

    results = await stack.safety.execute_outbox(context, stack.gateway)

    assert [item.get("ok") for item in results] == [True, True]
    assert [item["text"] for item in stack.gateway.sent] == ["在吗", "帮我看看"]


async def test_safety_pipeline_preserves_literal_at(stack: TestStack) -> None:
    session = Session(id="group:100000", scene_type="group", scene_id="100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    context.outbox.append({"type": "send_group", "group_id": "100000", "message": "咋了老@我 有啥事"})

    results = await stack.safety.execute_outbox(context, stack.gateway)

    assert results[0]["ok"] is True
    assert stack.gateway.sent[0]["text"] == "咋了老@我 有啥事"


async def test_safety_pipeline_blocks_unknown_scene_and_sensitive_text(stack: TestStack) -> None:
    session = Session(id="group:999999", scene_type="group", scene_id="999999")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    context.outbox.append({"type": "send_group", "group_id": "999999", "message": "你好"})

    results = await stack.safety.execute_outbox(context, stack.gateway)
    assert results[0]["ok"] is False
    assert results[0]["reason"] == "会话不在白名单中"

    session_allowed = Session(id="group:100000", scene_type="group", scene_id="100000")
    context2 = ToolContext(
        session=session_allowed,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    context2.outbox.append({"type": "send_group", "group_id": "100000", "message": "[CQ:image,file=x.jpg]"})

    results2 = await stack.safety.execute_outbox(context2, stack.gateway)
    assert results2[0]["ok"] is False
    assert "敏感" in results2[0]["reason"]


async def test_execute_outbox_caps_messages_per_wake_and_does_not_consume_limiter(
    stack: TestStack,
) -> None:
    stack.safety.send_limiter = RateLimiter(max_per_minute=4, max_per_hour=100)
    session = Session(id="group:100000", scene_type="group", scene_id="100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    for index in range(5):
        context.outbox.append({"type": "send_group", "group_id": "100000", "message": f"消息 {index}"})

    results = await stack.safety.execute_outbox(context, stack.gateway)

    assert [item.get("ok") for item in results] == [True, True, True, False, False]
    assert len(stack.gateway.sent) == 3
    assert "最多发送 3" in str(results[3]["reason"])
    assert stack.safety.send_limiter.allow("group:100000")


async def test_safety_pipeline_blocks_group_over_member_limit(stack: TestStack) -> None:
    stack.config.safety.max_group_members = 3
    stack.gateway.members = [
        {"user_id": 10001, "nickname": "甲"},
        {"user_id": 10002, "nickname": "乙"},
        {"user_id": 10003, "nickname": "丙"},
        {"user_id": 10004, "nickname": "丁"},
    ]
    session = Session(id="group:100000", scene_type="group", scene_id="100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    context.outbox.append({"type": "send_group", "group_id": "100000", "message": "在的"})

    results = await stack.safety.execute_outbox(context, stack.gateway)

    assert results[0]["ok"] is False
    assert "成员数超过上限" in results[0]["reason"]
    assert len(stack.gateway.sent) == 0


async def test_safety_pipeline_fails_closed_when_member_query_errors(stack: TestStack) -> None:
    class BrokenMembersGateway(NullGateway):
        async def get_group_members(self, group_id: str) -> list[dict[str, object]]:
            raise RuntimeError("member api down")

    gateway = BrokenMembersGateway()
    session = Session(id="group:100000", scene_type="group", scene_id="100000")
    context = ToolContext(
        session=session,
        gateway=gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
    )
    context.outbox.append({"type": "send_group", "group_id": "100000", "message": "在的"})

    results = await stack.safety.execute_outbox(context, gateway)

    assert results[0]["ok"] is False
    assert "成员数校验失败" in results[0]["reason"]
    assert gateway.sent == []


async def test_safety_pipeline_sends_approved_sticker_and_counts_usage(stack: TestStack) -> None:
    session = Session(id="group:100000", scene_type="group", scene_id="100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
        sticker_library=stack.sticker_library,
    )
    context.outbox.append({"type": "send_sticker", "sticker_id": "mock-01", "group_id": "100000"})

    results = await stack.safety.execute_outbox(context, stack.gateway)

    assert results[0]["ok"] is True
    assert len(stack.gateway.sent) == 1
    assert "sticker" in str(stack.gateway.sent[0])
    assert await stack.sticker_library.usage_count("mock-01") == 1


async def test_safety_pipeline_blocks_unknown_sticker(stack: TestStack) -> None:
    session = Session(id="group:100000", scene_type="group", scene_id="100000")
    context = ToolContext(
        session=session,
        gateway=stack.gateway,
        config=stack.config,
        memory=stack.memory,
        whitelist=stack.whitelist,
        sticker_library=stack.sticker_library,
    )
    context.outbox.append({"type": "send_sticker", "sticker_id": "missing", "group_id": "100000"})

    results = await stack.safety.execute_outbox(context, stack.gateway)

    assert results[0]["ok"] is False
    assert "表情" in results[0]["reason"]
    assert stack.gateway.sent == []


async def test_daily_send_cap_breaks_circuit_until_manual_reset(stack: TestStack) -> None:
    stack.config.safety.max_sends_per_day = 1
    session = Session(id="group:100000", scene_type="group", scene_id="100000")

    def context() -> ToolContext:
        ctx = ToolContext(
            session=session,
            gateway=stack.gateway,
            config=stack.config,
            memory=stack.memory,
            whitelist=stack.whitelist,
        )
        ctx.outbox.append({"type": "send_group", "group_id": "100000", "message": "在的"})
        return ctx

    results = await stack.safety.execute_outbox(context(), stack.gateway)
    assert results[0]["ok"] is True
    assert len(stack.gateway.sent) == 1

    results = await stack.safety.execute_outbox(context(), stack.gateway)
    assert results[0]["ok"] is False
    assert "每日发送" in results[0]["reason"]
    assert len(stack.gateway.sent) == 1

    await stack.safety.reset_daily_sends(session.id)
    results = await stack.safety.execute_outbox(context(), stack.gateway)
    assert results[0]["ok"] is True
    assert len(stack.gateway.sent) == 2

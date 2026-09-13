from __future__ import annotations

from app.agent.llm_provider import ChatRequest, ChatResult
from app.agent.providers.mock import MockProvider
from app.domain.types import (
    MessageSegment,
    NormalizedEvent,
    SessionState,
    ToolCall,
    ToolResult,
    TriggerCategory,
    WakeInput,
)
from app.runtime.orchestrator import _tool_result_text
from app.runtime.schedule import DelayRange, ScheduleProfile
from tests.conftest import TestStack


def _mention_event(session_id: str) -> NormalizedEvent:
    return NormalizedEvent(
        event_id="m1",
        session_id=session_id,
        timestamp=1767171600000,
        sender_id="10001",
        sender_name="阿泽",
        kind="group",
        segments=(
            MessageSegment(kind="at", data={"qq": "31415926"}),
            MessageSegment(kind="text", data={"text": " 在吗"}),
        ),
        text="在吗",
        raw={},
        bot_id="31415926",
    )


class _FailingProvider:
    async def complete(self, req: ChatRequest) -> ChatResult:
        raise RuntimeError("upstream down")


class _ScriptedProvider:
    def __init__(self, results: list[ChatResult]) -> None:
        self.results = results
        self.requests: list[ChatRequest] = []

    async def complete(self, req: ChatRequest) -> ChatResult:
        self.requests.append(req)
        return self.results.pop(0)


class _DiveBatchProvider(MockProvider):
    async def complete(self, req: ChatRequest) -> ChatResult:
        return ChatResult(
            kind="tool_calls",
            calls=[
                ToolCall(
                    name="qq_dive",
                    arguments={"nextWakeMs": 1000, "reason": "先潜水"},
                    id="dive-1",
                ),
                ToolCall(
                    name="memory_update",
                    arguments={"kind": "active_topic", "key": "topic", "value": "开黑"},
                    id="later-1",
                ),
            ],
        )


class _DiveThenReplyProvider(MockProvider):
    def __init__(self, config, session_id: str) -> None:
        super().__init__(config)
        self.session_id = session_id
        self.calls = 0

    async def complete(self, req: ChatRequest) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            return ChatResult(
                kind="tool_calls",
                calls=[
                    ToolCall(
                        name="qq_dive",
                        arguments={"nextWakeMs": 60_000, "reason": "先潜水"},
                        id="dive-first",
                    )
                ],
            )
        if self.calls == 2:
            return ChatResult(
                kind="tool_calls",
                calls=[
                    ToolCall(
                        name="qq_send_group_message",
                        arguments={"groupId": self.session_id.removeprefix("group:"), "message": "在的"},
                        id="send-after-rejection",
                    )
                ],
            )
        return ChatResult(kind="text", content="")


async def test_wake_sends_message_and_enters_cooldown(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.MENTION,
        reason="消息中包含 @",
        events=(_mention_event(session.id),),
    )

    outcome = await stack.orchestrator.run_wake(session, wake)

    assert outcome.sent == 2
    assert outcome.error is None
    assert len(stack.gateway.sent) == 2
    assert session.wake_count == 1
    assert session.state == SessionState.COOLDOWN
    assert session.cooldown_until > 0
    assert session.conversation[-1]["role"] == "assistant"


async def test_mock_wake_writes_memory_and_injects_it_on_next_wake(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    await stack.orchestrator.run_wake(
        session,
        WakeInput(
            session_id=session.id,
            category=TriggerCategory.MENTION,
            reason="消息中包含 @",
            events=(_mention_event(session.id),),
        ),
    )

    memories = await stack.store.load_memory(session.id)
    assert [(item["kind"], item["key"], item["value"]) for item in memories] == [
        ("active_topic", "当前话题", "群里刚聊到开黑")
    ]
    assert "群里刚聊到开黑" in await stack.memory.snapshot(session.id)

    await stack.orchestrator.run_wake(
        session,
        WakeInput(
            session_id=session.id,
            category=TriggerCategory.SCHEDULED,
            reason="定时唤醒到期",
        ),
    )
    latest_invocation = next(
        str(message["content"])
        for message in reversed(session.conversation)
        if message.get("role") == "user"
    )
    assert "群里刚聊到开黑" in latest_invocation


async def test_dive_batch_appends_cancelled_results_for_unexecuted_calls(stack: TestStack) -> None:
    stack.orchestrator.provider = _DiveBatchProvider(stack.config.llm)
    session = stack.sessions.ensure_session("group", "100000")
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.SCHEDULED,
        reason="定时唤醒到期",
    )

    outcome = await stack.orchestrator.run_wake(session, wake)

    assert outcome.dived is True
    cancelled = [item for item in session.conversation if item.get("role") == "tool" and "已取消" in str(item.get("content"))]
    assert len(cancelled) == 1
    assert cancelled[0]["tool_call_id"] == "later-1"


async def test_mention_dive_rejection_forces_reply(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    stack.config.scheduler.mention_dive_probability = 0.0
    stack.orchestrator.provider = _DiveThenReplyProvider(stack.config.llm, session.id)
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.MENTION,
        reason="消息中包含 @",
        events=(_mention_event(session.id),),
    )

    outcome = await stack.orchestrator.run_wake(session, wake)

    assert outcome.sent == 1
    assert outcome.dived is False
    assert any(item.get("text") == "在的" for item in stack.gateway.sent)


async def test_mention_can_dive_when_small_probability_hits(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    stack.config.scheduler.mention_dive_probability = 1.0
    stack.orchestrator.provider = _ScriptedProvider(
        [
            ChatResult(
                kind="tool_calls",
                calls=[
                    ToolCall(
                        name="qq_dive",
                        arguments={"nextWakeMs": 60_000, "reason": "小概率错过"},
                        id="mention-dive",
                    )
                ],
            )
        ]
    )
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.MENTION,
        reason="消息中包含 @",
        events=(_mention_event(session.id),),
    )

    outcome = await stack.orchestrator.run_wake(session, wake)

    assert outcome.sent == 0
    assert outcome.dived is True


async def test_llm_failure_is_audited(stack: TestStack) -> None:
    stack.orchestrator.provider = _FailingProvider()
    session = stack.sessions.ensure_session("group", "100000")
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.SCHEDULED,
        reason="定时唤醒到期",
    )

    outcome = await stack.orchestrator.run_wake(session, wake)

    assert outcome.error == "upstream down"
    audits = await stack.store.list_audit(limit=50)
    assert any(item["action"] == "llm_complete_failed" and "upstream down" in item["result"] for item in audits)


async def test_wake_without_action_dives(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.SCHEDULED,
        reason="定时唤醒到期",
    )

    outcome = await stack.orchestrator.run_wake(session, wake)

    assert outcome.dived is True
    assert outcome.sent == 0
    assert session.state == SessionState.DIVING
    assert session.next_wake_at > 0
    assert stack.gateway.sent == []


async def test_final_model_step_omits_tools_and_aggregates_usage(stack: TestStack) -> None:
    stack.config.scheduler.max_tool_steps = 2
    provider = _ScriptedProvider(
        [
            ChatResult(
                kind="tool_calls",
                calls=[
                    ToolCall(
                        name="qq_wait",
                        arguments={"durationMs": 1},
                        id="wait-1",
                    )
                ],
                prompt_tokens=11,
                completion_tokens=2,
            ),
            ChatResult(
                kind="text",
                content="收到",
                prompt_tokens=19,
                completion_tokens=3,
            ),
        ]
    )
    stack.orchestrator.provider = provider
    session = stack.sessions.ensure_session("group", "100000")

    outcome = await stack.orchestrator.run_wake(
        session,
        WakeInput(
            session_id=session.id,
            category=TriggerCategory.FORCE,
            reason="测试最终步骤",
        ),
    )

    assert len(provider.requests) == 2
    assert provider.requests[0].tools
    assert provider.requests[1].tools == []
    assert "最后一次模型调用" in provider.requests[1].system
    assert outcome.prompt_tokens == 30
    assert outcome.completion_tokens == 5
    assert outcome.total_tokens == 35


async def test_repeated_tool_results_are_compressed(stack: TestStack) -> None:
    stack.config.scheduler.max_tool_steps = 2
    provider = _ScriptedProvider(
        [
            ChatResult(
                kind="tool_calls",
                calls=[
                    ToolCall(name="qq_get_members", arguments={}, id="members-1"),
                    ToolCall(name="qq_get_members", arguments={}, id="members-2"),
                ],
            ),
            ChatResult(kind="text", content="收到"),
        ]
    )
    stack.orchestrator.provider = provider
    session = stack.sessions.ensure_session("group", "100000")

    await stack.orchestrator.run_wake(
        session,
        WakeInput(
            session_id=session.id,
            category=TriggerCategory.FORCE,
            reason="测试重复工具结果",
        ),
    )

    tool_messages = [item for item in session.conversation if item.get("role") == "tool"]
    assert len(tool_messages) == 2
    assert "读取成功" in str(tool_messages[0]["content"])
    assert "重复工具结果" in str(tool_messages[1]["content"])
    assert tool_messages[1]["tool_call_id"] == "members-2"


async def test_session_history_is_saved_within_budget(stack: TestStack) -> None:
    stack.orchestrator.provider = _ScriptedProvider(
        [ChatResult(kind="text", content="收到")]
    )
    session = stack.sessions.ensure_session("group", "100000")
    session.conversation = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"历史-{index}"}
        for index in range(60)
    ]

    await stack.orchestrator.run_wake(
        session,
        WakeInput(
            session_id=session.id,
            category=TriggerCategory.FORCE,
            reason="测试历史裁剪",
        ),
    )

    assert len(session.conversation) <= stack.config.token_budget.history_messages
    assert session.conversation[-1]["role"] == "assistant"


async def test_session_manager_persists_state(stack: TestStack) -> None:
    session = stack.sessions.ensure_session("group", "100000")
    await stack.sessions.set_state(session, SessionState.PAUSED)

    loaded = next(item for item in await stack.store.load_sessions() if item.id == session.id)
    assert loaded.state == SessionState.PAUSED


def test_tool_result_text_formats_group_history_and_members() -> None:
    history = _tool_result_text(
        "qq_get_group_history",
        ToolResult(
            success=True,
            message="读取成功",
            data={
                "messages": [
                    {
                        "message_id": 11,
                        "sender": {"user_id": 10001, "nickname": "阿泽"},
                        "raw_message": "在吗",
                    },
                    {
                        "message_id": 12,
                        "sender": {"user_id": 10002, "card": "小皮", "nickname": "旧名"},
                        "message": [{"type": "face", "data": {"id": 1}}],
                    },
                ]
            },
        ),
    )
    assert "读取成功" in history
    assert "[11] 阿泽(10001): 在吗" in history
    assert "[12] 小皮(10002):" in history

    members = _tool_result_text(
        "qq_get_members",
        ToolResult(
            success=True,
            message="读取成功",
            data={
                "members": [
                    {"user_id": 10001, "nickname": "阿泽", "role": "owner"},
                    {"user_id": 10002},
                ]
            },
        ),
    )
    assert "阿泽(10001) [owner]" in members
    assert "10002(10002)" in members


def test_tool_result_text_keeps_generic_data_bounded() -> None:
    text = _tool_result_text(
        "memory_read",
        ToolResult(
            success=True,
            message="ok",
            data={"blob": "x" * 5000},
        ),
        max_chars=100,
    )
    assert text.startswith("ok ")
    assert len(text) <= 100


async def test_wake_attaches_sticker_with_text_when_profile_allows(stack: TestStack, monkeypatch) -> None:
    stack.orchestrator.schedule_profile = ScheduleProfile(
        timezone="Asia/Shanghai",
        active_windows=((0, 24),),
        reply_delay=DelayRange(0, 0),
        mention_delay=DelayRange(0, 0),
        sticker_only_probability=0,
        sticker_with_text_probability=1,
    )
    monkeypatch.setattr("app.runtime.orchestrator.random.random", lambda: 0.5)
    monkeypatch.setattr("app.runtime.orchestrator.random.choice", lambda values: values[0])
    session = stack.sessions.ensure_session("group", "100000")
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.MENTION,
        reason="消息中包含 @",
        events=(_mention_event(session.id),),
    )

    outcome = await stack.orchestrator.run_wake(session, wake)

    assert outcome.sent == 3
    assert len(stack.gateway.sent) == 3
    assert any("sticker" in str(item) for item in stack.gateway.sent)
    assert any("text" in item for item in stack.gateway.sent)


async def test_wake_sends_sticker_only_when_no_text_and_profile_allows(
    stack: TestStack,
    monkeypatch,
) -> None:
    stack.orchestrator.schedule_profile = ScheduleProfile(
        timezone="Asia/Shanghai",
        active_windows=((0, 24),),
        reply_delay=DelayRange(0, 0),
        mention_delay=DelayRange(0, 0),
        sticker_only_probability=1,
        sticker_with_text_probability=0,
    )
    monkeypatch.setattr("app.runtime.orchestrator.random.random", lambda: 0.5)
    monkeypatch.setattr("app.runtime.orchestrator.random.choice", lambda values: values[0])
    session = stack.sessions.ensure_session("group", "100000")
    wake = WakeInput(
        session_id=session.id,
        category=TriggerCategory.SCHEDULED,
        reason="定时唤醒到期",
    )

    outcome = await stack.orchestrator.run_wake(session, wake)

    assert outcome.sent == 1
    assert len(stack.gateway.sent) == 1
    assert "sticker" in str(stack.gateway.sent[0])
    assert session.state == SessionState.COOLDOWN

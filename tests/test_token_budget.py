from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from app.agent.tool_schemas import build_tool_schemas
from app.agent.tools import GetHistoryArgs
from app.config import AppConfig
from app.storage.db import StateStore


def test_token_budget_defaults_are_conservative() -> None:
    config = AppConfig()

    assert config.llm.max_tokens <= 200
    assert config.scheduler.batch_window_ms >= 8000
    assert config.scheduler.wake_probability <= 0.04
    assert config.scheduler.cooldown_seconds >= 90
    assert 10 <= config.token_budget.wake_messages <= 12
    assert 24 <= config.token_budget.history_messages <= 30
    assert config.token_budget.event_text_chars <= 160
    assert config.token_budget.tool_result_chars <= 1600
    assert config.safety.max_message_chars <= 200


def test_tool_schemas_can_be_omitted_for_final_reply_and_keep_order() -> None:
    first = build_tool_schemas()
    second = build_tool_schemas()

    assert [item["function"]["name"] for item in first] == [item["function"]["name"] for item in second]
    assert build_tool_schemas(allow_tools=False) == []


def test_group_history_defaults_to_ten() -> None:
    assert GetHistoryArgs().limit == 10
    history_schema = next(
        item for item in build_tool_schemas() if item["function"]["name"] == "qq_get_group_history"
    )
    assert history_schema["function"]["parameters"]["properties"]["limit"]["default"] == 10


def test_token_budget_rejects_non_positive_limits() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"token_budget": {"wake_messages": 0}})
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"token_budget": {"history_messages": 0}})


async def test_existing_wake_log_is_migrated_for_token_usage(tmp_path) -> None:
    database_path = tmp_path / "old-qqf.db"
    conn = sqlite3.connect(database_path)
    conn.execute(
        """
        CREATE TABLE wake_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            category TEXT NOT NULL,
            reason TEXT NOT NULL,
            decided TEXT NOT NULL,
            probability REAL,
            next_wake_at REAL,
            created_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    store = StateStore(database_path)
    await store.init()
    try:
        await store.append_wake_log("group:100000", "force", "迁移", "wake")
        await store.update_latest_wake_log_usage("group:100000", 41, 5, 46)
        logs = await store.list_wake_logs("group:100000")
    finally:
        await store.close()

    assert logs[0]["prompt_tokens"] == 41
    assert logs[0]["completion_tokens"] == 5
    assert logs[0]["total_tokens"] == 46

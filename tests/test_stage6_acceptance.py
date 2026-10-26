from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from app.agent.llm_provider import ChatRequest
from scripts.mock_qq_server import MockOneBot
from scripts.stage6_acceptance import (
    Stage6Provider,
    _database_metrics,
    _evaluate_acceptance,
    _snapshot_database,
    _table_summary,
    assert_sticker_ready,
)


def _request(content: str, *, role: str = "user") -> ChatRequest:
    return ChatRequest(
        system="当前会话：group 100000",
        messages=[{"role": role, "content": content}],
        tools=[],
    )


async def test_stage6_provider_exercises_required_tool_paths() -> None:
    provider = Stage6Provider(object())

    memory = await provider.complete(_request("S6_MEMORY 在吗"))
    sticker = await provider.complete(_request("S6_STICKER 发个表情"))
    private = await provider.complete(_request("S6_PRIVATE 在吗"))
    dive = await provider.complete(_request("S6_DIVE 先潜水"))

    assert [call.name for call in memory.calls] == ["memory_update", "qq_send_group_message"]
    assert [call.name for call in sticker.calls] == ["sticker_send"]
    assert [call.name for call in private.calls] == ["qq_send_private_message"]
    assert [call.name for call in dive.calls] == ["qq_dive"]


async def test_stage6_provider_uses_two_tool_rounds_before_final_text() -> None:
    provider = Stage6Provider(object())
    request = _request("S6_MULTI 看一下成员和历史")

    first = await provider.complete(request)
    second = await provider.complete(
        ChatRequest(
            system=request.system,
            messages=[
                {"role": "user", "content": "S6_MULTI 看一下成员和历史"},
                {"role": "assistant", "content": None},
                {"role": "tool", "tool_call_id": "stage6-members", "content": "成员"},
            ],
            tools=[],
        )
    )
    final = await provider.complete(
        ChatRequest(
            system=request.system,
            messages=[
                {"role": "user", "content": "S6_MULTI 看一下成员和历史"},
                {"role": "assistant", "content": None},
                {"role": "tool", "tool_call_id": "stage6-members", "content": "成员"},
                {"role": "assistant", "content": None},
                {"role": "tool", "tool_call_id": "stage6-history", "content": "历史"},
            ],
            tools=[],
        )
    )

    assert first.calls[0].name == "qq_get_members"
    assert second.calls[0].name == "qq_get_group_history"
    assert final.kind == "text"
    assert final.content == "多轮工具完成"


def test_stage6_mock_event_matrix_is_deterministic() -> None:
    mock = MockOneBot("100000", "31415926", stage6=True, seed=7, scenario_interval=1.0)

    first = mock.stage6_payloads()
    second = mock.stage6_payloads()

    assert [item[0] for item in first] == [
        "M01_NORMAL",
        "M02_MENTION",
        "M03_POKE",
        "M06_STICKER",
        "M09_MULTI_TOOL",
        "M05_PRIVATE",
        "M04_DIVE",
        "M07_LIMIT_1",
        "M07_LIMIT_2",
        "M08_DEFER",
    ]
    assert [item[1]["message_type"] for item in first if "message_type" in item[1]] == [
        "group",
        "group",
        "group",
        "group",
        "private",
        "group",
        "group",
        "group",
        "group",
    ]
    assert [item[1]["message_id"] for item in first] == [item[1]["message_id"] for item in second]
    # 重放必须可被去重：拍一拍的事件指纹包含 time，两次生成必须完全一致，
    # 否则重启后重放会当成新事件再唤醒一次（S6-R07）。
    assert first[2][0] == "M03_POKE"
    assert first[2][1]["time"] == second[2][1]["time"]


def test_database_snapshot_and_table_compare_detect_changes(tmp_path) -> None:
    database = tmp_path / "stage6.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, state TEXT)")
    connection.execute("INSERT INTO sessions VALUES ('group:100000', 'idle')")
    connection.commit()
    connection.close()

    before = _snapshot_database(database)
    connection = sqlite3.connect(database)
    connection.execute("UPDATE sessions SET state = 'active'")
    connection.commit()
    connection.close()
    after = _snapshot_database(database)
    comparison = _table_summary(before, after)

    assert before["integrity_check"] == "ok"
    assert json.dumps(before["tables"]["sessions"], sort_keys=True) != json.dumps(
        after["tables"]["sessions"],
        sort_keys=True,
    )
    assert comparison["sessions"]["equal"] is False


def test_database_metrics_reads_audit_logs_table() -> None:
    metrics = _database_metrics(
        {
            "tables": {
                "wake_log": [{"decided": "wake"}, {"decided": "deferred"}],
                "audit_logs": [
                    {"result": "ok"},
                    {"result": "failed: blocked"},
                ],
            }
        }
    )

    assert metrics == {
        "wake_log_count": 2,
        "decisions": {"wake": 1, "deferred": 1},
        "categories": {},
        "audit_count": 2,
        "failed_or_denied_audit_count": 1,
        "tool_names": {},
        "orphan_tool_ids": [],
        "session_states": {},
        "daily_send_total": 0,
        "sticker_usage_total": 0,
        "memory_count": 0,
        "pending_wake_count": 0,
        "event_dedupe_count": 0,
    }


def test_acceptance_evaluation_returns_failed_checks() -> None:
    summary = _passing_summary()

    overall_pass, checks, failed = _evaluate_acceptance(summary)

    assert overall_pass is True
    assert all(checks.values())
    assert failed == []

    summary["restart_table_compare"].pop("pending_wakes")
    summary["restart_table_compare"].pop("event_dedupe")
    overall_pass, _, failed = _evaluate_acceptance(summary)

    assert overall_pass is False
    assert failed == ["S6-R06", "S6-R07"]


def _write_stage6_assets(root: Any, *, with_manifest: bool = True) -> Any:
    sticker_dir = root / "stickers"
    image_dir = sticker_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / "stage6-01.gif").write_bytes(b"GIF89a\x01\x00\x01\x00\x00\x00\x00")
    if with_manifest:
        (sticker_dir / "manifest.yaml").write_text(
            "- id: stage6-01\n"
            "  name: Stage 6\n"
            "  file: images/stage6-01.gif\n"
            "  tags: [acceptance]\n"
            "  approved: true\n"
            "  usage_count: 0\n",
            encoding="utf-8",
        )
    return sticker_dir


def test_stage6_acceptance_config_must_enable_sticker_library(tmp_path: Any) -> None:
    """S6-M06 曾经因为脚本没写 sticker 块而假失败：默认 enabled=false 只是静默跳过。"""
    sticker_dir = _write_stage6_assets(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"stickerDir": str(sticker_dir)}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="未启用表情库"):
        assert_sticker_ready(config_path)


def test_stage6_acceptance_config_must_resolve_manifest(tmp_path: Any) -> None:
    sticker_dir = _write_stage6_assets(tmp_path, with_manifest=False)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "stickerDir": str(sticker_dir),
                "sticker": {"enabled": True, "allowedIds": ["stage6-01"]},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="表情库不可用"):
        assert_sticker_ready(config_path)


def test_stage6_acceptance_config_passes_with_enabled_allowlisted_sticker(tmp_path: Any) -> None:
    sticker_dir = _write_stage6_assets(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "stickerDir": str(sticker_dir),
                "sticker": {"enabled": True, "allowedIds": ["stage6-01"]},
            }
        ),
        encoding="utf-8",
    )

    assert_sticker_ready(config_path)


def _passing_summary() -> dict[str, Any]:
    equal_tables = {
        table: {"before_count": 1, "after_count": 1, "equal": True}
        for table in (
            "sessions",
            "memory_items",
            "learning_notes",
            "learning_state",
            "observations",
            "daily_send_counts",
            "pending_wakes",
            "event_dedupe",
        )
    }
    return {
        "mock": {
            "event_counts": {
                "M01_NORMAL": 2,
                "M02_MENTION": 2,
                "M03_POKE": 2,
            }
        },
        "run1_metrics": {
            "decisions": {"wake": 1, "deferred": 1},
            "categories": {"mention": 1, "poke": 1},
            "failed_or_denied_audit_count": 1,
            "tool_names": {"qq_dive": 1, "qq_get_group_history": 1},
            "orphan_tool_ids": [],
            "memory_count": 1,
            "sticker_usage_total": 1,
            "daily_send_total": 1,
            "pending_wake_count": 1,
        },
        "final_metrics": {
            "orphan_tool_ids": [],
            "session_states": {"group:100000": "diving"},
            "daily_send_total": 1,
            "sticker_usage_total": 1,
        },
        "restart_table_compare": equal_tables,
        "log_errors": {"traceback": 0, "error_lines": 0},
        "integrity_check": {
            "before": "ok",
            "after_start": "ok",
            "final": "ok",
        },
        "wal_files_after_stop": {
            "wal_exists": False,
            "shm_exists": False,
        },
    }

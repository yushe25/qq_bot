"""Stage 6 mock long-run and restart-consistency acceptance driver."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sqlite3
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
import uvicorn

from app.agent.llm_provider import ChatRequest, ChatResult
from app.config import AppConfig
from app.domain.types import ToolCall


class Stage6Provider:
    """Deterministic acceptance-only provider that exercises the full wake loop."""

    def __init__(self, config: object) -> None:
        self.config = config

    async def complete(self, req: ChatRequest) -> ChatResult:
        if _is_learning_request(req):
            content = json.dumps(
                {
                    "notes": [
                        {
                            "kind": "speech_style",
                            "key": "stage6",
                            "value": "群里偏好短句",
                            "confidence": 0.9,
                            "evidence": [
                                {
                                    "sender_id": "10001",
                                    "message_id": "stage6",
                                    "text": "普通消息",
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
            return _with_usage(req, ChatResult(kind="text", content=content), content)

        invocation = _last_user_content(req.messages)
        tool_count = _tool_count_after_last_user(req.messages)
        scene_id = _scene_id(req.system)

        if "S6_MULTI" in invocation:
            if tool_count == 0:
                return _tool_result(req, "qq_get_members", {}, "stage6-members")
            if tool_count == 1:
                return _tool_result(
                    req,
                    "qq_get_group_history",
                    {"limit": 5},
                    "stage6-history",
                )
            return _with_usage(req, ChatResult(kind="text", content="多轮工具完成"), "多轮工具完成")

        if "S6_STICKER" in invocation:
            if tool_count == 0:
                return _tool_result(
                    req,
                    "sticker_send",
                    {"stickerId": "stage6-01"},
                    "stage6-sticker",
                )
            return _with_usage(req, ChatResult(kind="text", content=""), "")

        if "S6_PRIVATE" in invocation or "私聊消息直接唤醒" in invocation:
            if tool_count == 0:
                return _tool_result(
                    req,
                    "qq_send_private_message",
                    {"userId": scene_id, "message": "私聊收到"},
                    "stage6-private",
                )
            return _with_usage(req, ChatResult(kind="text", content=""), "")

        if "S6_MEMORY" in invocation or "@" in invocation or "消息中包含 @" in invocation:
            if tool_count == 0:
                return _tool_result(
                    req,
                    "memory_update",
                    {
                        "kind": "active_topic",
                        "key": "stage6-topic",
                        "value": "Stage 6 验收话题",
                    },
                    "stage6-memory",
                    extra_calls=[
                        ToolCall(
                            name="qq_send_group_message",
                            arguments={"groupId": scene_id, "message": "收到"},
                            id="stage6-send",
                        )
                    ],
                )
            return _with_usage(req, ChatResult(kind="text", content=""), "")

        if tool_count == 0:
            return _tool_result(
                req,
                "qq_dive",
                {"nextWakeMs": 10_000, "reason": "Stage 6 潜水"},
                "stage6-dive",
            )
        return _with_usage(req, ChatResult(kind="text", content=""), "")


def _tool_result(
    req: ChatRequest,
    name: str,
    arguments: dict[str, Any],
    call_id: str,
    *,
    extra_calls: Sequence[ToolCall] = (),
) -> ChatResult:
    calls = [ToolCall(name=name, arguments=arguments, id=call_id), *extra_calls]
    response_text = json.dumps([call.arguments for call in calls], ensure_ascii=False)
    return _with_usage(req, ChatResult(kind="tool_calls", calls=calls), response_text)


def _message_role(message: object) -> str:
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "role", "") or "")


def _message_content(message: object) -> str:
    if isinstance(message, dict):
        value = message.get("content")
    else:
        value = getattr(message, "content", None)
    return str(value) if value is not None else ""


def _last_user_index(messages: Sequence[object]) -> int:
    last_user = -1
    for index, message in enumerate(messages):
        if _message_role(message) == "user":
            last_user = index
    return last_user


def _tool_count_after_last_user(messages: Sequence[object]) -> int:
    last_user = _last_user_index(messages)
    return sum(
        1
        for index, message in enumerate(messages)
        if index > last_user and _message_role(message) == "tool"
    )


def _last_user_content(messages: Sequence[object]) -> str:
    return next(
        (_message_content(message) for message in reversed(messages) if _message_role(message) == "user"),
        "",
    )


def _is_learning_request(req: ChatRequest) -> bool:
    marker = req.system + "\n" + _last_user_content(req.messages)
    return "群聊风格观察器" in marker or "提炼 1 到 3 条学习笔记" in marker


def _scene_id(system_prompt: str) -> str:
    for line in system_prompt.splitlines():
        if line.startswith("当前会话："):
            parts = line.split()
            if parts:
                return parts[-1]
    return "100000"


def _with_usage(req: ChatRequest, result: ChatResult, response_text: str) -> ChatResult:
    prompt_text = req.system + "".join(_message_content(message) for message in req.messages)
    result.prompt_tokens = max(1, len(prompt_text) // 3)
    result.completion_tokens = max(1, len(response_text) // 3)
    result.total_tokens = result.prompt_tokens + result.completion_tokens
    return result


@dataclass
class ProcessHandle:
    name: str
    process: asyncio.subprocess.Process
    log_handle: Any


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _snapshot_database(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [
            "sessions",
            "memory_items",
            "learning_notes",
            "learning_state",
            "observations",
            "daily_send_counts",
            "sticker_usage_counts",
            "wake_log",
            "audit_log",
            "audit_logs",
            "pending_wakes",
            "event_dedupe",
        ]
        result: dict[str, Any] = {"integrity_check": integrity, "tables": {}}
        for table in tables:
            try:
                rows = connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            except sqlite3.OperationalError:
                continue
            result["tables"][table] = [dict(row) for row in rows]
        return result
    finally:
        connection.close()


def _table_summary(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    before_tables = before.get("tables", {})
    after_tables = after.get("tables", {})
    for table in sorted(set(before_tables) | set(after_tables)):
        before_rows = before_tables.get(table, [])
        after_rows = after_tables.get(table, [])
        tables[table] = {
            "before_count": len(before_rows),
            "after_count": len(after_rows),
            "equal": before_rows == after_rows,
        }
    return tables


async def _write_progress(
    path: Path,
    *,
    started_at: float,
    phase: str,
    active_samples: list[int],
    events: dict[str, Any],
) -> None:
    _write_json(
        path,
        {
            "started_at": started_at,
            "updated_at": time.time(),
            "elapsed_seconds": time.time() - started_at,
            "phase": phase,
            "peak_active_sessions": max(active_samples, default=0),
            "event_counts": events,
        },
    )


async def _spawn(name: str, args: list[str], log_path: Path) -> ProcessHandle:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("ab")
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=ROOT,
        stdout=log_handle,
        stderr=asyncio.subprocess.STDOUT,
    )
    return ProcessHandle(name=name, process=process, log_handle=log_handle)


async def _stop_process(handle: ProcessHandle, stop_file: Path, timeout: float = 20.0) -> int:
    stop_file.write_text("stop\n", encoding="utf-8")
    try:
        return await asyncio.wait_for(handle.process.wait(), timeout=timeout)
    except TimeoutError:
        handle.process.kill()
        return await handle.process.wait()
    finally:
        handle.log_handle.close()


async def _wait_health(port: int, process: asyncio.subprocess.Process, timeout: float = 30.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    async with httpx.AsyncClient(timeout=1.0) as client:
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is not None:
                raise RuntimeError(f"QQF worker exited early with code {process.returncode}")
            try:
                response = await client.get(f"http://127.0.0.1:{port}/api/health")
                if response.status_code == 200 and response.json().get("ok") is True:
                    return
            except (httpx.HTTPError, json.JSONDecodeError):
                pass
            await asyncio.sleep(0.25)
    raise TimeoutError("QQF health check timed out")


async def _monitor(
    duration_seconds: float,
    port: int,
    progress_path: Path,
    started_at: float,
    phase: str,
    event_counts: dict[str, Any],
    active_samples: list[int],
) -> None:
    deadline = time.time() + duration_seconds
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.time() < deadline:
            try:
                response = await client.get(f"http://127.0.0.1:{port}/api/sessions")
                response.raise_for_status()
                sessions = response.json().get("sessions", [])
                active = sum(1 for item in sessions if item.get("state") in {"active", "waking"})
                active_samples.append(active)
            except (httpx.HTTPError, json.JSONDecodeError):
                pass
            await _write_progress(
                progress_path,
                started_at=started_at,
                phase=phase,
                active_samples=active_samples,
                events=event_counts,
            )
            await asyncio.sleep(1.0)


def _prepare_files(
    run_dir: Path,
    *,
    http_port: int,
    ws_port: int,
    console_port: int,
    duration_seconds: float,
) -> Path:
    state_dir = run_dir / "state"
    sticker_dir = run_dir / "stickers"
    image_dir = sticker_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / "stage6-01.gif").write_bytes(
        b"GIF89a\x01\x00\x01\x00\x00\x00\x00"
    )
    (sticker_dir / "manifest.yaml").write_text(
        "- id: stage6-01\n"
        "  name: Stage 6\n"
        "  file: images/stage6-01.gif\n"
        "  tags: [acceptance]\n"
        "  approved: true\n"
        "  usage_count: 0\n",
        encoding="utf-8",
    )
    (run_dir / "persona.yaml").write_text(
        "id: stage6\n"
        "name: Stage6\n"
        "identity: 验收群友\n"
        "personality: 稳定、按场景行动\n"
        "speaking: 短句\n"
        "schedule:\n"
        "  timezone: Asia/Shanghai\n"
        "  active_windows:\n"
        "    - [0, 24]\n"
        "  quiet_window: null\n"
        "  reply_delay: {min_seconds: 0, max_seconds: 0}\n"
        "  mention_delay: {min_seconds: 0, max_seconds: 0}\n"
        "  sticker_only_probability: 0.0\n"
        "  sticker_with_text_probability: 1.0\n",
        encoding="utf-8",
    )
    batch_window_ms = 1000 if duration_seconds < 600 else 8000
    config = {
        "qq": {
            "wsUrl": f"ws://127.0.0.1:{ws_port}",
            "httpUrl": f"http://127.0.0.1:{http_port}",
            "accessToken": "",
            "ownerQq": "10001",
        },
        "llm": {"provider": "mock"},
        "scheduler": {
            "batchWindowMs": batch_window_ms,
            "wakeProbability": 1.0,
            "mentionDiveProbability": 0.0,
            "names": ["小皮"],
            "keywords": ["S6_STICKER", "S6_MULTI", "S6_DIVE", "S6_PERIODIC"],
            "maxWakesPerMinute": 6,
            "maxWakesPerHour": 20,
            "maxConcurrent": 1,
            "maxToolSteps": 6,
            "maxTextFallback": True,
            "noActionLimit": 2,
            "preSleepWaitMinutes": 0,
            "minSleepMs": 1000,
            "maxSleepMs": 5000,
            "cooldownSeconds": 1,
        },
        "tokenBudget": {
            "wakeMessages": 12,
            "historyMessages": 28,
            "eventTextChars": 160,
            "toolResultChars": 1600,
        },
        "learning": {
            "enabled": True,
            "intervalMinutes": 0.1,
            "minNewMessages": 1,
            "confidenceThreshold": 0.7,
            "maxNotesPerSession": 20,
            "notesTtlDays": 7,
            "maxObservationsPerSession": 250,
            "excludeMembers": [],
        },
        "safety": {
            "allowGroups": ["100000"],
            "allowPrivate": ["10001"],
            "maxGroupMembers": 20,
            "maxSendsPerMinute": 1,
            "maxSendsPerHour": 5,
            "maxSendsPerDay": 20,
            "maxSendsPerWake": 3,
            "maxMessageChars": 200,
            "maxImageBytes": 1048576,
            "maxImagePixels": 10000,
        },
        "console": {
            "host": "127.0.0.1",
            "port": console_port,
            "token": "",
        },
        "storage": {
            "dir": str(state_dir),
            "databasePath": str(state_dir / "qqf.db"),
        },
        "personaPath": str(run_dir / "persona.yaml"),
        "stickerDir": str(sticker_dir),
    }
    config_path = run_dir / "config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


def _mock_report_summary(paths: Sequence[Path]) -> dict[str, Any]:
    event_counts: dict[str, int] = {}
    action_count = 0
    connection_count = 0
    for path in paths:
        if not path.is_file():
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        action_count += int(report.get("action_count", 0))
        connection_count += int(report.get("connection_count", 0))
        for key, value in dict(report.get("event_counts", {})).items():
            event_counts[key] = event_counts.get(key, 0) + int(value)
    return {
        "event_counts": event_counts,
        "action_count": action_count,
        "connection_count": connection_count,
    }


def _database_metrics(snapshot: dict[str, Any]) -> dict[str, Any]:
    tables = snapshot.get("tables", {})
    wake_logs = list(tables.get("wake_log", []))
    audits = list(tables.get("audit_logs", []))
    sessions = list(tables.get("sessions", []))
    sticker_counts = list(tables.get("sticker_usage_counts", []))
    decisions: dict[str, int] = {}
    categories: dict[str, int] = {}
    for row in wake_logs:
        decision = str(row.get("decided", ""))
        decisions[decision] = decisions.get(decision, 0) + 1
        category = str(row.get("category", ""))
        if category:
            categories[category] = categories.get(category, 0) + 1
    failed_audits = sum(
        1
        for row in audits
        if "fail" in str(row.get("result", "")).lower()
        or "拒绝" in str(row.get("result", ""))
        or "denied" in str(row.get("result", "")).lower()
    )
    tool_names: dict[str, int] = {}
    orphan_tool_ids: list[str] = []
    for session in sessions:
        try:
            conversation = json.loads(str(session.get("conversation", "[]")))
        except json.JSONDecodeError:
            continue
        call_ids = {
            str(call.get("id"))
            for message in conversation
            if isinstance(message, dict) and message.get("role") == "assistant"
            for call in message.get("tool_calls", [])
            if isinstance(call, dict)
        }
        for message in conversation:
            if not isinstance(message, dict):
                continue
            if message.get("role") == "assistant":
                for call in message.get("tool_calls", []):
                    if isinstance(call, dict):
                        function = call.get("function")
                        if isinstance(function, dict):
                            name = str(function.get("name", ""))
                            tool_names[name] = tool_names.get(name, 0) + 1
            elif message.get("role") == "tool" and str(message.get("tool_call_id", "")) not in call_ids:
                orphan_tool_ids.append(str(message.get("tool_call_id", "")))
    return {
        "wake_log_count": len(wake_logs),
        "decisions": decisions,
        "categories": categories,
        "audit_count": len(audits),
        "failed_or_denied_audit_count": failed_audits,
        "tool_names": tool_names,
        "orphan_tool_ids": orphan_tool_ids,
        "session_states": {
            str(row.get("id")): str(row.get("state"))
            for row in sessions
        },
        "daily_send_total": sum(int(row.get("count", 0)) for row in tables.get("daily_send_counts", [])),
        "sticker_usage_total": sum(int(row.get("count", 0)) for row in sticker_counts),
        "memory_count": len(tables.get("memory_items", [])),
        "pending_wake_count": len(tables.get("pending_wakes", [])),
        "event_dedupe_count": len(tables.get("event_dedupe", [])),
    }


def _compare_table_equal(summary: dict[str, Any], table: str) -> bool:
    comparison = summary.get("restart_table_compare", {}).get(table)
    return isinstance(comparison, dict) and comparison.get("equal") is True


def _evaluate_acceptance(summary: dict[str, Any]) -> tuple[bool, dict[str, bool], list[str]]:
    events = summary.get("mock", {}).get("event_counts", {})
    run1 = summary.get("run1_metrics", {})
    final = summary.get("final_metrics", {})
    log_errors = summary.get("log_errors", {})
    integrity = summary.get("integrity_check", {})
    wal = summary.get("wal_files_after_stop", {})
    run1_decisions = run1.get("decisions", {})
    run1_categories = run1.get("categories", {})
    final_states = final.get("session_states", {})

    checks = {
        "S6-M01": int(events.get("M01_NORMAL", 0)) > 0 and int(run1_decisions.get("wake", 0)) > 0,
        "S6-M02": int(events.get("M02_MENTION", 0)) > 0 and int(run1_categories.get("mention", 0)) > 0,
        "S6-M03": int(events.get("M03_POKE", 0)) > 0 and int(run1_categories.get("poke", 0)) > 0,
        "S6-M04": int(run1.get("tool_names", {}).get("qq_dive", 0)) > 0,
        "S6-M05": int(run1.get("memory_count", 0)) > 0
        and _compare_table_equal(summary, "memory_items"),
        "S6-M06": int(run1.get("sticker_usage_total", 0)) > 0,
        "S6-M07": int(run1_decisions.get("deferred", 0)) > 0
        or int(run1.get("failed_or_denied_audit_count", 0)) > 0,
        "S6-M08": int(run1_decisions.get("deferred", 0)) > 0,
        "S6-M09": int(run1.get("tool_names", {}).get("qq_get_group_history", 0)) > 0
        and not run1.get("orphan_tool_ids"),
        "S6-M10": int(log_errors.get("traceback", 0)) == 0
        and int(log_errors.get("error_lines", 0)) == 0
        and all(str(state) not in {"active", "waking"} for state in final_states.values()),
        "S6-R01": _compare_table_equal(summary, "sessions")
        and all(str(state) not in {"active", "waking"} for state in final_states.values()),
        "S6-R02": _compare_table_equal(summary, "sessions") and not final.get("orphan_tool_ids"),
        "S6-R03": _compare_table_equal(summary, "memory_items"),
        "S6-R04": _compare_table_equal(summary, "learning_notes")
        and _compare_table_equal(summary, "learning_state")
        and _compare_table_equal(summary, "observations"),
        "S6-R05": _compare_table_equal(summary, "daily_send_counts"),
        "S6-R06": "pending_wakes" in summary.get("restart_table_compare", {})
        and int(run1.get("pending_wake_count", 0)) > 0,
        "S6-R07": "event_dedupe" in summary.get("restart_table_compare", {})
        and int(final.get("daily_send_total", -1)) == int(run1.get("daily_send_total", -2))
        and int(final.get("sticker_usage_total", -1)) == int(run1.get("sticker_usage_total", -2)),
        "S6-R08": integrity.get("before") == "ok"
        and integrity.get("after_start") == "ok"
        and integrity.get("final") == "ok"
        and wal.get("wal_exists") is False
        and wal.get("shm_exists") is False,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return not failed, checks, failed


def _log_errors(paths: Sequence[Path]) -> dict[str, int]:
    result = {"traceback": 0, "error_lines": 0}
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        result["traceback"] += text.count("Traceback (most recent call last)")
        result["error_lines"] += sum(
            1 for line in text.splitlines() if " ERROR " in line or line.startswith("ERROR")
        )
    return result


async def run_worker(config_path: Path, stop_file: Path) -> None:
    import app.main as app_main

    config = AppConfig.load(config_path)
    app_main.build_llm_provider = lambda llm_config: Stage6Provider(llm_config)
    app = app_main.create_app(config)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.console.host,
            port=config.console.port,
            log_level="info",
        )
    )

    async def wait_for_stop() -> None:
        while not stop_file.exists():
            await asyncio.sleep(0.25)
        server.should_exit = True

    watcher = asyncio.create_task(wait_for_stop())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass


async def run_acceptance(args: argparse.Namespace) -> int:
    run_dir = Path(args.report_dir).resolve()
    if run_dir.exists() and any(run_dir.iterdir()):
        run_dir = run_dir.with_name(f"{run_dir.name}-{int(time.time())}")
    run_dir.mkdir(parents=True, exist_ok=True)

    duration_seconds = max(30.0, float(args.duration_minutes) * 60.0)
    restart_at = min(duration_seconds - 15.0, duration_seconds / 2.0)
    scenario_interval = float(args.scenario_interval)
    http_port = _free_port()
    ws_port = _free_port()
    console_port = _free_port()
    started_at = time.time()
    progress_path = run_dir / "progress.json"
    config_path = _prepare_files(
        run_dir,
        http_port=http_port,
        ws_port=ws_port,
        console_port=console_port,
        duration_seconds=duration_seconds,
    )

    mock1_stop = run_dir / "mock1.stop"
    mock2_stop = run_dir / "mock2.stop"
    qqf1_stop = run_dir / "qqf1.stop"
    qqf2_stop = run_dir / "qqf2.stop"
    for path in (mock1_stop, mock2_stop, qqf1_stop, qqf2_stop):
        path.unlink(missing_ok=True)

    mock_report1 = run_dir / "mock-run1.json"
    mock_report2 = run_dir / "mock-run2.json"
    qqf_log1 = run_dir / "qqf-run1.log"
    qqf_log2 = run_dir / "qqf-run2.log"
    active_samples: list[int] = []
    processes: list[ProcessHandle] = []
    phase = "starting"
    event_counts: dict[str, Any] = {}
    before: dict[str, Any] = {}
    after_start: dict[str, Any] = {}
    final_snapshot: dict[str, Any] = {}
    result_code = 1

    def mock_args(stop_file: Path, report_path: Path) -> list[str]:
        return [
            sys.executable,
            str(ROOT / "scripts" / "mock_qq_server.py"),
            "--host",
            "127.0.0.1",
            "--http-port",
            str(http_port),
            "--ws-port",
            str(ws_port),
            "--group",
            "100000",
            "--bot-id",
            "31415926",
            "--stage6",
            "--seed",
            str(args.seed),
            "--scenario-interval",
            str(scenario_interval),
            "--periodic-interval",
            str(max(5.0, scenario_interval * 2)),
            "--stop-file",
            str(stop_file),
            "--report",
            str(report_path),
        ]

    def qqf_args(stop_file: Path) -> list[str]:
        return [
            sys.executable,
            "-m",
            "scripts.stage6_acceptance",
            "--worker",
            "--config",
            str(config_path),
            "--stop-file",
            str(stop_file),
        ]

    try:
        mock1 = await _spawn("mock1", mock_args(mock1_stop, mock_report1), run_dir / "mock-run1.log")
        processes.append(mock1)
        await asyncio.sleep(1.0)
        qqf1 = await _spawn("qqf1", qqf_args(qqf1_stop), qqf_log1)
        processes.append(qqf1)
        await _wait_health(console_port, qqf1.process)

        phase = "run1"
        await _monitor(
            max(5.0, restart_at - 1.0),
            console_port,
            progress_path,
            started_at,
            phase,
            event_counts,
            active_samples,
        )
        await _stop_process(mock1, mock1_stop)
        await _stop_process(qqf1, qqf1_stop)
        before = _snapshot_database(Path(AppConfig.load(config_path).storage.database_path))

        qqf2 = await _spawn("qqf2", qqf_args(qqf2_stop), qqf_log2)
        processes.append(qqf2)
        await _wait_health(console_port, qqf2.process)
        after_start = _snapshot_database(Path(AppConfig.load(config_path).storage.database_path))

        mock2 = await _spawn("mock2", mock_args(mock2_stop, mock_report2), run_dir / "mock-run2.log")
        processes.append(mock2)
        await asyncio.sleep(1.0)

        remaining = max(1.0, duration_seconds - (time.time() - started_at))
        phase = "run2"
        await _monitor(
            remaining,
            console_port,
            progress_path,
            started_at,
            phase,
            event_counts,
            active_samples,
        )
        await _stop_process(mock2, mock2_stop)
        await _stop_process(qqf2, qqf2_stop)
        await asyncio.sleep(1.0)
        final_snapshot = _snapshot_database(Path(AppConfig.load(config_path).storage.database_path))

        mock_summary = _mock_report_summary([mock_report1, mock_report2])
        table_compare = _table_summary(before, after_start)
        error_counts = _log_errors([qqf_log1, qqf_log2])
        wal_files = {
            "wal_exists": (run_dir / "state" / "qqf.db-wal").exists(),
            "shm_exists": (run_dir / "state" / "qqf.db-shm").exists(),
        }
        summary = {
            "started_at": started_at,
            "finished_at": time.time(),
            "duration_seconds": time.time() - started_at,
            "requested_duration_minutes": float(args.duration_minutes),
            "restart_at_seconds": restart_at,
            "seed": args.seed,
            "ports": {
                "http": http_port,
                "ws": ws_port,
                "console": console_port,
            },
            "mock": mock_summary,
            "run1_metrics": _database_metrics(before),
            "post_start_metrics": _database_metrics(after_start),
            "final_metrics": _database_metrics(final_snapshot),
            "peak_active_sessions": max(active_samples, default=0),
            "restart_table_compare": table_compare,
            "integrity_check": {
                "before": before.get("integrity_check"),
                "after_start": after_start.get("integrity_check"),
                "final": final_snapshot.get("integrity_check"),
            },
            "wal_files_after_stop": wal_files,
            "log_errors": error_counts,
        }
        overall_pass, checks, failed_checks = _evaluate_acceptance(summary)
        summary["checks"] = checks
        summary["failed_checks"] = failed_checks
        summary["overall_pass"] = overall_pass
        _write_json(run_dir / "summary.json", summary)
        result_code = 0 if overall_pass else 1
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return result_code
    finally:
        for handle in processes:
            if handle.process.returncode is None:
                handle.process.kill()
                await handle.process.wait()
            if not handle.log_handle.closed:
                handle.log_handle.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the QQF Stage 6 mock acceptance matrix")
    parser.add_argument("--duration-minutes", type=float, default=30.0)
    parser.add_argument("--report-dir", default=".pytest-tmp/stage6-20260913")
    parser.add_argument("--scenario-interval", type=float, default=10.0)
    parser.add_argument("--seed", type=int, default=20260913)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--config", help=argparse.SUPPRESS)
    parser.add_argument("--stop-file", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.worker:
        if not args.config or not args.stop_file:
            raise SystemExit("--worker requires --config and --stop-file")
        asyncio.run(run_worker(Path(args.config), Path(args.stop_file)))
        return

    raise SystemExit(asyncio.run(run_acceptance(args)))


if __name__ == "__main__":
    main()

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import ConsoleConfig
from app.console.router import build_console_router
from app.console.service import ConsoleService
from app.memory.store import MemoryStore
from app.runtime.session_manager import SessionManager
from app.storage.db import StateStore


async def _client(tmp_path: Path) -> tuple[TestClient, StateStore]:
    store = StateStore(tmp_path / "logs.db")
    await store.init()
    service = ConsoleService(
        SessionManager(store),
        MemoryStore(store),
        store,
    )
    app = FastAPI()
    app.state.runtime = SimpleNamespace(console=service)
    app.include_router(build_console_router(ConsoleConfig(token="secret")))
    return TestClient(app), store


async def _seed_logs(store: StateStore) -> None:
    for index in range(25):
        timestamp = float(1000 + index)
        await store._execute(
            """
            INSERT INTO wake_log (
                session_id, category, reason, decided, probability, next_wake_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "group:100000",
                "normal",
                f"wake-{index}",
                "no_action",
                0.04,
                None,
                timestamp,
            ),
        )
        await store._execute(
            """
            INSERT INTO audit_logs (timestamp, action, detail, result, session_id, actor_type)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                f"audit-{index}",
                f"detail-{index}",
                "ok",
                "group:100000",
                "system",
            ),
        )
    await store._execute("UPDATE wake_log SET created_at = 5000 WHERE reason = 'wake-24'")
    await store._execute("UPDATE audit_logs SET timestamp = 5000 WHERE action = 'audit-24'")


async def test_merged_logs_filter_order_and_pagination(tmp_path: Path) -> None:
    client, store = await _client(tmp_path)
    headers = {"authorization": "Bearer secret"}
    try:
        await _seed_logs(store)
        first = client.get("/api/logs?type=all&limit=20", headers=headers)
        second = client.get(
            f"/api/logs?type=all&limit=20&cursor={first.json()['nextCursor']}",
            headers=headers,
        )
        third = client.get(
            f"/api/logs?type=all&limit=20&cursor={second.json()['nextCursor']}",
            headers=headers,
        )
        wake = client.get("/api/logs?type=wake&limit=100", headers=headers)
        audit = client.get("/api/logs?type=audit&limit=100", headers=headers)
        invalid = client.get("/api/logs?cursor=bad", headers=headers)
        invalid_type = client.get("/api/logs?type=other", headers=headers)
    finally:
        await store.close()

    assert first.status_code == 200
    assert len(first.json()["items"]) == 20
    assert len(second.json()["items"]) == 20
    assert len(third.json()["items"]) == 10
    assert third.json()["nextCursor"] is None
    all_items = [*first.json()["items"], *second.json()["items"], *third.json()["items"]]
    assert len({item["id"] for item in all_items}) == 50
    assert all_items[0]["type"] == "audit"
    assert all_items[0]["title"] == "audit-24"
    assert all_items[1]["type"] == "wake"
    assert all_items[1]["title"] == "normal"
    assert all(item["type"] == "wake" for item in wake.json()["items"])
    assert all(item["type"] == "audit" for item in audit.json()["items"])
    assert len(wake.json()["items"]) == 25
    assert len(audit.json()["items"]) == 25
    assert invalid.status_code == 400
    assert invalid_type.status_code == 422

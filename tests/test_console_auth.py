from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import ConsoleConfig
from app.console.router import build_console_router
from app.domain.types import Session, SessionState


def _test_client(token: str = "") -> tuple[TestClient, FastAPI]:
    app = FastAPI()
    app.state.runtime = SimpleNamespace(
        started_at=1_700_000_000.0,
        sessions=SimpleNamespace(
            all=lambda: [
                Session(id="group:100000", scene_type="group", scene_id="100000", state=SessionState.ACTIVE)
            ]
        ),
    )
    app.include_router(build_console_router(ConsoleConfig(token=token)))
    return TestClient(app), app


def test_console_token_required() -> None:
    client, _ = _test_client(token="secret")

    response = client.get("/")

    assert response.status_code == 401


def test_console_token_via_query_parameter_and_header() -> None:
    client, _ = _test_client(token="secret")

    response = client.get("/", params={"token": "secret"})
    assert response.status_code == 200
    assert "QQF 控制台" in response.text

    response = client.get("/api/health", headers={"authorization": "Bearer secret"})
    assert response.status_code == 200
    assert response.json()["sessionCount"] == 1


def test_console_blocks_foreign_host_without_token() -> None:
    client, _ = _test_client()

    response = client.get("/api/health")

    assert response.status_code == 401


async def test_console_reset_daily_sends_endpoint() -> None:
    reset_calls: list[str] = []

    async def reset_daily_sends(session_id: str) -> bool:
        reset_calls.append(session_id)
        return True

    app = FastAPI()
    app.state.runtime = SimpleNamespace(
        sessions=SimpleNamespace(
            get=lambda session_id: (
                Session(id=session_id, scene_type="group", scene_id="100000")
                if session_id == "group:100000"
                else None
            )
        ),
        safety=SimpleNamespace(reset_daily_sends=reset_daily_sends),
    )
    app.include_router(build_console_router(ConsoleConfig(token="secret")))
    client = TestClient(app)
    headers = {"authorization": "Bearer secret"}

    response = client.post("/api/sessions/group:100000/reset-daily-sends", headers=headers)
    missing = client.post("/api/sessions/group:999999/reset-daily-sends", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert reset_calls == ["group:100000"]
    assert missing.status_code == 404

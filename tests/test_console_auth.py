from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.console.router as console_router
from app.config import ConsoleConfig
from app.console.router import _consume_ws_ticket, _issue_ws_ticket, build_console_router
from app.domain.types import Session, SessionState


class _Bus:
    def subscribe(self, subscriber) -> None:
        self.subscriber = subscriber

    def unsubscribe(self, subscriber) -> None:
        del subscriber


def _test_client(token: str = "") -> tuple[TestClient, FastAPI]:
    app = FastAPI()
    app.state.runtime = SimpleNamespace(
        started_at=1_700_000_000.0,
        sessions=SimpleNamespace(
            all=lambda: [
                Session(id="group:100000", scene_type="group", scene_id="100000", state=SessionState.ACTIVE)
            ]
        ),
        bus=_Bus(),
    )
    app.include_router(build_console_router(ConsoleConfig(token=token)))
    return TestClient(app), app


def test_console_token_required() -> None:
    client, _ = _test_client(token="secret")

    page = client.get("/")
    response = client.get("/api/health")

    assert page.status_code == 200
    assert response.status_code == 401


def test_console_long_token_is_not_accepted_in_url() -> None:
    client, _ = _test_client(token="secret")

    response = client.get("/api/health", params={"token": "secret"})
    assert response.status_code == 401

    response = client.get("/api/health", headers={"authorization": "Bearer secret"})
    assert response.status_code == 200
    assert response.json()["sessionCount"] == 1


def test_console_ws_ticket_is_single_use() -> None:
    client, _ = _test_client(token="secret")
    headers = {"authorization": "Bearer secret"}
    ticket = client.post("/api/ws-ticket", headers=headers).json()["ticket"]

    with client.websocket_connect(f"/ws/live?ticket={ticket}") as websocket:
        assert websocket.receive_json()["type"] == "connected"

    with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect(
        f"/ws/live?ticket={ticket}"
    ):
        pass
    assert exc_info.value.code == 1008


def test_console_ws_rejects_long_token_in_url() -> None:
    client, _ = _test_client(token="secret")

    with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect(
        "/ws/live?token=secret"
    ):
        pass

    assert exc_info.value.code == 1008


def test_console_ws_ticket_expires(monkeypatch) -> None:
    client, _ = _test_client(token="secret")
    headers = {"authorization": "Bearer secret"}
    now = [1_000.0]
    monkeypatch.setattr(console_router.time, "time", lambda: now[0])
    ticket = client.post("/api/ws-ticket", headers=headers).json()["ticket"]
    now[0] += console_router.WS_TICKET_TTL_SECONDS + 1

    with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect(
        f"/ws/live?ticket={ticket}"
    ):
        pass

    assert exc_info.value.code == 1008


def test_console_ws_ticket_is_source_bound() -> None:
    tickets: dict[str, console_router._WsTicket] = {}
    ticket = _issue_ws_ticket(tickets, "host-a")

    assert _consume_ws_ticket(tickets, ticket, "host-b") is False
    assert ticket not in tickets


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

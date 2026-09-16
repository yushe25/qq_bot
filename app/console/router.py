from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import ConsoleConfig
from app.console.service import InvalidLogCursor, InvalidReviewCursor
from app.safety.content_filter import ContentFilter

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
WS_TICKET_TTL_SECONDS = 60
PUBLIC_DIR = Path(__file__).resolve().parent / "public"
_METADATA_FILTER = ContentFilter()


@dataclass(frozen=True)
class _WsTicket:
    expires_at: float
    client_host: str


class WakeBody(BaseModel):
    reason: str = "控制台强制唤醒"


class StickerMetadataBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=8)
    moods: list[str] = Field(default_factory=list, max_length=8)
    scenes: list[str] = Field(default_factory=list, max_length=8)
    avoid_scenes: list[str] = Field(default_factory=list, max_length=8, alias="avoidScenes")

    @field_validator("name", "description")
    @classmethod
    def validate_sensitive_text(cls, value: str) -> str:
        text = value.strip()
        ok, _ = _METADATA_FILTER.check(text)
        if not ok:
            raise ValueError("metadata contains sensitive content")
        return text

    @field_validator("tags", "moods", "scenes", "avoid_scenes")
    @classmethod
    def validate_semantic_list(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            text = value.strip()
            if not text:
                raise ValueError("metadata entries must not be blank")
            if len(text) > 40:
                raise ValueError("metadata entries must be at most 40 characters")
            ok, _ = _METADATA_FILTER.check(text)
            if not ok:
                raise ValueError("metadata contains sensitive content")
            if text in cleaned:
                raise ValueError("metadata entries must not contain duplicates")
            cleaned.append(text)
        return cleaned


class StickerApproveBody(BaseModel):
    model_config = {"populate_by_name": True}

    group_id: str = Field(alias="groupId")
    metadata: StickerMetadataBody = Field(default_factory=StickerMetadataBody)


class StickerRejectBody(BaseModel):
    model_config = {"populate_by_name": True}

    group_id: str = Field(alias="groupId")
    reason: str = ""


def build_console_router(config: ConsoleConfig) -> APIRouter:
    router = APIRouter()
    require_auth = _make_auth_dependency(config)
    ws_tickets: dict[str, _WsTicket] = {}

    @router.get("/", response_class=HTMLResponse)
    async def console_page() -> str:
        page = PUBLIC_DIR / "console.html"
        if page.exists():
            return page.read_text(encoding="utf-8")
        return "<h1>console.html not found</h1>"

    @router.get("/api/health", dependencies=[Depends(require_auth)])
    async def health(request: Request) -> dict[str, Any]:
        runtime = _runtime(request)
        sessions = runtime.sessions.all()
        return {
            "ok": True,
            "service": "qqf",
            "version": "0.1.0",
            "uptimeSeconds": int(time.time() - runtime.started_at),
            "sessionCount": len(sessions),
            "activeSessions": sum(1 for session in sessions if str(session.state) in {"active", "waking"}),
        }

    @router.post("/api/ws-ticket", dependencies=[Depends(require_auth)])
    async def issue_ws_ticket(request: Request) -> dict[str, Any]:
        host = request.client.host if request.client else ""
        return {
            "ticket": _issue_ws_ticket(ws_tickets, host),
            "expiresIn": WS_TICKET_TTL_SECONDS,
        }

    @router.get("/api/sessions", dependencies=[Depends(require_auth)])
    async def list_sessions(request: Request) -> dict[str, Any]:
        runtime = _runtime(request)
        sessions = sorted(runtime.sessions.all(), key=lambda item: item.id)
        return {"sessions": [session.to_dict() for session in sessions]}

    @router.get("/api/sessions/{session_id}", dependencies=[Depends(require_auth)])
    async def get_session(request: Request, session_id: str) -> dict[str, Any]:
        runtime = _runtime(request)
        session = runtime.sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        logs = await runtime.store.list_wake_logs(session_id, limit=50)
        return {"session": session.to_dict(), "wake_logs": logs}

    @router.post("/api/sessions/{session_id}/wake", dependencies=[Depends(require_auth)])
    async def wake_session(request: Request, session_id: str, body: WakeBody) -> dict[str, Any]:
        runtime = _runtime(request)
        return await runtime.scheduler.force_wake(session_id, reason=body.reason)

    @router.post("/api/sessions/{session_id}/pause", dependencies=[Depends(require_auth)])
    async def pause_session(request: Request, session_id: str) -> dict[str, Any]:
        runtime = _runtime(request)
        if not await runtime.console.pause(session_id):
            raise HTTPException(status_code=404, detail="session not found")
        return {"ok": True}

    @router.post("/api/sessions/{session_id}/resume", dependencies=[Depends(require_auth)])
    async def resume_session(request: Request, session_id: str) -> dict[str, Any]:
        runtime = _runtime(request)
        if not await runtime.console.resume(session_id):
            raise HTTPException(status_code=404, detail="session not found")
        return {"ok": True}

    @router.post("/api/sessions/{session_id}/reset-memory", dependencies=[Depends(require_auth)])
    async def reset_memory(request: Request, session_id: str) -> dict[str, Any]:
        runtime = _runtime(request)
        if not await runtime.console.reset_memory(session_id):
            raise HTTPException(status_code=404, detail="session not found")
        return {"ok": True}

    @router.post("/api/sessions/{session_id}/reset-daily-sends", dependencies=[Depends(require_auth)])
    async def reset_daily_sends(request: Request, session_id: str) -> dict[str, Any]:
        runtime = _runtime(request)
        if runtime.sessions.get(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        if not await runtime.safety.reset_daily_sends(session_id):
            raise HTTPException(status_code=500, detail="daily send reset unavailable")
        return {"ok": True}

    @router.delete("/api/sessions/{session_id}/learning-notes", dependencies=[Depends(require_auth)])
    async def delete_learning_note(
        request: Request,
        session_id: str,
        kind: str = Query(...),
        key: str = Query(...),
    ) -> dict[str, Any]:
        runtime = _runtime(request)
        if runtime.sessions.get(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        if not await runtime.console.delete_learning_note(session_id, kind, key):
            raise HTTPException(status_code=404, detail="learning note not found")
        return {"ok": True}

    @router.delete("/api/sessions/{session_id}/learning-members/{member_id}", dependencies=[Depends(require_auth)])
    async def delete_learning_member(
        request: Request,
        session_id: str,
        member_id: str,
    ) -> dict[str, Any]:
        runtime = _runtime(request)
        if runtime.sessions.get(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        if not await runtime.console.delete_member_data(session_id, member_id):
            raise HTTPException(status_code=404, detail="session not found")
        return {"ok": True}

    @router.delete("/api/sessions/{session_id}/learning", dependencies=[Depends(require_auth)])
    async def clear_learning(request: Request, session_id: str) -> dict[str, Any]:
        runtime = _runtime(request)
        if runtime.sessions.get(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        if not await runtime.console.clear_learning(session_id):
            raise HTTPException(status_code=404, detail="session not found")
        return {"ok": True}

    @router.get("/api/audit", dependencies=[Depends(require_auth)])
    async def audit_logs(request: Request, limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        runtime = _runtime(request)
        return {"items": await runtime.audit.recent(limit=limit)}

    @router.get("/api/wake-logs", dependencies=[Depends(require_auth)])
    async def wake_logs(
        request: Request,
        session_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        runtime = _runtime(request)
        return {"items": await runtime.store.list_wake_logs(session_id=session_id, limit=limit)}

    @router.get("/api/logs", dependencies=[Depends(require_auth)])
    async def merged_logs(
        request: Request,
        type: Literal["all", "wake", "audit"] = Query(default="all"),
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None),
    ) -> dict[str, Any]:
        runtime = _runtime(request)
        try:
            return await runtime.console.list_logs(type, limit=limit, cursor=cursor)
        except InvalidLogCursor as exc:
            raise HTTPException(status_code=400, detail="invalid cursor") from exc

    @router.get("/api/stickers/review", dependencies=[Depends(require_auth)])
    async def sticker_review(
        request: Request,
        group_id: str | None = Query(default=None, alias="groupId"),
        limit: int = Query(default=10, ge=10, le=50),
        cursor: str | None = Query(default=None),
    ) -> dict[str, Any]:
        runtime = _runtime(request)
        try:
            return await runtime.console.list_sticker_review(
                group_id,
                limit=limit,
                cursor=cursor,
            )
        except InvalidReviewCursor as exc:
            raise HTTPException(status_code=400, detail="invalid cursor") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get("/api/stickers/{media_id}/preview", dependencies=[Depends(require_auth)])
    async def sticker_preview(request: Request, media_id: int) -> FileResponse:
        runtime = _runtime(request)
        path = await runtime.console.sticker_preview_path(media_id)
        if path is None:
            raise HTTPException(status_code=404, detail="sticker not found")
        return FileResponse(path)

    @router.post("/api/stickers/{media_id}/approve", dependencies=[Depends(require_auth)])
    async def sticker_approve(request: Request, media_id: int, body: StickerApproveBody) -> dict[str, Any]:
        runtime = _runtime(request)
        status = await runtime.console.approve_sticker(
            media_id,
            body.group_id,
            body.metadata.model_dump(),
        )
        if status != "approved":
            raise HTTPException(status_code=400, detail=status)
        return {"ok": True}

    @router.post("/api/stickers/{media_id}/reject", dependencies=[Depends(require_auth)])
    async def sticker_reject(request: Request, media_id: int, body: StickerRejectBody) -> dict[str, Any]:
        runtime = _runtime(request)
        if not await runtime.console.reject_sticker(media_id, body.group_id, body.reason):
            raise HTTPException(status_code=404, detail="sticker not found")
        return {"ok": True}

    @router.patch("/api/stickers/{media_id}/metadata", dependencies=[Depends(require_auth)])
    async def sticker_metadata(request: Request, media_id: int, body: StickerMetadataBody) -> dict[str, Any]:
        runtime = _runtime(request)
        if not await runtime.console.update_sticker_metadata(media_id, body.model_dump()):
            raise HTTPException(status_code=404, detail="sticker not found")
        return {"ok": True}

    @router.get("/api/stickers/{media_id}/context", dependencies=[Depends(require_auth)])
    async def sticker_context(request: Request, media_id: int) -> dict[str, Any]:
        runtime = _runtime(request)
        return {"items": await runtime.console.sticker_context(media_id)}

    @router.websocket("/ws/live")
    async def live(websocket: WebSocket, ticket: str | None = Query(default=None)) -> None:
        if not _ws_allowed(config, ws_tickets, websocket, ticket):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        runtime = _runtime(websocket)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)

        def subscriber(event: dict[str, Any]) -> None:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

        runtime.bus.subscribe(subscriber)
        try:
            await websocket.send_json({"type": "connected", "ts": time.time()})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25)
                except TimeoutError:
                    await websocket.send_json({"type": "ping", "ts": time.time()})
                    continue
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            runtime.bus.unsubscribe(subscriber)

    return router


def _make_auth_dependency(
    config: ConsoleConfig,
) -> Callable[[Request], Awaitable[None]]:
    async def require_auth(request: Request) -> None:
        if config.token:
            expected = f"Bearer {config.token}"
            if request.headers.get("authorization") != expected:
                raise HTTPException(status_code=401, detail="unauthorized")
            return
        host = request.client.host if request.client else ""
        if host not in LOOPBACK_HOSTS:
            raise HTTPException(status_code=401, detail="loophole blocked")

    return require_auth


def _ws_allowed(
    config: ConsoleConfig,
    tickets: dict[str, _WsTicket],
    websocket: WebSocket,
    ticket: str | None,
) -> bool:
    host = websocket.client.host if websocket.client else ""
    if config.token:
        return _consume_ws_ticket(tickets, ticket, host)
    return host in LOOPBACK_HOSTS


def _issue_ws_ticket(tickets: dict[str, _WsTicket], client_host: str) -> str:
    now = time.time()
    _prune_ws_tickets(tickets, now)
    ticket = secrets.token_urlsafe(32)
    tickets[ticket] = _WsTicket(
        expires_at=now + WS_TICKET_TTL_SECONDS,
        client_host=client_host,
    )
    return ticket


def _consume_ws_ticket(
    tickets: dict[str, _WsTicket],
    ticket: str | None,
    client_host: str,
) -> bool:
    if not ticket:
        return False
    record = tickets.pop(ticket, None)
    return not (
        record is None
        or record.expires_at < time.time()
        or record.client_host != client_host
    )


def _prune_ws_tickets(tickets: dict[str, _WsTicket], now: float) -> None:
    for ticket, record in list(tickets.items()):
        if record.expires_at < now:
            tickets.pop(ticket, None)


def _runtime(request: Request | WebSocket) -> Any:
    return request.app.state.runtime

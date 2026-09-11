from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import websockets

from app.config import QQConfig
from app.platform.gateway import EventHandler
from app.platform.qq.event_normalizer import normalize

logger = logging.getLogger("qqf.onebot")


class OneBotV11Gateway:
    def __init__(self, config: QQConfig) -> None:
        self.config = config
        self._handler: EventHandler | None = None
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._http_url = config.http_url.rstrip("/")
        self._stopping = False
        self._reconnect_task: asyncio.Task[None] | None = None

    def on_event(self, handler: EventHandler) -> None:
        self._handler = handler

    async def connect(self) -> None:
        self._stopping = False
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._run())

    async def disconnect(self) -> None:
        self._stopping = True
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning("OneBot reconnect task stopped with error", exc_info=True)
            self._reconnect_task = None

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stopping:
            try:
                await self._connect_once()
                backoff = 1.0
                await self._receive_loop()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 - reconnect regardless of the specific transport error
                if self._stopping:
                    break
                logger.warning("OneBot WS disconnected, retry in %ss", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _connect_once(self) -> None:
        headers = {"Authorization": f"Bearer {self.config.access_token}"} if self.config.access_token else {}
        self._ws = await websockets.connect(self.config.ws_url, additional_headers=headers)
        logger.info("OneBot WS connected: %s", self.config.ws_url)

    async def _receive_loop(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            try:
                payload = json.loads(raw)
                event = normalize(payload)
                if self._handler:
                    result = self._handler(event)
                    if result is not None:
                        await result
            except Exception:
                logger.debug("ignoring malformed OneBot event", exc_info=True)
                continue

    async def _post(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"content-type": "application/json"}
        if self.config.access_token:
            headers["authorization"] = f"Bearer {self.config.access_token}"
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(f"{self._http_url}/{action}", headers=headers, json=params)
        if response.status_code != 200:
            try:
                error_body = response.json()
            except json.JSONDecodeError:
                error_body = None
            detail = response.text[:300] or "<empty>"
            if isinstance(error_body, dict):
                detail = error_body.get("wording") or error_body.get("message") or detail
            raise RuntimeError(f"OneBot {action} failed: HTTP {response.status_code}: {detail}")
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"OneBot {action} returned invalid JSON: {response.text[:300] or '<empty>'}"
            ) from exc
        if not isinstance(body, dict):
            raise RuntimeError(  # noqa: TRY004 - protocol shape error, not an argument type error
                f"OneBot {action} returned unexpected JSON: {response.text[:300] or '<empty>'}"
            )
        if body.get("status") != "ok" or body.get("retcode") != 0:
            detail = body.get("wording") or body.get("message") or body.get("retcode")
            raise RuntimeError(f"OneBot {action} failed: {detail}")
        return body.get("data") or {}

    async def send_group_message(self, group_id: str, text: str, reply_to_message_id: str | None = None) -> dict[str, object]:
        params: dict[str, Any] = {"group_id": int(group_id), "message": text, "auto_escape": True}
        if reply_to_message_id:
            params["message"] = [
                {"type": "reply", "data": {"id": str(reply_to_message_id)}},
                {"type": "text", "data": {"text": text}},
            ]
            params.pop("auto_escape")
        return await self._post("send_group_msg", params)

    async def send_private_message(self, user_id: str, text: str, reply_to_message_id: str | None = None) -> dict[str, object]:
        params: dict[str, Any] = {"user_id": int(user_id), "message": text, "auto_escape": True}
        if reply_to_message_id:
            params["message"] = [
                {"type": "reply", "data": {"id": str(reply_to_message_id)}},
                {"type": "text", "data": {"text": text}},
            ]
            params.pop("auto_escape")
        return await self._post("send_private_msg", params)

    async def send_group_sticker(self, group_id: str, file_path: str, reply_to_message_id: str | None = None) -> dict[str, object]:
        message: list[dict[str, Any]] = [{"type": "image", "data": {"file": str(file_path)}}]
        if reply_to_message_id:
            message.insert(0, {"type": "reply", "data": {"id": str(reply_to_message_id)}})
        return await self._post("send_group_msg", {"group_id": int(group_id), "message": message})

    async def send_private_sticker(self, user_id: str, file_path: str, reply_to_message_id: str | None = None) -> dict[str, object]:
        message: list[dict[str, Any]] = [{"type": "image", "data": {"file": str(file_path)}}]
        if reply_to_message_id:
            message.insert(0, {"type": "reply", "data": {"id": str(reply_to_message_id)}})
        return await self._post("send_private_msg", {"user_id": int(user_id), "message": message})

    async def get_message(self, message_id: str) -> dict[str, object]:
        try:
            message_id_int = int(message_id)
        except ValueError as exc:
            raise RuntimeError(f"invalid message_id: {message_id}") from exc
        return await self._post("get_msg", {"message_id": message_id_int})

    async def get_group_history(self, group_id: str, limit: int = 20, message_seq: int | None = None) -> list[dict[str, object]]:
        params: dict[str, Any] = {"group_id": int(group_id)}
        if message_seq is not None:
            params["message_seq"] = message_seq
        data = await self._post("get_group_msg_history", params)
        messages = data.get("messages", data.get("data", []))
        return list(messages)[-limit:]

    async def get_group_members(self, group_id: str) -> list[dict[str, object]]:
        data = await self._post("get_group_member_list", {"group_id": int(group_id)})
        if isinstance(data, list):
            return list(data)
        if isinstance(data, dict):
            nested = data.get("data")
            if isinstance(nested, list):
                return list(nested)
            members = data.get("members")
            if isinstance(members, list):
                return list(members)
        return []

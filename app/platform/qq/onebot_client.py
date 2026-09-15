from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import json
import logging
import socket
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from websockets.asyncio.client import ClientConnection, connect

from app.config import QQConfig
from app.domain.types import MessageSegment
from app.platform.gateway import EventHandler
from app.platform.qq.event_normalizer import normalize

logger = logging.getLogger("qqf.onebot")


class OneBotV11Gateway:
    def __init__(self, config: QQConfig) -> None:
        self.config = config
        self._handler: EventHandler | None = None
        self._ws: ClientConnection | None = None
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
        self._ws = await connect(self.config.ws_url, additional_headers=headers)
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

    async def _post(self, action: str, params: dict[str, Any]) -> Any:
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

    async def send_group_message(
        self,
        group_id: str,
        text: str,
        reply_to_message_id: str | None = None,
        mention_user_ids: Sequence[str] = (),
    ) -> dict[str, object]:
        if reply_to_message_id or mention_user_ids:
            message: list[dict[str, Any]] = []
            if reply_to_message_id:
                message.append({"type": "reply", "data": {"id": str(reply_to_message_id)}})
            for user_id in mention_user_ids:
                message.append({"type": "at", "data": {"qq": str(user_id)}})
            message.append({"type": "text", "data": {"text": text}})
            params: dict[str, Any] = {"group_id": int(group_id), "message": message}
        else:
            params = {"group_id": int(group_id), "message": text, "auto_escape": True}
        return _dict_result(await self._post("send_group_msg", params))

    async def send_private_message(self, user_id: str, text: str, reply_to_message_id: str | None = None) -> dict[str, object]:
        params: dict[str, Any] = {"user_id": int(user_id), "message": text, "auto_escape": True}
        if reply_to_message_id:
            params["message"] = [
                {"type": "reply", "data": {"id": str(reply_to_message_id)}},
                {"type": "text", "data": {"text": text}},
            ]
            params.pop("auto_escape")
        return _dict_result(await self._post("send_private_msg", params))

    async def send_group_sticker(self, group_id: str, file_path: str, reply_to_message_id: str | None = None) -> dict[str, object]:
        message: list[dict[str, Any]] = [{"type": "image", "data": {"file": str(file_path)}}]
        if reply_to_message_id:
            message.insert(0, {"type": "reply", "data": {"id": str(reply_to_message_id)}})
        return _dict_result(await self._post("send_group_msg", {"group_id": int(group_id), "message": message}))

    async def send_private_sticker(self, user_id: str, file_path: str, reply_to_message_id: str | None = None) -> dict[str, object]:
        message: list[dict[str, Any]] = [{"type": "image", "data": {"file": str(file_path)}}]
        if reply_to_message_id:
            message.insert(0, {"type": "reply", "data": {"id": str(reply_to_message_id)}})
        return _dict_result(await self._post("send_private_msg", {"user_id": int(user_id), "message": message}))

    async def get_message(self, message_id: str) -> dict[str, object]:
        try:
            message_id_int = int(message_id)
        except ValueError as exc:
            raise RuntimeError(f"invalid message_id: {message_id}") from exc
        return _dict_result(await self._post("get_msg", {"message_id": message_id_int}))

    async def fetch_image_segment(self, segment: MessageSegment, *, max_bytes: int) -> bytes:
        if segment.kind not in {"image", "mface"}:
            raise ValueError("segment is not an image")
        data = segment.data
        file_ref = str(data.get("file_id") or data.get("file") or "").strip()
        if file_ref:
            try:
                result = await self._post("get_image", {"file": file_ref})
                payload = _decode_image_result(result)
                if payload:
                    _enforce_size(payload, max_bytes)
                    return payload
            except Exception:  # noqa: BLE001 - fall back to URL when get_image is unsupported
                logger.debug("OneBot get_image unavailable; trying URL fallback")
        url = data.get("url")
        if not isinstance(url, str) or not url.strip():
            raise RuntimeError("image segment has no downloadable source")
        return await _download_public_image(url, max_bytes=max_bytes, timeout=10.0)

    async def get_group_history(self, group_id: str, limit: int = 20, message_seq: int | None = None) -> list[dict[str, object]]:
        params: dict[str, Any] = {"group_id": int(group_id)}
        if message_seq is not None:
            params["message_seq"] = message_seq
        data = await self._post("get_group_msg_history", params)
        if isinstance(data, dict):
            data = data.get("messages", data.get("data", []))
        return _dict_list(data)[-limit:]

    async def get_group_members(self, group_id: str) -> list[dict[str, object]]:
        data = await self._post("get_group_member_list", {"group_id": int(group_id)})
        if isinstance(data, list):
            return _dict_list(data)
        if isinstance(data, dict):
            nested = data.get("data")
            if isinstance(nested, list):
                return _dict_list(nested)
            members = data.get("members")
            if isinstance(members, list):
                return _dict_list(members)
        return []


def _dict_result(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("OneBot action returned non-object data")  # noqa: TRY004 - protocol shape error
    return dict(value)


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _decode_image_result(value: object) -> bytes:
    candidate: object = value
    if isinstance(value, dict):
        candidate = value.get("base64") or value.get("data") or value.get("file") or ""
    if not isinstance(candidate, str) or not candidate:
        return b""
    text = candidate.strip()
    if text.startswith("base64://"):
        return _b64decode(text.removeprefix("base64://"))
    if text.startswith("data:"):
        _, _, payload = text.partition(",")
        return _b64decode(payload)
    path = Path(text)
    if path.is_file():
        return path.read_bytes()
    try:
        return _b64decode(text)
    except ValueError:
        return b""


def _b64decode(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64 image payload") from exc


def _enforce_size(payload: bytes, max_bytes: int) -> None:
    if len(payload) > max_bytes:
        raise RuntimeError("image exceeds max_bytes")


async def _download_public_image(url: str, *, max_bytes: int, timeout: float) -> bytes:
    current = url
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(3):
            await _validate_public_url(current)
            async with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise RuntimeError("image redirect missing location")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise RuntimeError("image exceeds max_bytes")
                    chunks.append(chunk)
                return b"".join(chunks)
    raise RuntimeError("too many image redirects")


async def _validate_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("image URL must use http or https")
    host = parsed.hostname
    if host.lower() == "localhost":
        raise ValueError("private image URL rejected")
    try:
        direct = ipaddress.ip_address(host)
    except ValueError:
        direct = None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = [direct] if direct is not None else await asyncio.to_thread(_resolve_host, host, port)
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("private image URL rejected")


def _resolve_host(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("image host resolution failed") from exc
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        raw = info[4][0]
        try:
            addresses.append(ipaddress.ip_address(raw))
        except ValueError:
            continue
    return addresses

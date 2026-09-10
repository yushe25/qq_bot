"""Offline OneBot v11 mock server for QQF end-to-end demos.

Runs an HTTP action endpoint (default :3000) and a WebSocket event endpoint
(default :3001) that behaves like a minimal OneBot implementation. It pushes
scripted messages to the configured demo group so the whole scheduler/agent
pipeline can run without a real QQ account.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from collections import deque
from typing import Any

import uvicorn
import websockets
from fastapi import FastAPI, Request
from websockets.exceptions import ConnectionClosed

from app.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger("qqf.mock")


class MockOneBot:
    def __init__(self, group_id: str, bot_id: str) -> None:
        self.group_id = group_id
        self.bot_id = bot_id
        self.connections: set[websockets.ServerConnection] = set()
        self.actions: deque[dict[str, Any]] = deque(maxlen=200)
        self.messages: dict[int, dict[str, Any]] = {}
        self._action_seq = 1000

    def record(self, action: str, params: dict[str, Any], result: dict[str, Any]) -> None:
        self._action_seq += 1
        item = {
            "action": action,
            "params": params,
            "result": result,
            "ts": time.time(),
        }
        self.actions.append(item)
        logger.info("mock action %s %s -> %s", action, params, result)

    def ok(self, data: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "retcode": 0, "data": data}

    def message_payload(self, message_id: int, member_id: str, name: str, segments: list[dict[str, Any]], raw: str) -> dict[str, Any]:
        payload = {
            "post_type": "message",
            "message_type": "group",
            "time": int(time.time()),
            "self_id": int(self.bot_id),
            "message_id": message_id,
            "group_id": int(self.group_id),
            "user_id": int(member_id),
            "sender": {"user_id": int(member_id), "nickname": name, "card": name},
            "raw_message": raw,
            "message": segments,
        }
        self.messages[int(message_id)] = payload
        return payload

    async def push_scripted_events(self, websocket: websockets.ServerConnection) -> None:
        members = [
            ("10001", "阿泽"),
            ("10002", "小满"),
            ("10003", "老胡"),
        ]
        seq = 2000

        def text_payload(text: str, member_id: str, name: str) -> dict[str, Any]:
            nonlocal seq
            seq += 1
            return self.message_payload(
                seq,
                member_id,
                name,
                [{"type": "text", "data": {"text": text}}],
                text,
            )

        script = [
            (0.8, text_payload("有人吗", "10001", "阿泽")),
            (
                2.6,
                self.message_payload(
                    9999,
                    "10002",
                    "小满",
                    [
                        {"type": "at", "data": {"qq": self.bot_id, "name": "小皮"}},
                        {"type": "text", "data": {"text": " 在吗"}},
                    ],
                    "在吗",
                ),
            ),
            (4.5, text_payload("晚上一起开黑？", "10003", "老胡")),
            (
                8.0,
                {
                    "post_type": "notice",
                    "notice_type": "notify",
                    "sub_type": "poke",
                    "time": int(time.time()),
                    "self_id": int(self.bot_id),
                    "group_id": int(self.group_id),
                    "user_id": 10003,
                    "target_id": int(self.bot_id),
                },
            ),
        ]
        try:
            for delay, payload in script:
                await asyncio.sleep(delay)
                await websocket.send(json.dumps(payload, ensure_ascii=False))

            while True:
                await asyncio.sleep(22)
                member_id, name = members[int(time.time()) % len(members)]
                text = ["今天有点困", "群里好安静", "谁有段子", "外卖到了没"][int(time.time()) % 4]
                await websocket.send(json.dumps(text_payload(text, member_id, name), ensure_ascii=False))
        except ConnectionClosed:
            return

    async def ws_handler(self, websocket: websockets.ServerConnection) -> None:
        self.connections.add(websocket)
        logger.info("mock WS client connected: %s", websocket.remote_address)
        try:
            await self.push_scripted_events(websocket)
            while True:
                try:
                    raw = await websocket.recv()
                    logger.info("mock WS received: %s", raw[:300])
                except ConnectionClosed:
                    break
        finally:
            self.connections.discard(websocket)
            logger.info("mock WS client disconnected")


def build_http_app(mock: MockOneBot) -> FastAPI:
    app = FastAPI(title="QQF Mock OneBot", version="0.1.0")

    @app.post("/send_group_msg")
    async def send_group_msg(request: Request) -> dict[str, Any]:
        body = await request.json()
        message_id = mock._action_seq + 1
        data = {"message_id": message_id, "group_id": body.get("group_id")}
        text = _message_text(body.get("message", ""))
        mock.messages[message_id] = {
            "message_id": message_id,
            "group_id": body.get("group_id"),
            "user_id": int(mock.bot_id),
            "raw_message": text,
            "message": [{"type": "text", "data": {"text": text}}],
        }
        mock.record("send_group_msg", body, data)
        return mock.ok(data)

    @app.post("/send_private_msg")
    async def send_private_msg(request: Request) -> dict[str, Any]:
        body = await request.json()
        message_id = mock._action_seq + 1
        data = {"message_id": message_id, "user_id": body.get("user_id")}
        text = _message_text(body.get("message", ""))
        mock.messages[message_id] = {
            "message_id": message_id,
            "user_id": body.get("user_id"),
            "raw_message": text,
            "message": [{"type": "text", "data": {"text": text}}],
        }
        mock.record("send_private_msg", body, data)
        return mock.ok(data)

    @app.post("/get_msg")
    async def get_msg(body: dict[str, Any]) -> dict[str, Any]:
        try:
            message_id = int(body.get("message_id"))
        except (TypeError, ValueError):
            message_id = -1
        data = mock.messages.get(message_id, {})
        mock.record("get_msg", body, data)
        return mock.ok(data)

    @app.post("/get_version_info")
    async def get_version_info() -> dict[str, Any]:
        data = {
            "app_name": "QQF Mock OneBot",
            "app_version": "0.1.0",
            "protocol_version": "v11",
        }
        mock.record("get_version_info", {}, data)
        return mock.ok(data)

    @app.post("/get_group_msg_history")
    async def get_group_msg_history(body: dict[str, Any]) -> dict[str, Any]:
        group_id = body.get("group_id")
        messages = [
            {
                "message_id": 2000000 + index,
                "group_id": group_id,
                "user_id": int(mock.bot_id),
                "time": int(time.time()) - index,
                "raw_message": "模拟历史消息",
                "message": [{"type": "text", "data": {"text": "模拟历史消息"}}],
            }
            for index in range(20)
        ]
        mock.record("get_group_msg_history", body, {"messages": len(messages)})
        return mock.ok({"messages": messages})

    @app.post("/get_group_member_list")
    async def get_group_member_list(body: dict[str, Any]) -> dict[str, Any]:
        data = [
            {"user_id": 10001, "nickname": "阿泽", "card": "阿泽", "role": "member"},
            {"user_id": 10002, "nickname": "小满", "card": "小满", "role": "owner"},
            {"user_id": 10003, "nickname": "老胡", "card": "老胡", "role": "member"},
        ]
        mock.record("get_group_member_list", body, {"members": len(data)})
        return mock.ok(data)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "actions": len(mock.actions), "ws_clients": len(mock.connections)}

    return app


def _message_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts: list[str] = []
        for item in message:
            if not isinstance(item, dict):
                continue
            segment_type = item.get("type")
            data = item.get("data") or {}
            if segment_type == "text":
                parts.append(str(data.get("text", "")))
            elif segment_type == "image":
                parts.append("[图片]")
            elif segment_type == "face":
                parts.append(f"[表情:{data.get('id', '')}]")
            elif segment_type == "record":
                parts.append("[语音]")
            elif segment_type == "file":
                filename = str(data.get("name", ""))
                parts.append(f"[文件:{filename}]" if filename else "[文件]")
            elif segment_type == "reply":
                parts.append("")
            else:
                parts.append(f"[{segment_type}]")
        return "".join(parts)
    return str(message or "")


async def run_servers(
    mock: MockOneBot,
    host: str,
    http_port: int,
    ws_port: int,
) -> None:
    app = build_http_app(mock)
    http_server = uvicorn.Server(uvicorn.Config(app, host=host, port=http_port, log_level="info"))
    ws_server = await websockets.serve(mock.ws_handler, host, ws_port)
    logger.info("mock OneBot HTTP=%s:%s WS=%s:%s group=%s bot=%s", host, http_port, host, ws_port, mock.group_id, mock.bot_id)
    try:
        await http_server.serve()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


def main() -> None:
    parser = argparse.ArgumentParser(description="QQF offline OneBot mock server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--http-port", type=int, default=3000)
    parser.add_argument("--ws-port", type=int, default=3001)
    parser.add_argument("--group", default="100000")
    parser.add_argument("--bot-id", default="31415926")
    args = parser.parse_args()

    mock = MockOneBot(args.group, args.bot_id)
    asyncio.run(run_servers(mock, args.host, args.http_port, args.ws_port))


if __name__ == "__main__":
    main()

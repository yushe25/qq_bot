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
import random
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

import uvicorn
import websockets
from fastapi import FastAPI, Request
from websockets.exceptions import ConnectionClosed

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger("qqf.mock")


class MockOneBot:
    def __init__(
        self,
        group_id: str,
        bot_id: str,
        *,
        stage6: bool = False,
        seed: int = 0,
        scenario_interval: float = 10.0,
        periodic_interval: float = 22.0,
        report_path: str | Path | None = None,
    ) -> None:
        self.group_id = group_id
        self.bot_id = bot_id
        self.connections: set[websockets.ServerConnection] = set()
        self.actions: deque[dict[str, Any]] = deque(maxlen=10_000 if stage6 else 200)
        self.messages: dict[int, dict[str, Any]] = {}
        self._action_seq = 1000
        self.stage6 = stage6
        self.seed = seed
        self.scenario_interval = max(1.0, scenario_interval)
        self.periodic_interval = max(5.0, periodic_interval)
        self.report_path = Path(report_path).resolve() if report_path else None
        self.started_at = time.time()
        self.connection_count = 0
        self.event_counts: dict[str, int] = {}
        self._stage6_seq = 2000

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

    def private_payload(self, message_id: int, user_id: str, name: str, text: str) -> dict[str, Any]:
        payload = {
            "post_type": "message",
            "message_type": "private",
            "time": int(time.time()),
            "self_id": int(self.bot_id),
            "message_id": message_id,
            "user_id": int(user_id),
            "sender": {"user_id": int(user_id), "nickname": name},
            "raw_message": text,
            "message": [{"type": "text", "data": {"text": text}}],
        }
        self.messages[int(message_id)] = payload
        return payload

    def stage6_payloads(self) -> list[tuple[str, dict[str, Any], float]]:
        self._stage6_seq = 2000

        def text(text_value: str, member_id: str, name: str) -> dict[str, Any]:
            self._stage6_seq += 1
            return self.message_payload(
                self._stage6_seq,
                member_id,
                name,
                [{"type": "text", "data": {"text": text_value}}],
                text_value,
            )

        def mention(text_value: str, member_id: str, name: str) -> dict[str, Any]:
            self._stage6_seq += 1
            return self.message_payload(
                self._stage6_seq,
                member_id,
                name,
                [
                    {"type": "at", "data": {"qq": self.bot_id, "name": "小皮"}},
                    {"type": "text", "data": {"text": f" {text_value}"}},
                ],
                text_value,
            )

        def poke_payload() -> dict[str, Any]:
            self._stage6_seq += 1
            return {
                "post_type": "notice",
                "notice_type": "notify",
                "sub_type": "poke",
                # 拍一拍的事件指纹包含 time（event_normalizer 的 poke:scene:user:time）。
                # 用 time.time() 会让重启后的重放变成"新事件"，再唤醒一次并多出一次发送，
                # S6-R07（重放不得增加 daily_send / sticker_usage）因此失败。固定时间保证重放可去重。
                "time": 1_700_000_000,
                "self_id": int(self.bot_id),
                "message_id": self._stage6_seq,
                "group_id": int(self.group_id),
                "user_id": 10003,
                "target_id": int(self.bot_id),
            }

        interval = self.scenario_interval
        return [
            ("M01_NORMAL", text("S6_NORMAL 今天有点困", "10001", "阿泽"), interval * 1),
            ("M02_MENTION", mention("S6_MEMORY 在吗", "10002", "小满"), interval * 2),
            ("M03_POKE", poke_payload(), interval * 3),
            ("M06_STICKER", text("S6_STICKER 发个表情", "10003", "老胡"), interval * 4),
            ("M09_MULTI_TOOL", text("S6_MULTI 看一下成员和历史", "10001", "阿泽"), interval * 5),
            ("M05_PRIVATE", self.private_payload(7001, "10001", "阿泽", "S6_PRIVATE 在吗"), interval * 6),
            ("M04_DIVE", text("S6_DIVE 先潜水", "10002", "小满"), interval * 7),
            ("M07_LIMIT_1", mention("S6_MEMORY 限流一", "10003", "老胡"), interval * 8),
            ("M07_LIMIT_2", mention("S6_MEMORY 限流二", "10001", "阿泽"), interval * 9),
            ("M08_DEFER", mention("S6_MEMORY 延后重试", "10002", "小满"), interval * 10),
        ]

    def record_stage6_event(self, scenario: str, payload: dict[str, Any]) -> None:
        self.event_counts[scenario] = self.event_counts.get(scenario, 0) + 1
        logger.info("stage6 event %s %s", scenario, payload.get("message_id"))

    def write_report(self) -> None:
        if self.report_path is None:
            return
        report = {
            "started_at": self.started_at,
            "finished_at": time.time(),
            "duration_seconds": max(0.0, time.time() - self.started_at),
            "seed": self.seed,
            "connection_count": self.connection_count,
            "event_counts": dict(sorted(self.event_counts.items())),
            "action_count": len(self.actions),
            "actions": [dict(item) for item in self.actions],
        }
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.report_path.with_suffix(self.report_path.suffix + ".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.report_path)

    async def push_stage6_events(self, websocket: websockets.ServerConnection) -> None:
        randomizer = random.Random(self.seed)
        try:
            for scenario, payload, delay in self.stage6_payloads():
                await asyncio.sleep(delay)
                await websocket.send(json.dumps(payload, ensure_ascii=False))
                self.record_stage6_event(scenario, payload)

            members = [
                ("10001", "阿泽"),
                ("10002", "小满"),
                ("10003", "老胡"),
            ]
            periodic_index = 0
            while True:
                await asyncio.sleep(self.periodic_interval)
                member_id, name = randomizer.choice(members)
                periodic_index += 1
                payload = self.message_payload(
                    8000 + periodic_index,
                    member_id,
                    name,
                    [{"type": "text", "data": {"text": f"S6_PERIODIC 普通消息 {periodic_index}"}}],
                    f"S6_PERIODIC 普通消息 {periodic_index}",
                )
                await websocket.send(json.dumps(payload, ensure_ascii=False))
                self.record_stage6_event("M10_PERIODIC", payload)
        except ConnectionClosed:
            return

    async def push_scripted_events(self, websocket: websockets.ServerConnection) -> None:
        if self.stage6:
            await self.push_stage6_events(websocket)
            return
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
        self.connection_count += 1
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
        raw_message_id = body.get("message_id")
        try:
            message_id = int(str(raw_message_id)) if raw_message_id is not None else -1
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
        return mock.ok({"members": data})

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
    stop_file: str | Path | None = None,
) -> None:
    app = build_http_app(mock)
    http_server = uvicorn.Server(uvicorn.Config(app, host=host, port=http_port, log_level="info"))
    handler_tasks: set[asyncio.Task[None]] = set()

    async def tracked_handler(websocket: Any) -> None:
        current = asyncio.current_task()
        if current is not None:
            handler_tasks.add(current)
        try:
            await mock.ws_handler(websocket)
        finally:
            if current is not None:
                handler_tasks.discard(current)

    ws_server = await websockets.serve(tracked_handler, host, ws_port)
    logger.info("mock OneBot HTTP=%s:%s WS=%s:%s group=%s bot=%s", host, http_port, host, ws_port, mock.group_id, mock.bot_id)
    stop_path = Path(stop_file).resolve() if stop_file else None
    stop_task: asyncio.Task[None] | None = None
    if stop_path is not None:
        async def wait_for_stop() -> None:
            while not stop_path.exists():
                await asyncio.sleep(0.25)
            # 先落盘证据：事件推送协程可能还在场景间隔的 sleep 中，等它自然结束
            # 会让 driver 等超时后强杀进程，报告文件直接丢失（S6-M01/M02/M03/R07 变假失败）。
            mock.write_report()
            http_server.should_exit = True

        stop_task = asyncio.create_task(wait_for_stop())
    try:
        await http_server.serve()
    finally:
        if stop_task is not None:
            stop_task.cancel()
            try:
                await stop_task
            except asyncio.CancelledError:
                pass
        ws_server.close()
        for task in list(handler_tasks):
            task.cancel()
        await ws_server.wait_closed()
        mock.write_report()


def main() -> None:
    parser = argparse.ArgumentParser(description="QQF offline OneBot mock server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--http-port", type=int, default=3000)
    parser.add_argument("--ws-port", type=int, default=3001)
    parser.add_argument("--group", default="100000")
    parser.add_argument("--bot-id", default="31415926")
    parser.add_argument("--stage6", action="store_true", help="send the deterministic Stage 6 event matrix")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--scenario-interval", type=float, default=10.0)
    parser.add_argument("--periodic-interval", type=float, default=22.0)
    parser.add_argument("--duration-seconds", type=float, default=0.0, help="accepted for driver compatibility")
    parser.add_argument("--stop-file")
    parser.add_argument("--report")
    args = parser.parse_args()

    mock = MockOneBot(
        args.group,
        args.bot_id,
        stage6=args.stage6,
        seed=args.seed,
        scenario_interval=args.scenario_interval,
        periodic_interval=args.periodic_interval,
        report_path=args.report,
    )
    asyncio.run(run_servers(mock, args.host, args.http_port, args.ws_port, stop_file=args.stop_file))


if __name__ == "__main__":
    main()

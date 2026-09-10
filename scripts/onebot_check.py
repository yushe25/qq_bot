from __future__ import annotations

import argparse
import asyncio
import os

import httpx
import websockets


async def check(args: argparse.Namespace) -> int:
    failures: list[str] = []

    if args.http_url:
        print(f"[1/2] HTTP 动作验证: {args.http_url}")
        headers = {"content-type": "application/json"}
        if args.token:
            headers["authorization"] = f"Bearer {args.token}"
        try:
            async with httpx.AsyncClient(timeout=args.timeout) as client:
                response = await client.post(
                    f"{args.http_url.rstrip('/')}/get_version_info",
                    json={},
                    headers=headers,
                )
                print(f"  HTTP {response.status_code}")
                if response.status_code != 200:
                    failures.append(f"HTTP action failed with status {response.status_code}")
                else:
                    body = response.json()
                    if body.get("status") == "ok" and body.get("retcode") == 0:
                        data = body.get("data") or {}
                        print(
                            "  OneBot 可达: "
                            f"{data.get('app_name', 'unknown')} "
                            f"{data.get('app_version', '')} "
                            f"protocol={data.get('protocol_version', 'v11')}"
                        )
                    else:
                        detail = body.get("wording") or body.get("message") or body
                        print(f"  OneBot 业务错误: {detail}")
                        failures.append(f"OneBot business error: {detail}")
        except Exception as exc:  # noqa: BLE001 - report any connectivity failure
            print(f"  HTTP 检查失败: {exc}")
            failures.append(f"HTTP check failed: {exc}")

    if args.ws_url:
        print(f"[2/2] WebSocket 验证: {args.ws_url}")
        headers = {}
        if args.token:
            headers["Authorization"] = f"Bearer {args.token}"
        try:
            async with websockets.connect(
                args.ws_url,
                additional_headers=headers,
                open_timeout=args.timeout,
            ) as ws:
                print("  WS 已连接")
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=args.timeout)
                    print(f"  收到事件: {raw[:160]}")
                except TimeoutError:
                    print("  WS 已连接，但超时未收到事件（可接受）")
        except Exception as exc:  # noqa: BLE001 - report any connectivity failure
            print(f"  WS 检查失败: {exc}")
            failures.append(f"WebSocket check failed: {exc}")

    if failures:
        print("检查失败")
        return 1
    print("检查通过")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Check a OneBot v11 HTTP/WS endpoint for QQF")
    parser.add_argument("--ws-url", default="ws://127.0.0.1:3001")
    parser.add_argument("--http-url", default="http://127.0.0.1:3000")
    parser.add_argument("--token", default=os.getenv("QQF_ONEBOT_TOKEN", ""))
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(check(args)))


if __name__ == "__main__":
    main()

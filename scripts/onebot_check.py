from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
import websockets

_LONG_NUMBER = re.compile(r"\d{5,}")


def _redact_text(value: str) -> str:
    return _LONG_NUMBER.sub("<id>", value)


def _redact(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    if key in {"message_id", "group_id", "user_id", "self_id", "target_id"}:
        return "<id>"
    return value


def _write_report(report_dir: Path, report: dict[str, Any]) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "onebot-check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# OneBot 连通性检查",
        "",
        f"- 时间：{report['created_at']}",
        f"- 结论：{'通过' if report['ok'] else '失败'}",
        "",
    ]
    for item in report["checks"]:
        lines.append(f"- `{item['name']}`：{item['status']}，{item['detail']}")
    (report_dir / "onebot-check.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def check(args: argparse.Namespace) -> int:
    token = os.getenv(args.token_env, "") if args.token_env else args.token
    failures: list[str] = []
    checks: list[dict[str, Any]] = []

    if args.http_url:
        print("[1/2] HTTP 动作检查")
        headers = {"content-type": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=args.timeout) as client:
                response = await client.post(
                    f"{args.http_url.rstrip('/')}/get_version_info",
                    json={},
                    headers=headers,
                )
                body = response.json()
            sanitized = _redact(body)
            if response.status_code != 200:
                detail = f"HTTP {response.status_code}"
                failures.append(detail)
                checks.append({"name": "http_reachable", "status": "失败", "detail": detail})
            elif body.get("status") != "ok" or body.get("retcode") != 0:
                detail = f"OneBot business error: {json.dumps(sanitized, ensure_ascii=False)}"
                failures.append(detail)
                checks.append({"name": "http_reachable", "status": "失败", "detail": detail})
            else:
                data = body.get("data") or {}
                detail = (
                    f"{data.get('app_name', 'unknown')} "
                    f"{data.get('app_version', '')} "
                    f"protocol={data.get('protocol_version', 'v11')}"
                )
                checks.append({"name": "http_reachable", "status": "通过", "detail": detail})
                print(f"  HTTP 可达，{detail}")
        except Exception as exc:  # noqa: BLE001 - connectivity report
            detail = f"HTTP check failed: {_redact_text(str(exc))}"
            failures.append(detail)
            checks.append({"name": "http_reachable", "status": "失败", "detail": detail})
            print(f"  {detail}")

    if args.ws_url:
        print("[2/2] WebSocket 检查")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with websockets.connect(
                args.ws_url,
                additional_headers=headers,
                open_timeout=args.timeout,
            ) as ws:
                checks.append({"name": "ws_connect", "status": "通过", "detail": "连接成功"})
                print("  WS 已连接")
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=args.timeout)
                    sanitized_event = _redact_text(str(raw)[:300])
                    checks.append({"name": "ws_event", "status": "通过", "detail": sanitized_event})
                    print("  已收到事件")
                except TimeoutError:
                    checks.append(
                        {
                            "name": "ws_event",
                            "status": "通过",
                            "detail": "超时未收到事件，但连接保持可用",
                        }
                    )
                    print("  连接存活，超时未收到事件")
        except Exception as exc:  # noqa: BLE001 - connectivity report
            detail = f"WebSocket check failed: {_redact_text(str(exc))}"
            failures.append(detail)
            checks.append({"name": "ws_connect", "status": "失败", "detail": detail})
            print(f"  {detail}")

    report = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ok": not failures,
        "failures": failures,
        "checks": checks,
    }
    if args.report_dir:
        _write_report(Path(args.report_dir).resolve(), report)
    if failures:
        print("检查失败")
        return 1
    print("检查通过")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Check a OneBot v11 HTTP/WS endpoint for QQF")
    parser.add_argument("--ws-url", default="ws://127.0.0.1:3001")
    parser.add_argument("--http-url", default="http://127.0.0.1:3000")
    parser.add_argument("--token", default="", help=argparse.SUPPRESS)
    parser.add_argument("--token-env", default="QQF_ONEBOT_TOKEN")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--report-dir")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(check(args)))


if __name__ == "__main__":
    main()

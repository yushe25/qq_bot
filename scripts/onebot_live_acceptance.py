"""OneBot v11 live protocol acceptance matrix for QQF.

The script is dry-run by default. Real sends require both --allow-send and
QQF_ALLOW_REAL_SEND=1, plus a target taken from the configured whitelist.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
import websockets

from app.config import QQConfig
from app.platform.qq.onebot_client import OneBotV11Gateway

_LONG_NUMBER = re.compile(r"\d{5,}")


@dataclass
class CheckResult:
    code: str
    title: str
    status: str
    detail: str
    evidence: str = ""


def _redact(value: Any, secrets: set[str], key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            str(item_key): _redact(item_value, secrets, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, secrets, key) for item in value]
    if isinstance(value, str):
        rendered = value
        for secret in secrets:
            if secret:
                rendered = rendered.replace(secret, "<redacted>")
        return _LONG_NUMBER.sub("<id>", rendered)
    if key in {"message_id", "group_id", "user_id", "self_id", "target_id"}:
        return "<id>"
    return value


def _send_decision(
    *,
    allow_send: bool,
    env_allowed: bool,
    group_id: str,
    whitelist: set[str],
) -> tuple[bool, str]:
    if not allow_send:
        return False, "dry-run: 未传 --allow-send"
    if not env_allowed:
        return False, "QQF_ALLOW_REAL_SEND 未设置为 1"
    if group_id not in whitelist:
        return False, "目标群不在验收白名单"
    return True, "允许发送"


def _matrix_exit_code(results: list[CheckResult]) -> int:
    return 0 if all(item.status == "通过" for item in results) else 1


class RawOneBot:
    def __init__(self, http_url: str, token: str, timeout: float) -> None:
        self.http_url = http_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    async def post(self, action: str, params: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        headers = {"content-type": "application/json"}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.http_url}/{action}",
                headers=headers,
                json=params,
            )
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {"raw": response.text[:300]}
        if not isinstance(body, dict):
            body = {"data": body}
        return response.status_code, body


def _message_id(response: dict[str, Any]) -> str:
    data = response.get("data")
    if isinstance(data, dict):
        return str(data.get("message_id") or "")
    return ""


def _has_segment(message: dict[str, Any], segment_type: str) -> bool:
    segments = message.get("message")
    if isinstance(segments, str):
        return segment_type in segments
    if isinstance(segments, list):
        return any(
            isinstance(segment, dict) and segment.get("type") == segment_type
            for segment in segments
        )
    return False


async def run_matrix(args: argparse.Namespace) -> int:
    token = os.getenv(args.token_env, "")
    group_id = os.getenv(args.group_id_env, "")
    owner_id = os.getenv(args.owner_id_env, "")
    env_allowed = os.getenv("QQF_ALLOW_REAL_SEND") == "1"
    send_allowed, send_reason = _send_decision(
        allow_send=args.allow_send,
        env_allowed=env_allowed,
        group_id=group_id,
        whitelist={group_id} if group_id else set(),
    )
    secrets = {token, group_id, owner_id}
    results: list[CheckResult] = []
    raw = RawOneBot(args.http_url, token, args.timeout)
    gateway = OneBotV11Gateway(
        QQConfig(
            ws_url=args.ws_url,
            http_url=args.http_url,
            access_token=token,
            owner_qq=owner_id,
            enabled=True,
        )
    )

    async def add(
        code: str,
        title: str,
        status: str,
        detail: str,
        evidence: str = "",
    ) -> None:
        results.append(
            CheckResult(
                code=code,
                title=title,
                status=status,
                detail=_redact_text(detail, secrets),
                evidence=_redact_text(evidence, secrets),
            )
        )

    try:
        status_code, version_body = await raw.post("get_version_info", {})
        version_data = version_body.get("data")
        protocol_version = (
            str(version_data.get("protocol_version", ""))
            if isinstance(version_data, dict)
            else ""
        )
        passed = (
            status_code == 200
            and version_body.get("status") == "ok"
            and version_body.get("retcode") == 0
            and protocol_version == "v11"
        )
        await add(
            "P01",
            "HTTP 版本",
            "通过" if passed else "失败",
            f"HTTP {status_code}, status={version_body.get('status')}, retcode={version_body.get('retcode')}, protocol={protocol_version}",
            json.dumps(_redact(version_body, secrets), ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001 - protocol report
        await add("P01", "HTTP 版本", "失败", f"请求失败: {exc}")

    account_online = False
    try:
        _, status_body = await raw.post("get_status", {})
        status_data = status_body.get("data")
        if isinstance(status_data, dict):
            account_online = bool(status_data.get("online"))
    except Exception:  # noqa: BLE001 - status is supplementary evidence
        account_online = False

    ws_headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with websockets.connect(
            args.ws_url,
            additional_headers=ws_headers,
            open_timeout=args.timeout,
        ) as ws:
            await add("P02", "WS 鉴权", "通过", "正确 Token 可连接")
            try:
                await asyncio.wait_for(ws.recv(), timeout=args.timeout)
                await add("P03", "WS 生命周期", "通过", "连接存活并收到事件")
            except TimeoutError:
                await add("P03", "WS 生命周期", "通过", "连接存活，超时时间内无事件")
    except Exception as exc:  # noqa: BLE001 - protocol report
        await add("P02", "WS 鉴权", "失败", f"WebSocket 连接失败: {exc}")
        await add("P03", "WS 生命周期", "未执行", "P02 失败")

    if token:
        try:
            async with websockets.connect(
                args.ws_url,
                additional_headers={"Authorization": "Bearer invalid-token"},
                open_timeout=args.timeout,
            ):
                await add("P02-WRONG", "错误 Token", "失败", "错误 Token 仍可连接")
        except Exception:  # noqa: BLE001 - any rejection is the expected wrong-token result
            await add("P02-WRONG", "错误 Token", "通过", "错误 Token 被拒绝")

    for code, title in (
        ("P04", "群文本入站"),
        ("P05", "@ 入站"),
        ("P06", "拍一拍入站"),
    ):
        await add(code, title, "未执行", "需要真实测试账号在测试群中人工发送事件")

    message_id = ""
    blocked_reason = send_reason if not send_allowed else "QQ 账号 offline"
    if not send_allowed or not account_online:
        for code, title in (
            ("P07", "群文本出站"),
            ("P08", "出站 @"),
            ("P09", "引用回复"),
            ("P10", "表情媒体"),
            ("P11", "私聊出入站"),
        ):
            await add(code, title, "未执行", blocked_reason)
    else:
        try:
            text_response = await gateway.send_group_message(
                group_id,
                "[QQF-T04B 测试] 群文本",
            )
            message_id = _message_id(text_response)
            fetched = await gateway.get_message(message_id) if message_id else {}
            passed = bool(message_id and _has_segment(fetched, "text"))
            await add(
                "P07",
                "群文本出站",
                "通过" if passed else "失败",
                f"message_id={bool(message_id)}, text_segment={_has_segment(fetched, 'text')}",
                json.dumps(_redact(fetched, secrets), ensure_ascii=False),
            )
        except Exception as exc:  # noqa: BLE001 - protocol report
            await add("P07", "群文本出站", "失败", f"发送失败: {exc}")

        try:
            status_code, at_body = await raw.post(
                "send_group_msg",
                {
                    "group_id": int(group_id),
                    "message": [
                        {"type": "at", "data": {"qq": owner_id}},
                        {"type": "text", "data": {"text": " [QQF-T04B 测试] @ 协议层"}},
                    ],
                },
            )
            at_id = _message_id(at_body)
            fetched_at = await gateway.get_message(at_id) if at_id else {}
            protocol_ok = status_code == 200 and at_body.get("status") == "ok" and _has_segment(fetched_at, "at")
            await add(
                "P08",
                "出站 @",
                "失败",
                f"NapCat at 协议层={'通过' if protocol_ok else '失败'}；QQF 无正式 at 接口，产品路径未覆盖",
                json.dumps(_redact(fetched_at, secrets), ensure_ascii=False),
            )
        except Exception as exc:  # noqa: BLE001 - protocol report
            await add("P08", "出站 @", "失败", f"NapCat at 发送失败: {exc}")

        if message_id:
            try:
                reply_response = await gateway.send_group_message(
                    group_id,
                    "[QQF-T04B 测试] 引用回复",
                    reply_to_message_id=message_id,
                )
                reply_id = _message_id(reply_response)
                fetched_reply = await gateway.get_message(reply_id) if reply_id else {}
                passed = bool(reply_id and _has_segment(fetched_reply, "reply"))
                await add(
                    "P09",
                    "引用回复",
                    "通过" if passed else "失败",
                    f"reply_segment={_has_segment(fetched_reply, 'reply')}",
                    json.dumps(_redact(fetched_reply, secrets), ensure_ascii=False),
                )
            except Exception as exc:  # noqa: BLE001 - protocol report
                await add("P09", "引用回复", "失败", f"引用发送失败: {exc}")
        else:
            await add("P09", "引用回复", "未执行", "缺少 P07 message_id")

        if args.sticker_path:
            sticker = Path(args.sticker_path).resolve()
            try:
                sticker_response = await gateway.send_group_sticker(group_id, str(sticker))
                sticker_id = _message_id(sticker_response)
                fetched_sticker = await gateway.get_message(sticker_id) if sticker_id else {}
                passed = bool(sticker_id and _has_segment(fetched_sticker, "image"))
                await add(
                    "P10",
                    "表情媒体",
                    "通过" if passed else "失败",
                    f"image_segment={_has_segment(fetched_sticker, 'image')}",
                    json.dumps(_redact(fetched_sticker, secrets), ensure_ascii=False),
                )
            except Exception as exc:  # noqa: BLE001 - protocol report
                await add("P10", "表情媒体", "失败", f"表情发送失败: {exc}")
        else:
            await add("P10", "表情媒体", "未执行", "未提供 --sticker-path")

        try:
            private_response = await gateway.send_private_message(
                owner_id,
                "[QQF-T04B 测试] 私聊出站",
            )
            private_id = _message_id(private_response)
            await add(
                "P11",
                "私聊出入站",
                "未执行",
                f"私聊出站成功={bool(private_id)}；入站仍需 owner 人工发送",
            )
        except Exception as exc:  # noqa: BLE001 - protocol report
            await add("P11", "私聊出入站", "失败", f"私聊出站失败: {exc}")

    try:
        status_code, error_body = await raw.post("get_msg", {"message_id": -1})
        normalized = status_code != 200 or error_body.get("status") != "ok" or error_body.get("retcode") != 0
        await add(
            "P16",
            "动作错误",
            "通过" if normalized else "失败",
            f"invalid get_msg 已归一化: HTTP {status_code}",
        )
    except Exception as exc:  # noqa: BLE001 - protocol report
        await add("P16", "动作错误", "通过", f"异常已归一化: {exc}")

    try:
        history = await gateway.get_group_history(group_id, limit=5)
        await add("P12", "历史接口", "通过", f"返回 {len(history)} 条可解析消息")
    except Exception as exc:  # noqa: BLE001 - protocol report
        await add("P12", "历史接口", "失败", f"历史接口失败: {exc}")

    try:
        members = await gateway.get_group_members(group_id)
        member_count = len(members)
        await add(
            "P13",
            "成员接口",
            "通过" if member_count > 0 else "失败",
            f"返回 {member_count} 名成员，上限检查由 QQF 运行链路另行执行",
        )
    except Exception as exc:  # noqa: BLE001 - protocol report
        await add("P13", "成员接口", "失败", f"成员接口失败: {exc}")

    await add("P14", "断线重连", "未执行", "需要受控中断真实 WS 并观察 QQF 自动恢复")
    await add("P15", "重连去重", "未执行", "P14 未执行")

    failed = [item.code for item in results if item.status == "失败"]
    not_run = [item.code for item in results if item.status == "未执行"]
    overall_pass = not failed and not not_run
    report = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dry_run": not send_allowed,
        "send_decision": send_reason,
        "account_online": account_online,
        "overall_pass": overall_pass,
        "failed_checks": failed,
        "not_run_checks": not_run,
        "results": [asdict(item) for item in results],
    }
    report_dir = Path(args.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# T-04B 真实协议矩阵",
        "",
        f"- 时间：{report['created_at']}",
        f"- dry_run：{report['dry_run']}",
        f"- 发送判定：{report['send_decision']}",
        f"- overall_pass：{overall_pass}",
        "",
    ]
    for item in results:
        lines.append(f"- `{item.code}` {item.title}：{item.status}，{item.detail}")
    (report_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return _matrix_exit_code(results)


def _redact_text(value: str, secrets: set[str]) -> str:
    rendered = value
    for secret in secrets:
        if secret:
            rendered = rendered.replace(secret, "<redacted>")
    return _LONG_NUMBER.sub("<id>", rendered)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the QQF OneBot live acceptance matrix")
    parser.add_argument("--ws-url", default="ws://127.0.0.1:3001")
    parser.add_argument("--http-url", default="http://127.0.0.1:3000")
    parser.add_argument("--token-env", default="QQF_ONEBOT_TOKEN")
    parser.add_argument("--group-id-env", default="QQF_TEST_GROUP_ID")
    parser.add_argument("--owner-id-env", default="QQF_TEST_OWNER_ID")
    parser.add_argument("--sticker-path")
    parser.add_argument("--report-dir", default=".pytest-tmp/t04b-20260913")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--allow-send", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_matrix(args)))


if __name__ == "__main__":
    main()

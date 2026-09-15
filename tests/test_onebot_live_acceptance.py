from __future__ import annotations

from scripts.onebot_live_acceptance import (
    CheckResult,
    _is_loopback_endpoint,
    _matrix_exit_code,
    _message_id,
    _redact,
    _redact_text,
    _send_decision,
)


def test_live_acceptance_requires_explicit_send_authorization() -> None:
    assert _send_decision(
        allow_send=False,
        env_allowed=True,
        group_id="100000",
        whitelist={"100000"},
    ) == (False, "dry-run: 未传 --allow-send")
    assert _send_decision(
        allow_send=True,
        env_allowed=False,
        group_id="100000",
        whitelist={"100000"},
    ) == (False, "QQF_ALLOW_REAL_SEND 未设置为 1")
    assert _send_decision(
        allow_send=True,
        env_allowed=True,
        group_id="100000",
        whitelist={"999999"},
    ) == (False, "目标群不在验收白名单")
    assert _send_decision(
        allow_send=True,
        env_allowed=True,
        group_id="100000",
        whitelist={"100000"},
    ) == (True, "允许发送")


def test_live_acceptance_redacts_ids_and_tokens() -> None:
    payload = {
        "group_id": 987654321,
        "user_id": "10001",
        "message_id": 123456789,
        "text": "token secret 987654321",
    }

    redacted = _redact(payload, {"secret", "987654321"})

    assert redacted["group_id"] == "<id>"
    assert redacted["user_id"] == "<id>"
    assert redacted["message_id"] == "<id>"
    assert "secret" not in redacted["text"]
    assert "987654321" not in redacted["text"]
    assert "<redacted>" in redacted["text"]
    assert _redact_text("message 123456789", set()) == "message <id>"


def test_live_acceptance_exit_code_fails_unless_all_checks_pass() -> None:
    passed = [CheckResult("P01", "HTTP", "通过", "ok")]
    failed = [CheckResult("P01", "HTTP", "通过", "ok"), CheckResult("P02", "WS", "失败", "bad")]
    not_run = [CheckResult("P01", "HTTP", "通过", "ok"), CheckResult("P04", "入站", "未执行", "manual")]
    controlled = [CheckResult("P02-WRONG", "错误 Token", "受控例外", "loopback")]

    assert _matrix_exit_code(passed) == 0
    assert _matrix_exit_code(controlled) == 0
    assert _matrix_exit_code(failed) == 1
    assert _matrix_exit_code(not_run) == 1


def test_live_acceptance_detects_loopback_endpoints() -> None:
    assert _is_loopback_endpoint("ws://127.0.0.1:3001")
    assert _is_loopback_endpoint("http://localhost:3000")
    assert _is_loopback_endpoint("http://[::1]:3000")
    assert not _is_loopback_endpoint("wss://napcat.example/ws")


def test_live_acceptance_reads_wrapped_and_unwrapped_message_ids() -> None:
    assert _message_id({"data": {"message_id": 123}}) == "123"
    assert _message_id({"message_id": 456}) == "456"
    assert _message_id({}) == ""

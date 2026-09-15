from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.config import QQConfig
from app.platform.qq.onebot_client import OneBotV11Gateway


def _patch_http_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    original_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return original_client(transport=transport)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def test_post_reports_http_error_with_non_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="<html>gateway error</html>"))
    _patch_http_client(monkeypatch, transport)
    gateway = OneBotV11Gateway(QQConfig())

    with pytest.raises(RuntimeError, match="HTTP 500"):
        await gateway.send_group_message("100000", "hello")


async def test_post_reports_http_error_with_json_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(403, json={"status": "failed", "retcode": 100, "wording": "forbidden"})
    )
    _patch_http_client(monkeypatch, transport)
    gateway = OneBotV11Gateway(QQConfig())

    with pytest.raises(RuntimeError, match="forbidden"):
        await gateway.send_group_message("100000", "hello")


async def test_post_reports_invalid_json_on_success_status(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>not json</html>"))
    _patch_http_client(monkeypatch, transport)
    gateway = OneBotV11Gateway(QQConfig())

    with pytest.raises(RuntimeError, match="invalid JSON"):
        await gateway.send_group_message("100000", "hello")


async def test_post_reports_unexpected_json_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    _patch_http_client(monkeypatch, transport)
    gateway = OneBotV11Gateway(QQConfig())

    with pytest.raises(RuntimeError, match="unexpected JSON"):
        await gateway.send_group_message("100000", "hello")


async def test_post_reports_onebot_business_error(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"status": "failed", "retcode": 100, "wording": "bad params"})
    )
    _patch_http_client(monkeypatch, transport)
    gateway = OneBotV11Gateway(QQConfig())

    with pytest.raises(RuntimeError, match="bad params"):
        await gateway.send_group_message("100000", "hello")


async def test_post_returns_data_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": 42}})
    )
    _patch_http_client(monkeypatch, transport)
    gateway = OneBotV11Gateway(QQConfig())

    result = await gateway.send_group_message("100000", "hello")
    assert result["message_id"] == 42


async def test_send_group_message_posts_reply_at_text_without_auto_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": 42}})

    _patch_http_client(monkeypatch, httpx.MockTransport(handler))
    gateway = OneBotV11Gateway(QQConfig())

    await gateway.send_group_message(
        "100000",
        "文件在这里",
        reply_to_message_id="88",
        mention_user_ids=["10001", "10002"],
    )

    assert captured["body"] == {
        "group_id": 100000,
        "message": [
            {"type": "reply", "data": {"id": "88"}},
            {"type": "at", "data": {"qq": "10001"}},
            {"type": "at", "data": {"qq": "10002"}},
            {"type": "text", "data": {"text": "文件在这里"}},
        ],
    }


async def test_send_plain_group_message_keeps_auto_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": 43}})

    _patch_http_client(monkeypatch, httpx.MockTransport(handler))
    gateway = OneBotV11Gateway(QQConfig())

    await gateway.send_group_message("100000", "hello")

    assert captured["body"] == {"group_id": 100000, "message": "hello", "auto_escape": True}


async def test_send_group_sticker_posts_image_segment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    file_path = tmp_path / "sticker.gif"
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": 77}})

    _patch_http_client(monkeypatch, httpx.MockTransport(handler))
    gateway = OneBotV11Gateway(QQConfig())

    result = await gateway.send_group_sticker("100000", str(file_path))

    body = json.loads(captured["body"])
    assert body["group_id"] == 100000
    assert body["message"] == [{"type": "image", "data": {"file": str(file_path)}}]
    assert result["message_id"] == 77


async def test_send_private_sticker_posts_image_segment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    file_path = tmp_path / "sticker.gif"
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": 78}})

    _patch_http_client(monkeypatch, httpx.MockTransport(handler))
    gateway = OneBotV11Gateway(QQConfig())

    result = await gateway.send_private_sticker("10001", str(file_path))

    body = json.loads(captured["body"])
    assert body["user_id"] == 10001
    assert body["message"] == [{"type": "image", "data": {"file": str(file_path)}}]
    assert result["message_id"] == 78


async def test_get_message_returns_data(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "status": "ok",
                "retcode": 0,
                "data": {"message_id": 123, "group_id": 100000, "user_id": 10001},
            },
        )
    )
    _patch_http_client(monkeypatch, transport)
    gateway = OneBotV11Gateway(QQConfig())

    result = await gateway.get_message("123")

    assert result["message_id"] == 123
    assert result["group_id"] == 100000


async def test_get_message_rejects_non_numeric_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http_client(
        monkeypatch,
        httpx.MockTransport(lambda request: httpx.Response(500, text="should not be called")),
    )
    gateway = OneBotV11Gateway(QQConfig())

    with pytest.raises(RuntimeError, match="invalid message_id"):
        await gateway.get_message("not-a-number")


async def test_get_message_reports_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(502, text="bad gateway"))
    _patch_http_client(monkeypatch, transport)
    gateway = OneBotV11Gateway(QQConfig())

    with pytest.raises(RuntimeError, match="HTTP 502"):
        await gateway.get_message("123")


async def test_get_group_members_accepts_direct_list_data(monkeypatch: pytest.MonkeyPatch) -> None:
    members = [{"user_id": 10001, "nickname": "alice"}]
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"status": "ok", "retcode": 0, "data": members})
    )
    _patch_http_client(monkeypatch, transport)
    gateway = OneBotV11Gateway(QQConfig())

    result = await gateway.get_group_members("100000")

    assert result == members


async def test_get_group_members_accepts_members_field(monkeypatch: pytest.MonkeyPatch) -> None:
    members = [{"user_id": 10002, "nickname": "bob"}]
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"members": members}})
    )
    _patch_http_client(monkeypatch, transport)
    gateway = OneBotV11Gateway(QQConfig())

    result = await gateway.get_group_members("100000")

    assert result == members

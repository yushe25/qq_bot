from __future__ import annotations

import pytest

from app.platform.qq.event_normalizer import normalize


def test_normalize_group_message_with_segments() -> None:
    raw = {
        "post_type": "message",
        "message_type": "group",
        "time": 1767171600,
        "self_id": 31415926,
        "message_id": 42,
        "group_id": 100000,
        "user_id": 10001,
        "sender": {"user_id": 10001, "nickname": "阿泽", "card": "阿泽"},
        "raw_message": "在吗",
        "message": [
            {"type": "at", "data": {"qq": "31415926", "name": "小皮"}},
            {"type": "text", "data": {"text": " 在吗"}},
        ],
    }

    event = normalize(raw)

    assert event.session_id == "group:100000"
    assert event.sender_id == "10001"
    assert event.bot_id == "31415926"
    assert len(event.segments) == 2
    assert event.segments[0].kind == "at"
    assert "在吗" in event.text


def test_normalize_poke() -> None:
    raw = {
        "post_type": "notice",
        "notice_type": "notify",
        "sub_type": "poke",
        "time": 1767171600,
        "self_id": 31415926,
        "group_id": 100000,
        "user_id": 10003,
        "target_id": 31415926,
    }

    event = normalize(raw)

    assert event.kind == "poke"
    assert event.session_id == "group:100000"
    assert event.text == "[拍一拍]"


def test_normalize_unsupported_event() -> None:
    with pytest.raises(ValueError):
        normalize({"post_type": "meta_event", "meta_event_type": "heartbeat"})


def test_normalize_media_segments_are_summarized() -> None:
    raw = {
        "post_type": "message",
        "message_type": "group",
        "time": 1767171600,
        "self_id": 31415926,
        "message_id": 43,
        "group_id": 100000,
        "user_id": 10001,
        "sender": {"user_id": 10001, "nickname": "阿泽"},
        "message": [
            {"type": "image", "data": {"file": "x.jpg"}},
            {"type": "face", "data": {"id": 178}},
            {"type": "record", "data": {"file": "x.silk"}},
            {"type": "file", "data": {"name": "报告.pdf"}},
            {"type": "forward", "data": {}},
        ],
    }

    event = normalize(raw)

    assert event.text == "[图片][表情:178:抠鼻][语音][文件:报告.pdf][分享卡片]"


def test_normalize_raw_message_with_media_still_uses_media_summary() -> None:
    raw = {
        "post_type": "message",
        "message_type": "group",
        "time": 1767171600,
        "self_id": 31415926,
        "message_id": 44,
        "group_id": 100000,
        "user_id": 10001,
        "sender": {"user_id": 10001, "nickname": "阿泽"},
        "raw_message": "[CQ:image,file=x.jpg]",
        "message": [{"type": "image", "data": {"file": "x.jpg"}}],
    }

    event = normalize(raw)

    assert event.text == "[图片]"

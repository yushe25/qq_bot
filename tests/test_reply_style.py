from __future__ import annotations

from app.agent.reply_style import normalize_reply_segments


def test_comma_splits_into_separate_messages() -> None:
    assert normalize_reply_segments("在吗，帮我看看") == ["在吗", "帮我看看"]


def test_non_emotional_punctuation_is_removed_or_split() -> None:
    assert normalize_reply_segments("今天好累。我们改天聊！") == ["今天好累", "我们改天聊！"]
    assert normalize_reply_segments("好的：没问题；收到了") == ["好的", "没问题", "收到了"]


def test_emotional_punctuation_and_ellipsis_are_kept() -> None:
    assert normalize_reply_segments("真的假的？") == ["真的假的？"]
    assert normalize_reply_segments("无语了...") == ["无语了..."]
    assert normalize_reply_segments("算了吧……") == ["算了吧..."]


def test_decimal_point_is_not_treated_as_sentence_end() -> None:
    assert normalize_reply_segments("价格是3.14元") == ["价格是3.14元"]


def test_empty_segments_are_dropped() -> None:
    assert normalize_reply_segments("，，，") == []

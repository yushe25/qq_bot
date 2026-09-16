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


def test_chinese_pause_space_splits_into_messages() -> None:
    assert normalize_reply_segments("这串代码是啥 没看懂") == ["这串代码是啥", "没看懂"]
    assert normalize_reply_segments("这串代码是啥\u3000没看懂") == ["这串代码是啥", "没看懂"]
    assert normalize_reply_segments("这串代码是啥   没看懂") == ["这串代码是啥", "没看懂"]


def test_english_spaces_are_not_pause_boundaries() -> None:
    assert normalize_reply_segments("hello world") == ["hello world"]


def test_urls_paths_versions_and_inline_code_are_not_split() -> None:
    assert normalize_reply_segments("版本是 3.14 元") == ["版本是 3.14 元"]
    assert normalize_reply_segments("看下 https://example.com 这个") == ["看下 https://example.com 这个"]
    assert normalize_reply_segments("打开 C:\\work\\a.py 文件") == ["打开 C:\\work\\a.py 文件"]
    assert normalize_reply_segments("运行 `pytest -q` 命令") == ["运行 `pytest -q` 命令"]


def test_literal_at_is_preserved() -> None:
    assert normalize_reply_segments("咋了老@我 有啥事") == ["咋了老@我 有啥事"]


def test_pause_space_respects_segment_limit_and_keeps_tail() -> None:
    assert normalize_reply_segments("这串代码是啥 没看懂 再看一下", max_segments=2) == [
        "这串代码是啥",
        "没看懂再看一下",
    ]


def test_empty_segments_are_dropped() -> None:
    assert normalize_reply_segments("，，，") == []
    assert normalize_reply_segments("！！！ ...") == []

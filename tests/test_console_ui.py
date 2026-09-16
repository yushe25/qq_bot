from __future__ import annotations

from pathlib import Path


def test_console_uses_wake_logs_and_structured_event_feed() -> None:
    page = (Path(__file__).resolve().parents[1] / "app" / "console" / "public" / "console.html").read_text(
        encoding="utf-8"
    )

    assert "id=\"wake-logs\"" in page
    assert "/api/wake-logs?" in page
    assert "async function loadWakeLogs()" in page
    assert "eventLabels" in page
    assert "JSON.stringify(event)" not in page
    assert "id=\"review-group\"" not in page
    assert "async function saveStickerMetadata" in page
    assert "metadataFromCard" in page
    assert "avoidScenes: splitMetadataList" in page
    assert "body: JSON.stringify({ groupId: item.groupId, metadata })" in page
    assert "let stickerLoadedCount = 10;" in page
    assert "async function reloadStickerReviewAfterMutation()" in page
    assert "window.scrollTo({ top: scrollY })" in page

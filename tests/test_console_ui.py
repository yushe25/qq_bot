from __future__ import annotations

from pathlib import Path


def test_console_uses_merged_logs_and_structured_event_feed() -> None:
    page = (Path(__file__).resolve().parents[1] / "app" / "console" / "public" / "console.html").read_text(
        encoding="utf-8"
    )

    assert "id=\"log-list\"" in page
    assert "/api/logs?" in page
    assert "async function loadLogs(reset = false)" in page
    assert "data-log-type=\"all\"" in page
    assert "logRequestId" in page
    assert "requestId !== logRequestId" in page
    assert "id=\"wake-logs\"" not in page
    assert "id=\"audit\"" not in page
    assert "/api/wake-logs?" not in page
    assert "/api/audit?" not in page
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
    assert "let stickerReviewCursor = null;" in page
    assert "new IntersectionObserver" in page
    assert "async function loadNextStickerReviewPage()" in page
    assert "button.textContent = '加载更多'" in page
    assert "tail.textContent = '已全部加载'" in page
    assert "async function fetchAuthorizedBlob" in page
    assert "URL.createObjectURL(blob)" in page
    assert "URL.revokeObjectURL(objectUrl)" in page
    assert "image.src = item.previewUrl" not in page

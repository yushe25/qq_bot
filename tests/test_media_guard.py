from __future__ import annotations

from app.safety.media_guard import MediaGuard


def _jpeg_with_exif() -> bytes:
    app1 = b"\xff\xe1\x00\x08Exif\x00\x00"
    sof0 = b"\xff\xc0\x00\x11\x08\x01\xe0\x01\xf8\x03"
    return b"\xff\xd8" + app1 + sof0 + b"\xff\xd9"


def test_media_guard_reads_jpeg_dimensions_with_exif(tmp_path) -> None:
    path = tmp_path / "photo.jpg"
    path.write_bytes(_jpeg_with_exif())

    ok, reason = MediaGuard(max_bytes=1024, max_pixels=1_000_000).check_file(path)

    assert ok is True
    assert reason == "ok"

from __future__ import annotations

import struct
from pathlib import Path


class MediaGuard:
    def __init__(self, max_bytes: int, max_pixels: int) -> None:
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels

    def check_metadata(self, size_bytes: int | None = None, width: int | None = None, height: int | None = None) -> tuple[bool, str]:
        if size_bytes is not None and size_bytes > self.max_bytes:
            return False, f"文件过大: {size_bytes} bytes"
        if width is not None and height is not None and width * height > self.max_pixels:
            return False, f"像素超限: {width}x{height}"
        return True, "ok"

    def check_file(self, path: str | Path) -> tuple[bool, str]:
        file_path = Path(path)
        try:
            size = file_path.stat().st_size
        except OSError as exc:
            return False, f"文件不可读: {exc}"
        if size > self.max_bytes:
            return False, f"文件过大: {size} bytes"
        try:
            with file_path.open("rb") as f:
                header = f.read(4096)
        except OSError as exc:
            return False, f"文件不可读: {exc}"
        if not header:
            return False, "空文件"
        width, height = _image_dimensions(header)
        if width is None or height is None:
            return False, "不支持的图片格式"
        return self.check_metadata(size, width, height)


def _image_dimensions(header: bytes) -> tuple[int | None, int | None]:
    if header.startswith((b"GIF87a", b"GIF89a")) and len(header) >= 10:
        return _unpack_le(header[6:8]), _unpack_le(header[8:10])
    if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
        return _unpack_be(header[16:20]), _unpack_be(header[20:24])
    if header.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(header)
    return None, None


def _unpack_le(raw: bytes) -> int | None:
    try:
        return struct.unpack("<H", raw)[0]
    except struct.error:
        return None


def _unpack_be(raw: bytes) -> int | None:
    try:
        if len(raw) == 2:
            return struct.unpack(">H", raw)[0]
        if len(raw) == 4:
            return struct.unpack(">I", raw)[0]
        return None
    except struct.error:
        return None


def _jpeg_dimensions(header: bytes) -> tuple[int | None, int | None]:
    # JPEG dimensions live in SOF markers; scan segment headers until a SOF appears.
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    index = 2
    while index + 9 < len(header):
        if header[index] != 0xFF:
            index += 1
            continue
        marker = header[index + 1]
        if marker in sof_markers:
            height = _unpack_be(header[index + 5 : index + 7])
            width = _unpack_be(header[index + 7 : index + 9])
            return width, height
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if marker == 0xD9:
            return None, None
        length = int.from_bytes(header[index + 2 : index + 4], "big")
        if length < 2:
            return None, None
        index += 2 + length
    return None, None

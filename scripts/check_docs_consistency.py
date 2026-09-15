from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import AppConfig

README = ROOT / "README.md"
EXAMPLE = ROOT / "config.example.json"
REPORT = ROOT / "docs" / "PROJECT_REPORT.md"

_SOURCE_LINE_REF = re.compile(r"`[^`]+\.py:\d+(?:-\d+)?`")


def check_docs_consistency() -> list[str]:
    errors: list[str] = []
    try:
        json.loads(EXAMPLE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"config.example.json is invalid: {exc}"]
    defaults = AppConfig().model_dump(mode="json")

    readme = README.read_text(encoding="utf-8")
    documented: set[str] = set()
    for line in readme.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not cells[0].startswith("`"):
            continue
        keys = re.findall(r"`([^`]+)`", cells[0])
        if not keys:
            continue
        documented.update(keys)
        if len(keys) != 1:
            continue
        key = keys[0]
        value = _resolve(defaults, _snake_path(key))
        if value is _MISSING:
            continue
        expected = _render_default(value)
        rendered_default = cells[2].strip().strip("`")
        if expected is not None and expected != rendered_default.strip():
            errors.append(
                f"README default mismatch for {key}: documented={rendered_default.strip()!r} config={expected!r}"
            )

    required = {
        "qq.allowRemote",
        "sticker.autoTag.enabled",
        "sticker.autoTag.provider",
        "sticker.autoTag.fallbacks",
        "sticker.autoTag.providers",
    }
    missing = sorted(required - documented)
    if missing:
        errors.append("README config table missing keys: " + ", ".join(missing))

    report = REPORT.read_text(encoding="utf-8") if REPORT.exists() else ""
    for match in _SOURCE_LINE_REF.finditer(report):
        errors.append(f"PROJECT_REPORT contains fragile source line reference: {match.group(0)}")
    return errors


_MISSING = object()


def _resolve(data: Any, dotted_path: str) -> Any:
    value = data
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _snake_path(path: str) -> str:
    return ".".join(re.sub(r"(?<!^)(?=[A-Z])", "_", part).lower() for part in path.split("."))


def _render_default(value: Any) -> str | None:
    if value == "":
        return "空"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "[]" if not value else None
    if isinstance(value, dict):
        return "{}" if not value else None
    return None


def main() -> int:
    errors = check_docs_consistency()
    if errors:
        for error in errors:
            print(error)
        return 1
    print("docs consistency ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

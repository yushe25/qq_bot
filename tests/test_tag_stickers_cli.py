from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

import scripts.tag_stickers as cli
from app.agent.sticker_tagger import StickerTagResult


def _write_config(tmp_path: Path, library_dir: Path) -> Path:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "storage": {
                    "dir": str(tmp_path / "state"),
                    "databasePath": str(tmp_path / "state" / "qqf.db"),
                },
                "stickerDir": str(library_dir),
                "sticker": {
                    "enabled": True,
                    "allowedIds": ["pilot-01", "pilot-02"],
                    "autoTag": {
                        "enabled": True,
                        "provider": "vision-main",
                        "providers": {
                            "vision-main": {
                                "baseUrl": "https://vision.example/v1",
                                "apiKeyEnv": "QQF_VISION_API_KEY",
                                "model": "vision-model",
                            }
                        },
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return config_path


def _install_fake_tagger(
    monkeypatch: Any,
    results: list[StickerTagResult],
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    class _FakeTagger:
        def __init__(self, library_dir: Path, config: Any, provider: Any, *, dry_run: bool = False) -> None:
            self.library_dir = library_dir
            self.config = config
            self.provider = provider
            self.dry_run = dry_run
            self.last_results = results

        async def tag_many(self, sticker_ids: list[str], *, force: bool = False) -> list[Any]:
            calls.append(
                {
                    "ids": list(sticker_ids),
                    "force": force,
                    "dry_run": self.dry_run,
                }
            )
            return [result.entry for result in self.last_results if result.entry is not None]

    monkeypatch.setattr(cli, "build_vision_provider", lambda config: object())
    monkeypatch.setattr(cli, "StickerTagger", _FakeTagger)
    return calls


def test_cli_requires_exactly_one_selector(capsys: Any) -> None:
    assert cli.main([]) == 2
    assert "只指定 --id 或 --all" in capsys.readouterr().err


def test_cli_supports_multiple_ids_and_dry_run(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    library_dir = tmp_path / "stickers"
    config_path = _write_config(tmp_path, library_dir)
    calls = _install_fake_tagger(
        monkeypatch,
        [
            StickerTagResult(sticker_id="pilot-01", status="updated"),
            StickerTagResult(sticker_id="pilot-02", status="updated"),
        ],
    )

    code = cli.main(
        [
            "--config",
            str(config_path),
            "--id",
            "pilot-01",
            "--id",
            "pilot-02",
            "--dry-run",
        ]
    )

    assert code == 0
    assert calls == [{"ids": ["pilot-01", "pilot-02"], "force": False, "dry_run": True}]
    assert "dry-run 完成，未修改 manifest" in capsys.readouterr().out


def test_cli_all_filters_allowed_and_approved(tmp_path: Path, monkeypatch: Any) -> None:
    library_dir = tmp_path / "stickers"
    library_dir.mkdir()
    (library_dir / "manifest.yaml").write_text(
        yaml.safe_dump(
            [
                {"id": "pilot-01", "name": "one", "file": "images/one.gif", "approved": True},
                {"id": "pilot-02", "name": "two", "file": "images/two.gif", "approved": False},
                {"id": "other", "name": "other", "file": "images/other.gif", "approved": True},
            ],
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    config_path = _write_config(tmp_path, library_dir)
    calls = _install_fake_tagger(
        monkeypatch,
        [StickerTagResult(sticker_id="pilot-01", status="updated")],
    )

    code = cli.main(["--config", str(config_path), "--all"])

    assert code == 0
    assert calls[0]["ids"] == ["pilot-01"]


def test_cli_failure_returns_nonzero_without_leaking_reason(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    library_dir = tmp_path / "stickers"
    config_path = _write_config(tmp_path, library_dir)
    _install_fake_tagger(
        monkeypatch,
        [
            StickerTagResult(
                sticker_id="pilot-01",
                status="failed",
                reason=f"{tmp_path}\\secret-key-and-path",
            )
        ],
    )

    code = cli.main(["--config", str(config_path), "--id", "pilot-01"])

    output = capsys.readouterr()
    assert code == 1
    assert "失败 id: pilot-01" in output.err
    assert "secret-key-and-path" not in output.err
    assert str(tmp_path) not in output.err


def test_cli_missing_manifest_does_not_print_absolute_path(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    library_dir = tmp_path / "missing"
    config_path = _write_config(tmp_path, library_dir)
    _install_fake_tagger(monkeypatch, [])

    code = cli.main(["--config", str(config_path), "--all"])

    output = capsys.readouterr()
    assert code == 1
    assert str(tmp_path) not in output.err

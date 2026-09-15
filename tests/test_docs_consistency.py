from __future__ import annotations

from scripts.check_docs_consistency import check_docs_consistency


def test_docs_consistency() -> None:
    assert check_docs_consistency() == []

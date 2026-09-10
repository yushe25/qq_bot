from __future__ import annotations

import pytest

from app.safety.instance_lock import InstanceLock, InstanceLockError


def test_second_lock_on_same_path_fails_until_released(tmp_path) -> None:
    path = tmp_path / "qqf.lock"
    first = InstanceLock(path)
    second = InstanceLock(path)

    first.acquire()
    try:
        with pytest.raises(InstanceLockError):
            second.acquire()
    finally:
        first.release()

    retry = InstanceLock(path)
    retry.acquire()
    retry.release()

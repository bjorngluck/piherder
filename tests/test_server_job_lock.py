"""Unit tests for per-server job mutex (multi-worker)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services import server_job_lock as lock


@pytest.fixture(autouse=True)
def _clean_local_locks():
    lock.clear_local_locks_for_tests()
    lock.reset_redis_client_for_tests()
    # Force local path (no Redis) unless a test patches _get_redis
    lock._redis_failed = True
    yield
    lock.clear_local_locks_for_tests()
    lock.reset_redis_client_for_tests()


def test_acquire_release_local():
    t = lock.try_acquire_server_lock("backup", 1, holder="job-1")
    assert t is not None
    assert lock.is_server_locked("backup", 1)
    assert lock.try_acquire_server_lock("backup", 1, holder="job-2") is None
    assert lock.release_server_lock("backup", 1, t) is True
    assert not lock.is_server_locked("backup", 1)


def test_different_servers_independent():
    a = lock.try_acquire_server_lock("backup", 10, holder="a")
    b = lock.try_acquire_server_lock("backup", 11, holder="b")
    assert a and b
    assert a != b
    lock.release_server_lock("backup", 10, a)
    assert lock.is_server_locked("backup", 11)
    lock.release_server_lock("backup", 11, b)


def test_backup_and_patch_kinds_independent():
    b = lock.try_acquire_server_lock("backup", 5, holder="b")
    p = lock.try_acquire_server_lock("patch", 5, holder="p")
    assert b and p
    lock.release_server_lock("backup", 5, b)
    lock.release_server_lock("patch", 5, p)


def test_release_wrong_token_noop():
    t = lock.try_acquire_server_lock("backup", 3, holder="x")
    assert lock.release_server_lock("backup", 3, "not-the-token") is False
    assert lock.is_server_locked("backup", 3)
    assert lock.release_server_lock("backup", 3, t) is True


def test_refresh_extends_local():
    t = lock.try_acquire_server_lock("backup", 9, holder="h", ttl_sec=60)
    assert lock.refresh_server_lock("backup", 9, t, ttl_sec=120) is True
    assert lock.refresh_server_lock("backup", 9, "other", ttl_sec=120) is False


def test_redis_path_set_nx(monkeypatch):
    lock.reset_redis_client_for_tests()
    lock._redis_failed = False
    fake = MagicMock()
    fake.set.return_value = True
    fake.eval.return_value = 1
    monkeypatch.setattr(lock, "_get_redis", lambda: fake)

    t = lock.try_acquire_server_lock("backup", 42, holder="job-9", ttl_sec=100)
    assert t is not None
    fake.set.assert_called_once()
    args, kwargs = fake.set.call_args
    assert args[0] == "piherder:server_lock:backup:42"
    assert kwargs.get("nx") is True
    assert kwargs.get("ex") == 100

    assert lock.release_server_lock("backup", 42, t) is True
    fake.eval.assert_called()


def test_redis_busy_returns_none(monkeypatch):
    lock.reset_redis_client_for_tests()
    lock._redis_failed = False
    fake = MagicMock()
    fake.set.return_value = None  # NX not set
    monkeypatch.setattr(lock, "_get_redis", lambda: fake)
    assert lock.try_acquire_server_lock("backup", 1, holder="a") is None

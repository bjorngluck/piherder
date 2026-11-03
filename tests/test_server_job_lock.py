"""Unit tests for per-server job mutex (multi-worker)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Retry

from app.services import server_job_lock as lock


@pytest.fixture(autouse=True)
def _clean_local_locks():
    lock.clear_local_locks_for_tests()
    lock.reset_redis_client_for_tests()
    lock.reset_lock_acquire_failures_for_tests()
    # Force local path (no Redis) unless a test patches _get_redis
    lock._redis_failed = True
    yield
    lock.clear_local_locks_for_tests()
    lock.reset_redis_client_for_tests()
    lock.reset_lock_acquire_failures_for_tests()


def test_acquire_release_local():
    t = lock.try_acquire_server_lock("backup", 1, holder="job-1")
    assert t is not None
    assert lock.is_server_locked("backup", 1)
    assert lock.try_acquire_server_lock("backup", 1, holder="job-2") is None
    assert lock.release_server_lock("backup", 1, t) is True
    assert not lock.is_server_locked("backup", 1)


def test_dual_acquire_lower_id_first_releases_on_partial():
    a = lock.try_acquire_server_lock("backup", 2, holder="other")
    assert a
    # 1 is free, 2 is held — must not leave 1 locked
    assert lock.try_acquire_dual_server_lock("backup", 1, 2, holder="mig") is None
    assert not lock.is_server_locked("backup", 1)
    assert lock.is_server_locked("backup", 2)
    lock.release_server_lock("backup", 2, a)


def test_dual_acquire_and_release_caller_order():
    toks = lock.try_acquire_dual_server_lock("backup", 20, 10, holder="mig")
    assert toks is not None
    ta, tb = toks
    assert lock.is_server_locked("backup", 20)
    assert lock.is_server_locked("backup", 10)
    # second dual acquire fails
    assert lock.try_acquire_dual_server_lock("backup", 10, 20, holder="other") is None
    lock.release_dual_server_lock("backup", 20, ta, 10, tb)
    assert not lock.is_server_locked("backup", 20)
    assert not lock.is_server_locked("backup", 10)


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
    assert lock.lock_acquire_failure_counts() == {"redis_error": 0, "busy": 1}


def test_redis_acquire_error_does_not_grant_and_reschedules(monkeypatch, caplog):
    """A Redis error defers the backup. The job body does not run and it does not succeed."""
    import logging

    from app.tasks import backup_server

    class _RedisDown:
        def set(self, *args, **kwargs):
            raise ConnectionError("redis down")

    monkeypatch.setattr(lock, "_get_redis", lambda: _RedisDown())
    with caplog.at_level(logging.WARNING, logger="app.services.server_job_lock"):
        with pytest.raises(lock.LockUnavailable):
            lock.try_acquire_server_lock("backup", 3, holder="job-7")
    assert not lock.is_server_locked("backup", 3)
    assert lock.lock_acquire_failure_counts() == {"redis_error": 1, "busy": 0}
    assert any(
        rec.levelname == "WARNING" and "Redis acquire failed" in rec.message
        for rec in caplog.records
    )

    server = SimpleNamespace(id=3, name="host-a", hostname="host-a.example.com")
    job = SimpleNamespace(
        id=7,
        status="pending",
        server_id=3,
        details="{}",
        started_at=None,
        finished_at=None,
    )
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = server
    mock_db.get.return_value = job

    with (
        patch("app.tasks.Session", return_value=mock_db),
        patch("app.tasks._update_job_status") as upd,
        patch("app.tasks.run_backup") as run_backup,
        patch.object(backup_server, "retry", side_effect=Retry(message="wait")) as retry,
    ):
        with pytest.raises(Retry):
            backup_server.run(3, job_id=7)

    retry.assert_called_once()
    run_backup.assert_not_called()
    upd.assert_called()
    status = upd.call_args.args[1] if upd.call_args.args else upd.call_args.kwargs.get("status")
    extra = upd.call_args.args[2] if len(upd.call_args.args) > 2 else {}
    # Deferred: still pending, retried, and not recorded as success or failure.
    assert status == "pending"
    assert status not in ("success", "failed")
    assert extra.get("current") == "lock_unavailable"
    assert extra.get("log_lines") == ["lock_unavailable"]
    assert "redis down" not in str(extra)
    assert "Waiting for another backup" not in str(extra)
    assert lock.lock_acquire_failure_counts()["redis_error"] == 2


def test_redis_auth_error_fails_the_job_and_does_not_defer(monkeypatch, caplog):
    """A rejected password fails the job. It is not deferred like a connection error."""
    import logging

    import redis

    from app.tasks import backup_server

    marker = "marker-secret redis://192.0.2.40:6379/0"

    class _Rejected:
        def set(self, *args, **kwargs):
            raise redis.exceptions.AuthenticationError(f"WRONGPASS {marker}")

    monkeypatch.setattr(lock, "_get_redis", lambda: _Rejected())
    with caplog.at_level(logging.ERROR, logger="app.services.server_job_lock"):
        with pytest.raises(lock.RedisAuthFailed) as caught:
            lock.try_acquire_server_lock("backup", 3, holder="job-7")
    assert str(caught.value) == "Redis authentication failed."
    assert marker not in " ".join(rec.getMessage() for rec in caplog.records)
    assert "WRONGPASS" not in " ".join(rec.getMessage() for rec in caplog.records)
    assert lock.lock_acquire_failure_counts()["redis_error"] == 1

    server = SimpleNamespace(id=3, name="host-a", hostname="host-a.example.com")
    job = SimpleNamespace(
        id=7,
        status="pending",
        server_id=3,
        details="{}",
        started_at=None,
        finished_at=None,
    )
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = server
    mock_db.get.return_value = job

    with (
        caplog.at_level(logging.ERROR, logger="app.tasks"),
        patch("app.tasks.Session", return_value=mock_db),
        patch("app.tasks._update_job_status") as upd,
        patch("app.tasks.run_backup") as run_backup,
        patch("app.tasks._flush_job_progress_db"),
        patch("app.tasks.clear_job_progress_buffer"),
        patch.object(backup_server, "retry", side_effect=Retry(message="wait")) as retry,
    ):
        backup_server.run(3, job_id=7)

    retry.assert_not_called()
    run_backup.assert_not_called()
    upd.assert_called()
    status = upd.call_args.args[1]
    extra = upd.call_args.args[2]
    assert status == "failed"
    assert extra.get("error") == "Redis authentication failed."
    assert marker not in str(extra)
    assert "WRONGPASS" not in str(extra)
    assert "redis://" not in str(extra)
    task_log = " ".join(rec.getMessage() for rec in caplog.records)
    assert marker not in task_log
    assert "WRONGPASS" not in task_log


def test_dual_lock_releases_the_first_host_when_auth_fails(monkeypatch):
    import redis

    calls = {"n": 0}

    class _SecondRejects:
        def set(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return True
            raise redis.exceptions.AuthenticationError("WRONGPASS")

        def eval(self, *args, **kwargs):
            return 1

    monkeypatch.setattr(lock, "_get_redis", lambda: _SecondRejects())
    with pytest.raises(lock.RedisAuthFailed):
        lock.try_acquire_dual_server_lock("backup", 2, 9, holder="job-1")
    assert calls["n"] == 2

"""Move's inline dual-lock path finalizes auth failures and retries outages."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import jobs_migrate as jm
from app.services.server_job_lock import LockUnavailable, RedisAuthFailed


class _Session:
    def __init__(self, job):
        self.job = job

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, _model, _ident):
        return self.job

    def add(self, _obj):
        return None

    def commit(self):
        return None


class _Jobs:
    def __init__(self, job):
        self.job = job
        self.finished = []

    def _load_server_for_job(self, _server_id):
        return SimpleNamespace(id=1, name="host-a"), "host-a"

    def _finish(self, *args):
        self.finished.append(args)

    def _get_fresh_session(self):
        return _Session(self.job)

    def _merge_job_details(self, job, **fields):
        job.merged = fields


def _job():
    return SimpleNamespace(id=50, status="pending", merged=None)


def test_dual_lock_auth_fails_the_job_once(monkeypatch):
    job = _job()
    js = _Jobs(job)
    monkeypatch.setattr(jm, "_jobs", lambda: js)
    monkeypatch.setattr(
        jm,
        "try_acquire_dual_server_lock",
        lambda *_a, **_k: (_ for _ in ()).throw(RedisAuthFailed()),
    )
    ran = {"n": 0}
    monkeypatch.setattr(jm, "_execute_service_migrate", lambda *_a, **_k: ran.__setitem__("n", 1))
    jm._run_migrate_holding_locks(50, 1, 2, "app1", 7)
    assert ran["n"] == 0
    assert js.finished[-1][2] == "failed"
    assert js.finished[-1][3] == "Redis authentication failed."
    assert js.finished[-1][5] == "service_migrate"


def test_dual_lock_outage_stays_pending_and_raises(monkeypatch):
    job = _job()
    js = _Jobs(job)
    monkeypatch.setattr(jm, "_jobs", lambda: js)
    monkeypatch.setattr(
        jm,
        "try_acquire_dual_server_lock",
        lambda *_a, **_k: (_ for _ in ()).throw(LockUnavailable()),
    )
    monkeypatch.setattr(
        jm,
        "_execute_service_migrate_undo",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ran")),
    )
    with pytest.raises(LockUnavailable):
        jm._run_undo_holding_locks(50, 1, 2, 7)
    assert js.finished == []
    assert job.status == "pending"
    assert job.merged["current"] == "lock_unavailable"


def test_lock_outage_leaves_a_finished_job_alone(monkeypatch):
    job = _job()
    job.status = "failed"
    js = _Jobs(job)
    monkeypatch.setattr(jm, "_jobs", lambda: js)
    monkeypatch.setattr(
        jm,
        "try_acquire_dual_server_lock",
        lambda *_a, **_k: (_ for _ in ()).throw(LockUnavailable()),
    )
    with pytest.raises(LockUnavailable):
        jm._run_undo_holding_locks(50, 1, 2, 7)
    assert job.status == "failed"
    assert job.merged is None


def test_dual_lock_busy_still_fails_the_job(monkeypatch):
    job = _job()
    js = _Jobs(job)
    monkeypatch.setattr(jm, "_jobs", lambda: js)
    monkeypatch.setattr(jm, "try_acquire_dual_server_lock", lambda *_a, **_k: None)
    monkeypatch.setattr(
        jm,
        "_execute_dest_up_recover",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ran")),
    )
    jm._run_dest_recover_holding_locks(50, 1, 2, 7)
    assert "already using" in js.finished[-1][3]
    assert js.finished[-1][2] == "failed"

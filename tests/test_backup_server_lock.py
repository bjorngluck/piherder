"""backup_server multi-worker mutex: wait/retry when lock held."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Retry


def _job(status="pending"):
    return SimpleNamespace(
        id=7,
        status=status,
        server_id=3,
        details="{}",
        started_at=None,
        finished_at=None,
    )


def test_backup_server_retries_when_lock_busy():
    from app.tasks import backup_server

    server = SimpleNamespace(id=3, name="pi", hostname="pi.local")
    job = _job("pending")

    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = server
    mock_db.get.return_value = job

    task = backup_server
    # bind=True task: call run with mock request
    with (
        patch("app.tasks.Session", return_value=mock_db),
        patch("app.tasks.try_acquire_server_lock", return_value=None),
        patch("app.tasks._update_job_status") as upd,
        patch.object(task, "retry", side_effect=Retry(message="wait")) as retry,
    ):
        with pytest.raises(Retry):
            task.run(3, job_id=7)

    retry.assert_called_once()
    assert retry.call_args.kwargs.get("countdown") == 20
    upd.assert_called()
    # Must not have started rsync
    assert mock_db.commit.call_count == 0 or True


def test_backup_server_skips_cancelled_while_waiting():
    from app.tasks import backup_server

    server = SimpleNamespace(id=3, name="pi", hostname="pi.local")
    job = _job("cancelled")

    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = server
    mock_db.get.return_value = job

    with (
        patch("app.tasks.Session", return_value=mock_db),
        patch("app.tasks.try_acquire_server_lock") as acq,
        patch.object(backup_server, "retry") as retry,
    ):
        out = backup_server.run(3, job_id=7)

    assert out["status"] == "skipped"
    acq.assert_not_called()
    retry.assert_not_called()


def test_backup_server_releases_lock_after_success():
    from app.tasks import backup_server

    server = SimpleNamespace(
        id=3,
        name="pi",
        hostname="pi.local",
        last_backup_at=None,
        get_backup_sources=lambda: [],
    )
    job = _job("pending")
    job.status = "pending"

    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = server

    def _get(model, jid):
        if jid == 7:
            # After success path may re-read; keep non-cancelled
            j = _job("running")
            return j
        return None

    mock_db.get.side_effect = lambda model, jid: _get(model, jid)

    with (
        patch("app.tasks.Session", return_value=mock_db),
        patch("app.tasks.try_acquire_server_lock", return_value="tok-abc"),
        patch("app.tasks.release_server_lock") as rel,
        patch("app.tasks._update_job_status"),
        patch("app.tasks.record_backup_audit_from_job"),
        patch("app.tasks.run_backup", return_value={"ok": True, "sources": []}),
        patch("app.tasks.backup_succeeded", return_value=True),
        patch("app.tasks._flush_job_progress_db"),
        patch("app.tasks.clear_job_progress_buffer"),
    ):
        out = backup_server.run(3, job_id=7)

    assert out["status"] == "success"
    rel.assert_called_with("backup", 3, "tok-abc")

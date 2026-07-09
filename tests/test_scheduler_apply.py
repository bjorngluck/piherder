"""Unit tests for patch-apply schedule skip decisions (no APScheduler / DB)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.scheduler import (
    container_apply_skip_reason,
    os_apply_skip_reason,
    schedule_container_apply_job,
    schedule_os_apply_job,
    _cron_trigger,
)


def _server(**kwargs):
    defaults = dict(
        id=1,
        os_patch_enabled=True,
        os_apply_enabled=True,
        os_apply_only_if_updates=True,
        os_updates_count=3,
        container_patch_enabled=True,
        container_apply_enabled=True,
        container_apply_only_if_updates=True,
        container_updates_count=2,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_os_apply_runs_when_enabled_and_updates():
    assert os_apply_skip_reason(_server()) is None


def test_os_apply_skip_disabled_feature():
    assert os_apply_skip_reason(_server(os_patch_enabled=False)) == "disabled"
    assert os_apply_skip_reason(_server(os_apply_enabled=False)) == "disabled"


def test_os_apply_skip_no_updates():
    assert os_apply_skip_reason(_server(os_updates_count=0)) == "no_updates"
    assert os_apply_skip_reason(_server(os_updates_count=None)) is None  # unknown → allow
    # only_if_updates off → run even with zero
    assert (
        os_apply_skip_reason(
            _server(os_updates_count=0, os_apply_only_if_updates=False)
        )
        is None
    )


def test_apply_skip_missing_server():
    assert os_apply_skip_reason(None) == "missing"
    assert container_apply_skip_reason(None) == "missing"


def test_container_apply_skip_paths():
    assert container_apply_skip_reason(_server()) is None
    assert (
        container_apply_skip_reason(_server(container_patch_enabled=False)) == "disabled"
    )
    assert (
        container_apply_skip_reason(_server(container_apply_enabled=False)) == "disabled"
    )
    assert (
        container_apply_skip_reason(_server(container_updates_count=0)) == "no_updates"
    )
    assert (
        container_apply_skip_reason(_server(container_updates_count=None)) is None
    )
    # only_if_updates off → run even with zero
    assert (
        container_apply_skip_reason(
            _server(container_updates_count=0, container_apply_only_if_updates=False)
        )
        is None
    )


def test_cron_trigger_valid():
    t = _cron_trigger("0 2 * * *")
    assert t is not None


def test_cron_trigger_invalid_fields():
    try:
        _cron_trigger("0 2 *")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "5" in str(e)


def _patch_db_server(server):
    """Context: Session(engine) yields a fake DB with server.get."""
    db = MagicMock()
    db.get.return_value = server
    db.__enter__ = MagicMock(return_value=db)
    db.__exit__ = MagicMock(return_value=False)
    session_cls = MagicMock(return_value=db)
    return session_cls, db


def test_schedule_os_apply_skips_disabled_without_enqueue():
    server = _server(os_apply_enabled=False)
    session_cls, _db = _patch_db_server(server)
    with patch("app.services.scheduler.Session", session_cls):
        with patch("app.services.jobs.enqueue_os_patch_apply") as enqueue:
            # engine import is inside the function from database
            with patch("app.database.engine", MagicMock()):
                schedule_os_apply_job(1)
            enqueue.assert_not_called()


def test_schedule_os_apply_busy_enqueue_returns_none():
    server = _server()
    session_cls, _db = _patch_db_server(server)
    with patch("app.services.scheduler.Session", session_cls):
        with patch(
            "app.services.jobs.enqueue_os_patch_apply", return_value=None
        ) as enqueue:
            with patch("app.database.engine", MagicMock()):
                schedule_os_apply_job(1)  # must not raise
            enqueue.assert_called_once_with(1, user_id=None, scheduled=True)


def test_schedule_container_apply_skips_no_updates():
    server = _server(container_updates_count=0)
    session_cls, _db = _patch_db_server(server)
    with patch("app.services.scheduler.Session", session_cls):
        with patch("app.services.jobs.enqueue_container_patch_apply") as enqueue:
            with patch("app.database.engine", MagicMock()):
                schedule_container_apply_job(1)
            enqueue.assert_not_called()


def test_schedule_container_apply_enqueues_when_ready():
    server = _server(id=7)
    session_cls, _db = _patch_db_server(server)
    fake_job = SimpleNamespace(id=99)
    with patch("app.services.scheduler.Session", session_cls):
        with patch(
            "app.services.jobs.enqueue_container_patch_apply", return_value=fake_job
        ) as enqueue:
            with patch("app.database.engine", MagicMock()):
                schedule_container_apply_job(7)
            enqueue.assert_called_once_with(7, user_id=None, scheduled=True)

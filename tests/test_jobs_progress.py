"""Unit tests for job details merge, public dict, cancel, and labels (no DB)."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.jobs import (
    JOB_TYPE_LABELS,
    JobNotCancellable,
    _initial_job_details,
    _mark_job_cancelled,
    _merge_job_details,
    cancel_job,
    job_public_dict,
    job_type_label,
)


def test_job_type_labels():
    assert job_type_label("os_patch") == "OS patch"
    assert job_type_label("backup") == "Backup"
    assert job_type_label(None) == "Job"
    assert job_type_label("unknown_thing") == "Unknown Thing"
    assert "container_patch" in JOB_TYPE_LABELS


def test_initial_job_details_shape():
    raw = _initial_job_details("OS patch queued…", scheduled=True, os_steps=["update"])
    data = json.loads(raw)
    assert data["current"] == "queued"
    assert data["done"] is False
    assert data["log_lines"] == ["OS patch queued…"]
    assert data["scheduled"] is True
    assert data["os_steps"] == ["update"]


def test_merge_job_details_log_line_cap():
    job = SimpleNamespace(details=None)
    _merge_job_details(job, current="running", log_line="first")
    data = json.loads(job.details)
    assert data["current"] == "running"
    assert data["log_lines"] == ["first"]

    for i in range(50):
        _merge_job_details(job, log_line=f"line-{i}")
    data = json.loads(job.details)
    assert len(data["log_lines"]) == 40
    assert data["log_lines"][0] == "line-10"
    assert data["log_lines"][-1] == "line-49"


def test_merge_job_details_replaces_log_lines_list():
    job = SimpleNamespace(details=json.dumps({"log_lines": ["a", "b"]}))
    _merge_job_details(job, log_lines=["x", "y", "z"], current="patching")
    data = json.loads(job.details)
    assert data["log_lines"] == ["x", "y", "z"]
    assert data["current"] == "patching"


def test_job_public_dict_summary_and_tails():
    lines = [f"L{i}" for i in range(20)]
    job = SimpleNamespace(
        id=42,
        server_id=7,
        job_type="os_patch",
        status="running",
        created_at=datetime(2026, 7, 9, 12, 0, 0),
        started_at=datetime(2026, 7, 9, 12, 0, 1),
        finished_at=None,
        details=json.dumps(
            {
                "current": "patching",
                "summary": "upgrading…",
                "log_lines": lines,
                "scheduled": True,
                "os_steps": ["update", "upgrade"],
                "result_snippet": "big " * 100,
            }
        ),
    )
    short = job_public_dict(job, detail=False)
    assert short["id"] == 42
    assert short["job_type_label"] == "OS patch"
    assert short["current"] == "patching"
    assert short["scheduled"] is True
    assert short["done"] is False
    assert len(short["log_tail"]) == 8
    assert short["log_tail"][-1] == "L19"
    assert "details_json" not in short

    full = job_public_dict(job, detail=True)
    assert len(full["log_tail"]) == 20
    assert "details_json" in full
    assert full["os_steps"] == ["update", "upgrade"]


def test_job_public_dict_done_on_terminal():
    job = SimpleNamespace(
        id=1,
        server_id=1,
        job_type="backup",
        status="success",
        created_at=None,
        started_at=None,
        finished_at=datetime(2026, 7, 9, 12, 5, 0),
        details=json.dumps({"summary": "ok", "log_lines": ["done"]}),
    )
    d = job_public_dict(job)
    assert d["done"] is True
    assert d["status"] == "success"
    assert d["cancellable"] is False


def test_job_public_dict_cancellable_when_active():
    job = SimpleNamespace(
        id=9,
        server_id=1,
        job_type="backup",
        status="running",
        created_at=datetime(2026, 7, 10, 12, 0, 0),
        started_at=datetime(2026, 7, 10, 12, 0, 1),
        finished_at=None,
        details=json.dumps({"current": "/home/bjorn/docker/", "log_lines": ["Backing up…"]}),
    )
    d = job_public_dict(job)
    assert d["done"] is False
    assert d["cancellable"] is True


def test_job_public_dict_done_when_cancelled():
    job = SimpleNamespace(
        id=10,
        server_id=1,
        job_type="os_patch",
        status="cancelled",
        created_at=None,
        started_at=None,
        finished_at=datetime(2026, 7, 10, 12, 5, 0),
        details=json.dumps({"summary": "Cancelled by user", "log_lines": ["Cancelled by user"]}),
    )
    d = job_public_dict(job)
    assert d["done"] is True
    assert d["cancellable"] is False
    assert d["status"] == "cancelled"


def test_mark_job_cancelled_shape():
    job = SimpleNamespace(
        id=3,
        job_type="os_patch",
        status="running",
        details=json.dumps({"log_lines": ["started"], "current": "patching"}),
        finished_at=None,
    )
    session = MagicMock()
    _mark_job_cancelled(job, "Cancelled by user", session, record_audit=False)
    assert job.status == "cancelled"
    assert job.finished_at is not None
    data = json.loads(job.details)
    assert data["cancelled"] is True
    assert data["done"] is True
    assert "Cancelled by user" in data["log_lines"][-1]
    session.add.assert_called_with(job)


def test_cancel_job_rejects_terminal():
    job = SimpleNamespace(id=1, status="success", job_type="backup", server_id=1)
    session = MagicMock()
    with pytest.raises(JobNotCancellable):
        cancel_job(session, job, user_id=1)


def test_cancel_job_backup_stops_rsync():
    job = SimpleNamespace(
        id=260,
        status="running",
        job_type="backup",
        server_id=1,
        celery_task_id="task-abc",
        details=json.dumps({"log_lines": ["Backing up…"], "current": "/home/bjorn/docker/"}),
        finished_at=None,
    )
    server = SimpleNamespace(id=1, hostname="rpi5-2.example.com")
    session = MagicMock()
    session.get.return_value = server

    with (
        patch("app.services.jobs.backup.stop_backup") as stop,
        patch("app.services.jobs._revoke_celery_task") as revoke,
        patch("app.services.jobs.record_backup_audit_from_job"),
    ):
        out = cancel_job(session, job, user_id=7)

    assert out.status == "cancelled"
    stop.assert_called_once_with("rpi5-2.example.com")
    revoke.assert_called_once_with("task-abc")
    session.commit.assert_called()


def test_cancel_job_non_backup_marks_cancelled():
    job = SimpleNamespace(
        id=11,
        status="pending",
        job_type="os_update_check",
        server_id=2,
        celery_task_id=None,
        details=json.dumps({"log_lines": ["queued"]}),
        finished_at=None,
    )
    session = MagicMock()
    session.get.return_value = SimpleNamespace(hostname="host.example")

    with patch("app.services.jobs._revoke_celery_task") as revoke:
        out = cancel_job(session, job, user_id=1)

    assert out.status == "cancelled"
    revoke.assert_called_once_with(None)
    session.commit.assert_called()

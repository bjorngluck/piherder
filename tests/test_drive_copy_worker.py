"""Drive copy worker limits. No live rclone."""
from __future__ import annotations

import json

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Job
from app.tasks import (
    _drive_copy_failure_message,
    _on_drive_copy_task_failure,
    fail_replicate_job,
    replicate_backup,
)
from app.celery_app import celery


def test_drive_copy_outlives_the_global_two_hour_limit():
    assert replicate_backup.time_limit == 7 * 24 * 3600
    assert replicate_backup.acks_late is False
    visibility = celery.conf.broker_transport_options["visibility_timeout"]
    assert visibility > celery.conf.task_time_limit


def test_worker_death_message():
    assert "time limit" in _drive_copy_failure_message(
        type("HardTimeLimitExceeded", (Exception,), {})("hard")
    ).lower()
    assert "worker stopped" in _drive_copy_failure_message(RuntimeError("gone")).lower()


def test_fail_replicate_job_closes_a_running_copy(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'copy.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.tasks.engine", engine)
    with Session(engine) as session:
        running = Job(
            job_type="backup_replicate",
            status="running",
            details=json.dumps({"current": "copying", "log_lines": ["Drive copy queued…"]}),
        )
        done = Job(job_type="backup_replicate", status="success", details="{}")
        session.add(running)
        session.add(done)
        session.commit()
        session.refresh(running)
        session.refresh(done)
        running_id = running.id
        done_id = done.id

    assert fail_replicate_job(running_id, "stopped") is True
    assert fail_replicate_job(done_id, "stopped") is False
    with Session(engine) as session:
        job = session.get(Job, running_id)
        assert job.status == "failed"
        assert job.finished_at is not None
        details = json.loads(job.details)
        assert details["current"] == "failed"
        assert details["log_lines"][-1] == "stopped"

    class _Sender:
        name = "app.tasks.replicate_backup"

    assert fail_replicate_job(running_id, "again") is False
    _on_drive_copy_task_failure(sender=_Sender(), exception=RuntimeError("x"), args=(running_id,))
    _on_drive_copy_task_failure(sender=type("Other", (), {"name": "app.tasks.backup_server"})(), args=(running_id,))

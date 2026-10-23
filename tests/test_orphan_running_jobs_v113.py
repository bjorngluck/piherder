"""Running jobs are interrupted when their Celery worker is gone."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.database import get_engine
from app.models import Job
from app.services import jobs as jobs_svc


def _clear_running(session: Session) -> None:
    for row in session.exec(select(Job).where(Job.status == "running")).all():
        session.delete(row)
    session.commit()


def _running(session: Session, *, task_id: str, started: datetime) -> Job:
    job = Job(
        job_type="backup",
        status="running",
        celery_task_id=task_id,
        started_at=started,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_dead_worker_interrupts_a_running_job(monkeypatch):
    monkeypatch.setattr(jobs_svc, "celery_worker_activity", lambda: (False, set()))
    with Session(get_engine()) as session:
        _clear_running(session)
        job = _running(session, task_id="task-dead", started=datetime.utcnow())
        assert jobs_svc.reconcile_orphaned_running_jobs(session) == 1
        session.refresh(job)
        assert job.status == "failed"
        assert "no longer alive" in (job.details or "") or job.finished_at is not None


def test_live_task_stays_running(monkeypatch):
    monkeypatch.setattr(jobs_svc, "celery_worker_activity", lambda: (True, {"task-live"}))
    with Session(get_engine()) as session:
        _clear_running(session)
        job = _running(session, task_id="task-live", started=datetime.utcnow() - timedelta(minutes=5))
        assert jobs_svc.reconcile_orphaned_running_jobs(session) == 0
        session.refresh(job)
        assert job.status == "running"
        _clear_running(session)


def test_missing_task_waits_out_the_grace_then_fails(monkeypatch):
    monkeypatch.setattr(jobs_svc, "celery_worker_activity", lambda: (True, set()))
    with Session(get_engine()) as session:
        _clear_running(session)
        young = _running(session, task_id="task-young", started=datetime.utcnow())
        old = _running(
            session,
            task_id="task-old",
            started=datetime.utcnow() - timedelta(minutes=2),
        )
        assert jobs_svc.reconcile_orphaned_running_jobs(session) == 1
        session.refresh(young)
        session.refresh(old)
        assert young.status == "running"
        assert old.status == "failed"
        _clear_running(session)


def test_unknown_worker_check_leaves_jobs_alone(monkeypatch):
    monkeypatch.setattr(jobs_svc, "celery_worker_activity", lambda: None)
    with Session(get_engine()) as session:
        _clear_running(session)
        job = _running(session, task_id="task-unknown", started=datetime.utcnow() - timedelta(minutes=10))
        assert jobs_svc.reconcile_orphaned_running_jobs(session) == 0
        session.refresh(job)
        assert job.status == "running"
        _clear_running(session)

"""Running jobs are interrupted when their Celery worker is gone."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlmodel import Session, select

from app.database import get_engine
from app.models import AuditLog, Job, Server
from app.services import jobs as jobs_svc
from app.tasks import _on_host_bootstrap_revoked


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


def test_crashed_host_bootstrap_is_failed(monkeypatch):
    monkeypatch.setattr(jobs_svc, "celery_worker_activity", lambda: (False, set()))
    with Session(get_engine()) as session:
        _clear_running(session)
        job = Job(
            job_type="host_bootstrap",
            status="running",
            celery_task_id="task-bootstrap-dead",
            started_at=datetime.utcnow() - timedelta(minutes=2),
            details="{}",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        assert jobs_svc.reconcile_orphaned_running_jobs(session) == 1
        session.refresh(job)
        assert job.status == "failed"
        assert "no longer alive" in (job.details or "")
        _clear_running(session)


def test_expired_host_bootstrap_frees_the_host(monkeypatch):
    monkeypatch.setattr(jobs_svc, "celery_worker_activity", lambda: None)
    old = datetime.utcnow() - timedelta(seconds=120 + jobs_svc._HOST_BOOTSTRAP_EXPIRE_GRACE_SEC + 5)
    young = datetime.utcnow() - timedelta(seconds=30)
    with Session(get_engine()) as session:
        _clear_running(session)
        for row in session.exec(select(Job).where(Job.status == "pending")).all():
            session.delete(row)
        session.commit()
        host = Server(name="host-a", hostname="host-a.example.com")
        session.add(host)
        session.commit()
        session.refresh(host)
        expired = Job(
            server_id=host.id,
            job_type="host_bootstrap",
            status="pending",
            created_at=old,
            details="{}",
        )
        fresh = Job(
            server_id=host.id,
            job_type="host_bootstrap",
            status="pending",
            created_at=young,
            details="{}",
        )
        other = Job(
            server_id=host.id,
            job_type="os_patch",
            status="pending",
            created_at=old,
            details="{}",
        )
        session.add(expired)
        session.add(fresh)
        session.add(other)
        session.commit()
        session.refresh(expired)
        audit = AuditLog(
            server_id=host.id,
            action="host_bootstrap",
            status="running",
            details=f"Job #{expired.id} started",
        )
        session.add(audit)
        session.commit()
        assert jobs_svc.reconcile_orphaned_running_jobs(session) == 1
        session.refresh(expired)
        session.refresh(fresh)
        session.refresh(other)
        session.refresh(audit)
        assert expired.status == "failed"
        assert jobs_svc.HOST_BOOTSTRAP_EXPIRED_REASON in (expired.details or "")
        assert fresh.status == "pending"
        assert other.status == "pending"
        assert audit.status == "failed"
        assert jobs_svc._active_job_of_type(session, host.id, "host_bootstrap") is fresh
        for row in (expired, fresh, other):
            session.delete(row)
        session.delete(audit)
        session.delete(host)
        session.commit()


def test_revoke_signal_fails_only_an_expired_host_bootstrap():
    with Session(get_engine()) as session:
        host = Server(name="host-b", hostname="host-b.example.com")
        session.add(host)
        session.commit()
        session.refresh(host)
        pending = Job(
            server_id=host.id,
            job_type="host_bootstrap",
            status="pending",
            details="{}",
        )
        running = Job(
            server_id=host.id,
            job_type="host_bootstrap",
            status="running",
            details="{}",
        )
        session.add(pending)
        session.add(running)
        session.commit()
        session.refresh(pending)
        session.refresh(running)
        pending_id, running_id, host_id = pending.id, running.id, host.id
    sender = SimpleNamespace(name="app.tasks.host_bootstrap_job")
    _on_host_bootstrap_revoked(
        sender=sender,
        request=SimpleNamespace(args=(pending_id, host_id, 0, {})),
        expired=False,
    )
    _on_host_bootstrap_revoked(
        sender=sender,
        request=SimpleNamespace(args=(running_id, host_id, 0, {})),
        expired=True,
    )
    _on_host_bootstrap_revoked(
        sender=SimpleNamespace(name="app.tasks.exclusive_job"),
        request=SimpleNamespace(args=(pending_id, host_id, 0, "os_patch", {})),
        expired=True,
    )
    with Session(get_engine()) as session:
        still = session.get(Job, pending_id)
        live = session.get(Job, running_id)
        assert still.status == "pending"
        assert live.status == "running"
    _on_host_bootstrap_revoked(
        sender=sender,
        request=SimpleNamespace(args=(pending_id, host_id, 0, {})),
        expired=True,
    )
    with Session(get_engine()) as session:
        row = session.get(Job, pending_id)
        assert row.status == "failed"
        assert jobs_svc.HOST_BOOTSTRAP_EXPIRED_REASON in (row.details or "")
        active = jobs_svc._active_job_of_type(session, host_id, "host_bootstrap")
        assert active is not None and active.id == running_id
        session.delete(row)
        session.delete(session.get(Job, running_id))
        session.delete(session.get(Server, host_id))
        session.commit()


def test_unknown_worker_check_leaves_jobs_alone(monkeypatch):
    monkeypatch.setattr(jobs_svc, "celery_worker_activity", lambda: None)
    with Session(get_engine()) as session:
        _clear_running(session)
        job = _running(session, task_id="task-unknown", started=datetime.utcnow() - timedelta(minutes=10))
        assert jobs_svc.reconcile_orphaned_running_jobs(session) == 0
        session.refresh(job)
        assert job.status == "running"
        _clear_running(session)

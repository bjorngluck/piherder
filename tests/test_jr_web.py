"""Jr-web: retention and herder backup on housekeeping_job; host facts on exclusive_job."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from datetime import datetime, timedelta

from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AuditLog, Job, Server
from app.services import host_facts as facts
from app.services import jobs as js
from app.tasks import housekeeping_job


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _bind(monkeypatch, engine):
    monkeypatch.setattr(js, "engine", engine)
    monkeypatch.setattr(facts, "engine", engine)

    @contextmanager
    def _fresh():
        session = Session(engine, expire_on_commit=False)
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(js, "_get_fresh_session", _fresh)
    return _fresh


def _server(engine) -> Server:
    with Session(engine) as session:
        srv = Server(name="pi", hostname="pi.local", ssh_port=22)
        session.add(srv)
        session.commit()
        session.refresh(srv)
        session.expunge(srv)
        return srv


class _Task:
    def __init__(self):
        self.request = SimpleNamespace(id="house-1", hostname="celery@lab-a")


def test_housekeeping_task_name_and_lanes():
    assert housekeeping_job.name == "app.tasks.housekeeping_job"
    assert "retention" in js._HOUSEKEEPING_JOB_TYPES
    assert "herder_backup" in js._HOUSEKEEPING_JOB_TYPES
    assert "host_facts" not in js._HOUSEKEEPING_JOB_TYPES
    assert "retention" not in js._STACK_MUTATING_JOB_TYPES
    assert "herder_backup" not in js._STACK_MUTATING_JOB_TYPES


def test_running_redelivery_fails_and_does_not_archive(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    with Session(engine) as session:
        job = Job(job_type="herder_backup", status="running", details="{}")
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.id
    with patch(
        "app.services.herder_backup.create_herder_backup",
        side_effect=AssertionError("must not resume"),
    ):
        out = js.run_housekeeping_job(_Task(), jid, 0, 1, "herder_backup")
    assert out["reason"] == "worker_restart"
    with Session(engine) as session:
        row = session.get(Job, jid)
        assert row.status == "failed"
        assert "not resumed" in (row.details or "")


def test_enqueue_herder_backup_has_no_host_and_reuses_the_active_row(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    seen: dict = {}

    def delay(job_id, server_id, audit_id, job_type):
        seen["args"] = (job_id, server_id, audit_id, job_type)
        return SimpleNamespace(id="celery-hb-1")

    monkeypatch.setattr("app.services.jobs_exclusive.exclusive_runs_inline", lambda: False)
    monkeypatch.setattr(js.housekeeping_job_task, "delay", delay)
    with Session(engine) as session:
        first = js.enqueue_herder_backup_job(
            session, 1, include_audit=True, config_only=False
        )
        second = js.enqueue_herder_backup_job(
            session, 1, include_audit=False, config_only=True
        )
        assert first.id == second.id
        assert first.server_id is None
        session.refresh(first)
        assert first.celery_task_id == "celery-hb-1"
        assert '"config_only": false' in (first.details or "")
        assert '"include_audit": true' in (first.details or "")
        n = session.exec(select(Job).where(Job.job_type == "herder_backup")).all()
        assert len(n) == 1
    assert seen["args"][1] == 0
    assert seen["args"][3] == "herder_backup"


def test_stale_pending_self_backup_fails_and_raises_the_critical_alert(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    notes: list[tuple[str, str]] = []

    def _alert(message, link_url="/herder-backups"):
        notes.append((message, link_url))

    monkeypatch.setattr(js, "_notify_herder_backup_failed", _alert)
    old = datetime.utcnow() - timedelta(minutes=31)
    with Session(engine) as session:
        fresh = Job(job_type="herder_backup", status="pending", details="{}")
        running = Job(
            job_type="herder_backup",
            status="running",
            created_at=old,
            worker_hostname="celery@lab-a",
            details="{}",
        )
        stuck = Job(
            job_type="herder_backup",
            status="pending",
            created_at=old,
            details='{"current": "queued"}',
        )
        session.add(fresh)
        session.add(running)
        session.add(stuck)
        session.commit()
        session.refresh(stuck)
        stuck_id = stuck.id
        session.add(
            AuditLog(
                action="herder_backup",
                status="running",
                details=f"Job #{stuck_id} started · config_only",
            )
        )
        session.commit()
        timed_out = js.expire_stale_pending_herder_backups(session)
        assert timed_out == [stuck_id]
        assert session.get(Job, fresh.id).status == "pending"
        assert session.get(Job, running.id).status == "running"
        failed = session.get(Job, stuck_id)
        assert failed.status == "failed"
        assert failed.finished_at is not None
        assert "stayed pending" in (failed.details or "")
        assert '"error"' in (failed.details or "")
        audit = session.exec(select(AuditLog)).one()
        assert audit.status == "failed"
    assert len(notes) == 1
    assert f"#{stuck_id}" in notes[0][0]
    assert "pending" in notes[0][0]
    assert notes[0][1] == f"/jobs?highlight={stuck_id}"
    with Session(engine) as session:
        again = js.expire_stale_pending_herder_backups(session)
        assert again == []
    assert len(notes) == 1


def test_enqueue_replaces_a_self_backup_that_timed_out(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    notes: list[str] = []
    monkeypatch.setattr(
        js, "_notify_herder_backup_failed", lambda message, link_url="/herder-backups": notes.append(message)
    )
    monkeypatch.setattr("app.services.jobs_exclusive.exclusive_runs_inline", lambda: False)
    monkeypatch.setattr(
        js.housekeeping_job_task,
        "delay",
        lambda *args: SimpleNamespace(id="celery-hb-new"),
    )
    old = datetime.utcnow() - timedelta(minutes=45)
    with Session(engine) as session:
        session.add(
            Job(
                job_type="herder_backup",
                status="pending",
                created_at=old,
                details="{}",
            )
        )
        session.commit()
        queued = js.enqueue_herder_backup_job(
            session, 1, include_audit=False, config_only=True
        )
        session.refresh(queued)
        rows = session.exec(select(Job).where(Job.job_type == "herder_backup")).all()
        assert len(rows) == 2
        assert queued.status == "pending"
        assert queued.celery_task_id == "celery-hb-new"
        assert any(row.status == "failed" for row in rows)
    assert len(notes) == 1


def test_request_refresh_queues_one_host_facts_job(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    srv = _server(engine)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    facts.request_refresh(srv.id, force=False)
    facts.request_refresh(srv.id, force=True)
    with Session(engine) as session:
        rows = session.exec(select(Job).where(Job.job_type == "host_facts")).all()
        assert len(rows) == 1
        assert rows[0].server_id == srv.id
        assert rows[0].status == "pending"


def test_herder_execute_honors_mode_and_notifies(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    seen: dict = {}

    def _backup(**kwargs):
        seen.update(kwargs)
        return "/tmp/piherder.tar.gz"

    monkeypatch.setattr(js.herder_backup, "create_herder_backup", _backup)
    notes: list[str] = []
    monkeypatch.setattr(js, "_resolve_herder_backup_failed", lambda: notes.append("ok"))
    monkeypatch.setattr(js, "_notify_herder_backup_failed", lambda msg: notes.append(msg))
    with Session(engine) as session:
        job = Job(
            job_type="herder_backup",
            status="pending",
            details='{"include_audit": true, "config_only": false}',
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.id
    js._execute_herder_backup(jid, 1)
    assert seen["include_audit"] is True
    assert seen["config_only"] is False
    assert notes == ["ok"]
    with Session(engine) as session:
        assert session.get(Job, jid).status == "success"

"""Jr-1: exclusive jobs on the default Celery queue. No live SSH."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from celery.exceptions import Retry
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditLog, Job, Server
from app.services import jobs as js
from app.services.jobs_exclusive import (
    EXCLUSIVE_CELERY_TYPES,
    run_exclusive_job,
)
from app.tasks import exclusive_job


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _bind(monkeypatch, engine):
    monkeypatch.setattr(js, "engine", engine)

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
        self.request = SimpleNamespace(id="exclusive-1")
        self.countdowns: list[int] = []

    def retry(self, countdown=None):
        self.countdowns.append(countdown)
        raise Retry(message="wait")


def test_exclusive_types_are_celery_owned_and_not_the_backup_mutex():
    assert "os_patch" in js._CELERY_JOB_TYPES
    assert "docker_stack_deploy" in EXCLUSIVE_CELERY_TYPES
    assert "template_drift_check" in EXCLUSIVE_CELERY_TYPES
    assert "service_migrate" not in EXCLUSIVE_CELERY_TYPES
    assert "backup" not in EXCLUSIVE_CELERY_TYPES
    assert "nmap_discover" not in EXCLUSIVE_CELERY_TYPES
    assert "retention" not in EXCLUSIVE_CELERY_TYPES
    assert "host_facts" not in EXCLUSIVE_CELERY_TYPES
    assert js._is_celery_owned_job(Job(job_type="os_patch", status="pending")) is True
    assert js._is_celery_owned_job(Job(job_type="host_facts", status="pending")) is False
    assert js._is_celery_owned_job(Job(job_type="retention", status="running")) is False
    assert exclusive_job.name == "app.tasks.exclusive_job"
    from app.services import jobs_exclusive as exclusive_mod

    src = open(exclusive_mod.__file__, encoding="utf-8").read()
    assert "try_acquire" not in src


def test_enqueue_os_patch_uses_default_queue_not_the_thread_pool(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    srv = _server(engine)
    seen: dict = {}

    class Pool:
        def submit(self, *args, **kwargs):
            raise AssertionError("thread pool must not run production enqueue")

    def delay(job_id, server_id, audit_id, job_type, payload):
        seen["args"] = (job_id, server_id, audit_id, job_type, payload)
        return SimpleNamespace(id="celery-os-1")

    monkeypatch.setattr(
        "app.services.jobs_exclusive.exclusive_runs_inline", lambda: False
    )
    monkeypatch.setattr(js, "_patch_apply_pool", Pool())
    monkeypatch.setattr(js.exclusive_job_task, "delay", delay)
    monkeypatch.setattr(
        "app.services.server_job_lock.try_acquire_server_lock",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("backup mutex")),
    )
    monkeypatch.setattr(
        "app.services.server_job_lock.try_acquire_dual_server_lock",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("dual mutex")),
    )

    job = js.enqueue_os_patch_apply(srv.id, user_id=1, os_steps=["update"])
    assert seen["args"][1] == srv.id
    assert seen["args"][3] == "os_patch"
    assert seen["args"][4]["os_steps"] == ["update"]
    with Session(engine) as session:
        row = session.get(Job, job.id)
        assert row is not None
        assert row.status == "pending"
        assert row.celery_task_id == "celery-os-1"


def test_second_stack_mutate_still_blocked(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    srv = _server(engine)

    class Pool:
        def submit(self, *args, **kwargs):
            return None

    monkeypatch.setattr(js, "_update_check_pool", Pool())
    first = js.enqueue_docker_stack_deploy(srv.id, "/home/pi/docker/grafana", user_id=1)
    assert first.status == "pending"
    with pytest.raises(js.JobAlreadyActive):
        js.enqueue_docker_stack_lifecycle(srv.id, "/home/pi/docker/grafana", "stop")


def test_running_redelivery_fails_honest_without_ssh(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    srv = _server(engine)
    with Session(engine) as session:
        job = Job(
            server_id=srv.id,
            job_type="os_patch",
            status="running",
            details='{"current":"patching"}',
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        audit = AuditLog(
            server_id=srv.id,
            action="os_patch",
            status="running",
            details=f"Job #{job.id} started",
        )
        session.add(audit)
        session.commit()
        session.refresh(audit)
        jid, aid = job.id, audit.id

    def boom(*args, **kwargs):
        raise AssertionError("ssh")

    monkeypatch.setattr("app.services.ssh.test_connection", boom)
    monkeypatch.setattr(js, "_execute_os_patch_sync", boom)
    result = run_exclusive_job(_Task(), jid, srv.id, aid, "os_patch", {"os_steps": ["update"]})
    assert result["reason"] == "worker_restart"
    with Session(engine) as session:
        row = session.get(Job, jid)
        audit_row = session.get(AuditLog, aid)
        assert row.status == "failed"
        assert "not resumed" in (row.details or "")
        assert audit_row.status == "failed"


def test_host_down_stays_pending_and_retries(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    srv = _server(engine)
    with Session(engine) as session:
        job = Job(
            server_id=srv.id,
            job_type="docker_stack_deploy",
            status="pending",
            details='{"current":"queued","project_path":"/home/pi/docker/g"}',
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.id

    monkeypatch.setattr("app.services.ssh.test_connection", lambda *a, **k: False)
    monkeypatch.setattr(
        js,
        "_execute_docker_stack_deploy",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")),
    )
    task = _Task()
    with pytest.raises(Retry):
        run_exclusive_job(
            task,
            jid,
            srv.id,
            1,
            "docker_stack_deploy",
            {"project_path": "/home/pi/docker/g", "pull": True, "compose_files": []},
        )
    assert task.countdowns == [30]
    with Session(engine) as session:
        row = session.get(Job, jid)
        assert row.status == "pending"
        assert "waiting_for_host" in (row.details or "")
        assert "host_wait_started" in (row.details or "")


def test_host_wait_past_limit_fails(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    srv = _server(engine)
    started = (datetime.utcnow() - timedelta(hours=2)).isoformat()
    with Session(engine) as session:
        job = Job(
            server_id=srv.id,
            job_type="container_patch",
            status="pending",
            details='{"current":"waiting_for_host","host_wait_started":"%s"}' % started,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.id

    monkeypatch.setattr("app.services.ssh.test_connection", lambda *a, **k: False)
    task = _Task()
    result = run_exclusive_job(task, jid, srv.id, 1, "container_patch", {})
    assert result["reason"] == "host_wait"
    assert task.countdowns == []
    with Session(engine) as session:
        row = session.get(Job, jid)
        assert row.status == "failed"
        assert "unreachable" in (row.details or "")


def test_ssh_up_runs_body_without_backup_lock(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    srv = _server(engine)
    with Session(engine) as session:
        job = Job(server_id=srv.id, job_type="os_update_check", status="pending", details="{}")
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.id

    called: dict = {}

    def execute(job_id, server_id, audit_id):
        called["args"] = (job_id, server_id, audit_id)

    monkeypatch.setattr("app.services.ssh.test_connection", lambda *a, **k: True)
    monkeypatch.setattr(js, "_execute_os_update_check", execute)
    monkeypatch.setattr(
        "app.services.server_job_lock.try_acquire_dual_server_lock",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("dual mutex")),
    )
    result = run_exclusive_job(_Task(), jid, srv.id, 4, "os_update_check", {})
    assert result["status"] == "ok"
    assert called["args"][0] == jid
    assert called["args"][1] == srv.id


def test_demo_does_not_probe_ssh(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    srv = _server(engine)
    with Session(engine) as session:
        job = Job(server_id=srv.id, job_type="os_patch", status="pending", details="{}")
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.id

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    monkeypatch.setattr(
        "app.services.ssh.test_connection",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ssh")),
    )
    result = run_exclusive_job(_Task(), jid, srv.id, 1, "os_patch", {})
    assert result["status"] == "demo"
    with Session(engine) as session:
        row = session.get(Job, jid)
        assert row.status == "success"
        assert "demo" in (row.details or "").lower() or "Demo" in (row.details or "")

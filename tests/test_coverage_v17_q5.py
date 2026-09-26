"""v1.7 coverage — exclusive-job edges and TLS endpoint parsing.

No broker and no live SSH. Celery delay and SSH probes are stubs.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import sys

import pytest
from celery.exceptions import MaxRetriesExceededError, Retry
from sqlmodel import Session, SQLModel, create_engine

from app.models import Job, Notification, Server
from app.services import jobs_exclusive as ex

# jobs/__init__ swaps sys.modules onto service.py. Patch that object.
_js = sys.modules["app.services.jobs"]


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _bind(monkeypatch, engine):
    monkeypatch.setattr(_js, "engine", engine)

    @contextmanager
    def _fresh():
        session = Session(engine, expire_on_commit=False)
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(_js, "_get_fresh_session", _fresh)


def _job(engine, **kwargs) -> Job:
    with Session(engine) as session:
        if "server_id" not in kwargs:
            server = Server(name="pi", hostname="pi.local", ssh_username="pi", os_type="debian")
            session.add(server)
            session.commit()
            session.refresh(server)
            kwargs["server_id"] = server.id
        row = Job(
            server_id=kwargs.get("server_id"),
            job_type=kwargs.get("job_type", "os_patch"),
            status=kwargs.get("status", "pending"),
            details=kwargs.get("details"),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        session.expunge(row)
        return row


def test_host_wait_helpers(tmp_path, monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    assert ex.clamp_host_wait_sec("nope") == ex.DEFAULT_HOST_WAIT_SEC
    assert ex.clamp_host_wait_sec(1) == 30
    assert ex.clamp_host_wait_sec(10**9) == ex._HOST_WAIT_CEILING_SEC
    monkeypatch.setenv("PIHERDER_EXCLUSIVE_HOST_WAIT_SEC", "90")
    assert ex.host_wait_env_locked() is True
    assert ex.host_wait_limit_sec() == 90
    monkeypatch.delenv("PIHERDER_EXCLUSIVE_HOST_WAIT_SEC")

    def _unreadable():
        raise RuntimeError("db")

    monkeypatch.setattr("app.services.app_settings._load_raw_from_db", _unreadable)
    assert ex.host_wait_limit_sec() == ex.DEFAULT_HOST_WAIT_SEC

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("PIHERDER_EXCLUSIVE_INLINE", raising=False)
    assert ex.exclusive_runs_inline() is False
    monkeypatch.setenv("PIHERDER_EXCLUSIVE_INLINE", "yes")
    assert ex.exclusive_runs_inline() is True

    assert ex.host_down_signal(Session(engine), None) is None
    with Session(engine) as session:
        server = Server(
            name="pi",
            hostname="pi.local",
            ssh_username="pi",
            os_type="debian",
            last_seen=datetime.utcnow() - timedelta(hours=1),
        )
        session.add(server)
        session.commit()
        session.refresh(server)
        assert ex.host_down_signal(session, server) == "last_seen"
        note = Notification(
            server_id=server.id,
            type="host_down",
            title="down",
            fingerprint="host-down-1",
            status="open",
        )
        session.add(note)
        session.commit()
        assert ex.host_down_signal(session, server) == "kuma"
        fresh = Server(name="up", hostname="up.local", ssh_username="pi", os_type="debian")
        session.add(fresh)
        session.commit()
        session.refresh(fresh)
        assert ex.host_down_signal(session, fresh) is None
        sid = server.id

    pending = SimpleNamespace(status="pending", job_type="os_patch", server_id=None)
    assert ex.host_wait_display(pending, {"current": "waiting_for_host"}) == ("waiting on host", "ssh")
    assert ex.host_wait_display(SimpleNamespace(status="running", job_type="os_patch"), {}) == (None, None)
    assert ex.host_wait_display(SimpleNamespace(status="pending", job_type="backup", server_id=sid), {}) == (
        None,
        None,
    )
    assert ex.host_wait_display(SimpleNamespace(status="pending", job_type="os_patch", server_id=None), {}) == (
        None,
        None,
    )
    label = ex.host_wait_display(SimpleNamespace(status="pending", job_type="os_patch", server_id=sid), {})
    assert label[0] == "waiting on host"

    def _boom():
        raise RuntimeError("session")

    monkeypatch.setattr(_js, "_get_fresh_session", _boom)
    assert ex.host_wait_display(
        SimpleNamespace(status="pending", job_type="os_patch", server_id=sid), {}
    ) == (None, None)
    assert ex._load_details(SimpleNamespace(details="[")) == {}
    assert ex._load_details(SimpleNamespace(details="1")) == {}


def test_handoff_and_enqueue_edges(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    job = _job(engine, job_type="os_patch", status="pending")
    monkeypatch.setattr(ex, "exclusive_runs_inline", lambda: False)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    monkeypatch.setattr(_js, "_finish_demo_job", lambda session, row: setattr(row, "status", "success"))
    ex.handoff_exclusive(
        job_id=job.id,
        server_id=job.server_id,
        audit_id=1,
        job_type="os_patch",
        payload={},
    )

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(_js, "HAS_CELERY", False)
    monkeypatch.setattr(_js, "exclusive_job_task", None)
    with pytest.raises(RuntimeError):
        ex.handoff_exclusive(
            job_id=job.id,
            server_id=job.server_id,
            audit_id=1,
            job_type="os_patch",
        )

    class _Delay:
        id = "celery-1"

        def delay(self, *args):
            if args[3] == "os_update_check":
                raise RuntimeError("broker")
            return self

    monkeypatch.setattr(_js, "HAS_CELERY", True)
    monkeypatch.setattr(_js, "exclusive_job_task", _Delay())
    ex.handoff_exclusive(
        job_id=job.id,
        server_id=job.server_id,
        audit_id=1,
        job_type="container_update_check",
        payload={"n": 1},
    )
    with pytest.raises(RuntimeError):
        ex.handoff_exclusive(
            job_id=job.id,
            server_id=job.server_id,
            audit_id=1,
            job_type="os_update_check",
        )

    monkeypatch.setattr(ex, "exclusive_runs_inline", lambda: True)
    tasks = SimpleNamespace(added=[], add_task=lambda fn, *args: None)
    ex.handoff_exclusive(
        job_id=job.id,
        server_id=job.server_id,
        audit_id=1,
        job_type="os_patch",
        background_tasks=tasks,
        bg_fn=lambda: None,
        bg_args=(),
    )
    pool = SimpleNamespace(submitted=[])
    pool.submit = lambda fn, *args: pool.submitted.append(fn)
    ex.handoff_exclusive(
        job_id=job.id,
        server_id=job.server_id,
        audit_id=1,
        job_type="os_patch",
        pool=pool,
        sync_fn=lambda: None,
    )
    with pytest.raises(RuntimeError):
        ex.handoff_exclusive(
            job_id=job.id,
            server_id=job.server_id,
            audit_id=1,
            job_type="os_patch",
        )


def test_run_exclusive_job_branches(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    task = SimpleNamespace(request=SimpleNamespace(id="t-1"), retry=lambda **k: (_ for _ in ()).throw(Retry()))
    assert ex.run_exclusive_job(task, 999, 1, 1, "os_patch", {})["status"] == "skipped"

    other = _job(engine, job_type="backup", status="pending")
    assert ex.run_exclusive_job(task, other.id, other.server_id, 1, "backup", {})["reason"] == "backup"

    cancelled = _job(engine, job_type="os_patch", status="cancelled")
    assert ex.run_exclusive_job(task, cancelled.id, cancelled.server_id, 1, "os_patch", {})["status"] == "cancelled"

    done = _job(engine, job_type="os_patch", status="success")
    assert ex.run_exclusive_job(task, done.id, done.server_id, 1, "os_patch", {})["reason"] == "success"

    running = _job(engine, job_type="os_patch", status="running")
    monkeypatch.setattr(ex, "_fail_running_redelivery", lambda *a, **k: None)
    assert ex.run_exclusive_job(task, running.id, running.server_id, 1, "os_patch", {})["reason"] == "worker_restart"

    pending = _job(engine, job_type="container_patch", status="pending")
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    monkeypatch.setattr(_js, "_finish_demo_job", lambda session, row: None)
    assert ex.run_exclusive_job(task, pending.id, pending.server_id, 1, "container_patch", {})["status"] == "demo"

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    real_ssh_ready = ex._ssh_ready
    monkeypatch.setattr(ex, "_ssh_ready", lambda *_a, **_k: False)
    monkeypatch.setattr(ex, "_wait_for_host", lambda *a, **k: {"status": "waiting"})
    assert ex.run_exclusive_job(task, pending.id, pending.server_id, 1, "container_patch", {})["status"] == "waiting"

    monkeypatch.setattr(ex, "_ssh_ready", lambda *_a, **_k: True)
    seen = []
    real_execute = ex._execute
    monkeypatch.setattr(ex, "_execute", lambda *a, **k: seen.append(a[0]))
    assert ex.run_exclusive_job(task, pending.id, pending.server_id, 1, "container_patch", {"x": 1})["status"] == "ok"
    assert seen == ["container_patch"]

    def _max(*_a, **_k):
        raise MaxRetriesExceededError()

    monkeypatch.setattr(ex, "_ssh_ready", _max)
    monkeypatch.setattr(ex, "_fail_wait_exceeded", lambda *a, **k: None)
    assert ex.run_exclusive_job(task, pending.id, pending.server_id, 1, "container_patch", {})["reason"] == "host_wait"

    def _boom(*_a, **_k):
        raise RuntimeError("explode")

    monkeypatch.setattr(ex, "_ssh_ready", _boom)
    monkeypatch.setattr(ex, "_fail_unexpected", lambda *a, **k: None)
    assert ex.run_exclusive_job(task, pending.id, pending.server_id, 1, "container_patch", {})["status"] == "error"

    calls = []

    def _record(name):
        def _fn(*_a, **_k):
            calls.append(name)

        return _fn

    for name in (
        "_execute_os_patch_sync",
        "_execute_container_patch_sync",
        "_execute_os_update_check",
        "_execute_container_update_check",
        "_execute_docker_stack_check",
        "_execute_docker_stack_deploy",
        "_execute_docker_stack_lifecycle",
        "_execute_docker_stack_remove",
        "_execute_template_deploy",
        "_execute_template_redeploy",
        "_execute_template_drift_check",
    ):
        monkeypatch.setattr(_js, name, _record(name))
    payload = {
        "project_path": "/home/pi/docker/web",
        "pull": False,
        "compose_files": ["compose.yml"],
        "action": "restart",
        "template_slug": "web",
        "deployment_id": 4,
        "deploy_now": False,
        "os_steps": ["apt"],
    }
    for kind in (
        "os_patch",
        "container_patch",
        "os_update_check",
        "container_update_check",
        "docker_stack_check",
        "docker_stack_deploy",
        "docker_stack_restart",
        "docker_stack_remove",
        "template_deploy",
        "template_redeploy",
        "template_drift_check",
    ):
        real_execute(kind, pending.id, pending.server_id, 1, payload)
    assert len(calls) == 11
    with pytest.raises(ValueError):
        real_execute("backup", pending.id, pending.server_id, 1, {})

    monkeypatch.setattr(ex, "_ssh_ready", real_ssh_ready)
    monkeypatch.setattr("app.services.ssh.test_connection", lambda *_a, **_k: (_ for _ in ()).throw(OSError("no")))
    assert ex._ssh_ready(pending.server_id) is False
    monkeypatch.setattr("app.services.ssh.test_connection", lambda *_a, **_k: True)
    assert ex._ssh_ready(pending.server_id) is True
    assert ex._ssh_ready(99999) is False


def test_wait_for_host_and_fail_helpers(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    monkeypatch.setattr(ex, "host_wait_limit_sec", lambda: 30)
    finished = []
    monkeypatch.setattr(_js, "_finish", lambda *a, **k: finished.append(a))
    monkeypatch.setattr(_js, "_mark_job_terminal", lambda *a, **k: None)
    monkeypatch.setattr(_js, "_finish_running_audit_for_job", lambda *a, **k: None)
    monkeypatch.setattr(_js, "_merge_job_details", lambda job, **k: job)

    class _Task:
        def retry(self, countdown=None):
            raise Retry(message=str(countdown))

    missing = _job(engine, job_type="os_patch", status="pending")
    with Session(engine) as session:
        row = session.get(Job, missing.id)
        row.server_id = None
        session.add(row)
        session.commit()
    assert ex._wait_for_host(_Task(), missing.id, 1, "os_patch")["reason"] == "no_server"

    gone = _job(engine, job_type="os_patch", status="success")
    assert ex._wait_for_host(_Task(), gone.id, 1, "os_patch")["status"] == "skipped"
    running = _job(engine, job_type="os_patch", status="running")
    assert ex._wait_for_host(_Task(), running.id, 1, "os_patch")["reason"] == "running"

    waiting = _job(engine, job_type="os_patch", status="pending", details="{}")
    with pytest.raises(Retry):
        ex._wait_for_host(_Task(), waiting.id, 1, "os_patch")
    with Session(engine) as session:
        row = session.get(Job, waiting.id)
        row.details = '{"host_wait_started": "not-a-date"}'
        session.add(row)
        session.commit()
    with pytest.raises(Retry):
        ex._wait_for_host(_Task(), waiting.id, 1, "os_patch")
    with Session(engine) as session:
        row = session.get(Job, waiting.id)
        old = (datetime.utcnow() - timedelta(hours=2)).isoformat()
        row.details = '{"host_wait_started": "%s"}' % old
        session.add(row)
        session.commit()
    assert ex._wait_for_host(_Task(), waiting.id, 1, "os_patch")["reason"] == "host_wait"

    ex._fail_running_redelivery(waiting.id, 1, "os_patch")
    ex._fail_wait_exceeded(waiting.id, 1, "os_patch")
    ex._fail_unexpected(waiting.id, 1, "os_patch", "boom")
    ex._fail_unexpected(999, 1, "os_patch", "missing")
    assert finished


def test_parse_verify_endpoints():
    from app.services.certificates import parse_verify_endpoint

    assert parse_verify_endpoint("  ") is None
    assert parse_verify_endpoint("https://app.example.com/")["port"] == 443
    assert parse_verify_endpoint("app.example.com:8443")["port"] == 8443
    pg = parse_verify_endpoint("postgres://db.local:5432")
    assert pg["starttls"] == "postgres" and pg["port"] == 5432
    assert parse_verify_endpoint("mysql://db.local")["starttls"] == "mysql"
    assert parse_verify_endpoint("tls://10.0.0.5:636")["port"] == 636
    forced = parse_verify_endpoint("host:5432?starttls=postgres&sni=db.local")
    assert forced["starttls"] == "postgres" and forced["servername"] == "db.local"
    ip = parse_verify_endpoint("10.0.0.5:443?sni=app.example.com")
    assert ip["servername"] == "app.example.com"
    v6 = parse_verify_endpoint("[fe80::1]:5432")
    assert v6["host"] == "fe80::1" and v6["port"] == 5432
    assert parse_verify_endpoint("[fe80::1]")["port"] == 443
    assert parse_verify_endpoint("[nope") is None
    assert parse_verify_endpoint("example.com")["port"] == 443
    assert parse_verify_endpoint("http://plain.example/")["port"] == 80
    assert parse_verify_endpoint("weird://db.local:9")["port"] == 9
    assert parse_verify_endpoint("https://") is None
    aliased = parse_verify_endpoint("https://db.local?starttls=postgresql")
    assert aliased["starttls"] == "postgres"

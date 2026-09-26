"""host_reboot job: token allowlist, exclusive lane, no live SSH."""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import ApiToken, AuditLog, Job, Server, User
from app.security.auth import get_password_hash
from app.services import api_tokens as tok
from app.services import jobs as jobs_mod
from app.services.host_reboot import send_deferred_reboot
from app.services.jobs_exclusive import EXCLUSIVE_CELERY_TYPES


def _memory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


def _server(session, **kw):
    row = Server(
        name=kw.get("name", "pi"),
        hostname="pi.local",
        ssh_username="pi",
        os_patch_enabled=kw.get("os_patch_enabled", True),
        container_patch_enabled=True,
        backup_enabled=True,
        reboot_pending=kw.get("reboot_pending", False),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _job(session, server, job_type, status="pending"):
    row = Job(
        server_id=server.id,
        job_type=job_type,
        status=status,
        details="{}",
        created_at=datetime.utcnow(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


class _Chan:
    def __init__(self, ready=True, code=0):
        self._ready = ready
        self._code = code

    def exit_status_ready(self):
        return self._ready

    def recv_exit_status(self):
        return self._code


class _Stream:
    def __init__(self, data=b"", ready=True, code=0):
        self.channel = _Chan(ready=ready, code=code)
        self._data = data

    def read(self):
        return self._data


class _Client:
    def __init__(self, code=0, drop=False):
        self.code = code
        self.drop = drop
        self.cmds = []

    def exec_command(self, cmd, timeout=8):
        self.cmds.append(cmd)
        if self.drop:
            raise OSError("connection reset by peer")
        return None, _Stream(code=self.code), _Stream()


def test_host_reboot_is_os_feature_on_celery():
    assert tok.JOB_FEATURE_KEY["host_reboot"] == "os"
    assert "host_reboot" in EXCLUSIVE_CELERY_TYPES
    assert "host_reboot" in jobs_mod._EXCLUSIVE_JOB_TYPES


def test_send_deferred_reboot_accepts_and_drop():
    ok = _Client(code=0)
    success, details = send_deferred_reboot(ok)
    assert success is True
    assert ok.cmds
    assert "reboot" in details.lower() or "sent" in details.lower() or "scheduled" in details.lower()

    dropped = _Client(drop=True)
    success, details = send_deferred_reboot(dropped)
    assert success is True
    assert "closed" in details.lower() or "sent" in details.lower()

    failed = _Client(code=1)
    failed_stream = _Stream(data=b"denied", code=1)

    class _Fail:
        def exec_command(self, cmd, timeout=8):
            return None, failed_stream, _Stream(data=b"sudo: denied")

    success, details = send_deferred_reboot(_Fail())
    assert success is False
    assert "failed" in details.lower()


def test_reboot_blocked_by_patch_and_backup(monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    session, _engine = _memory()
    srv = _server(session)
    _job(session, srv, "os_patch", status="running")
    with pytest.raises(jobs_mod.JobAlreadyActive) as caught:
        jobs_mod.create_job_and_run(BackgroundTasks(), session, srv, "host_reboot")
    assert caught.value.job.job_type == "os_patch"

    session2, _engine2 = _memory()
    srv2 = _server(session2)
    _job(session2, srv2, "backup", status="pending")
    with pytest.raises(jobs_mod.JobAlreadyActive) as caught2:
        jobs_mod.create_job_and_run(BackgroundTasks(), session2, srv2, "host_reboot")
    assert caught2.value.job.job_type == "backup"


def test_patch_and_backup_blocked_by_reboot(monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    session, engine = _memory()
    monkeypatch.setattr(jobs_mod, "engine", engine)
    srv = _server(session)
    _job(session, srv, "host_reboot", status="pending")
    with pytest.raises(jobs_mod.JobAlreadyActive):
        jobs_mod.create_job_and_run(BackgroundTasks(), session, srv, "os_patch")
    with pytest.raises(jobs_mod.JobAlreadyActive):
        jobs_mod.create_job_and_run(BackgroundTasks(), session, srv, "container_patch")
    with pytest.raises(jobs_mod.JobAlreadyActive):
        jobs_mod.create_job_and_run(BackgroundTasks(), session, srv, "backup")
    assert jobs_mod.enqueue_os_patch_apply(srv.id) is None
    assert jobs_mod.enqueue_container_patch_apply(srv.id) is None
    with pytest.raises(jobs_mod.JobAlreadyActive):
        jobs_mod.enqueue_backup_for_server(session, srv)


def test_demo_host_reboot_does_not_ssh(monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("ssh")

    monkeypatch.setattr("app.services.host_reboot.send_deferred_reboot", boom)
    session, _engine = _memory()
    srv = _server(session, reboot_pending=True)
    job = jobs_mod.create_job_and_run(
        BackgroundTasks(), session, srv, "host_reboot", user_id=1
    )
    assert job.status == "success"
    assert called["n"] == 0
    details = job.details or ""
    assert "Demo simulation" in details


def test_execute_host_reboot_uses_deferred_commands(monkeypatch):
    session, engine = _memory()
    monkeypatch.setattr(jobs_mod, "engine", engine)
    monkeypatch.setattr(jobs_mod, "_send_summary_webhook", lambda *_a, **_k: None)
    srv = _server(session, reboot_pending=True)
    job = Job(server_id=srv.id, job_type="host_reboot", status="pending", details="{}")
    session.add(job)
    session.commit()
    session.refresh(job)
    audit = AuditLog(
        server_id=srv.id,
        action="host_reboot",
        status="running",
        details=f"Job #{job.id} started",
    )
    session.add(audit)
    session.commit()
    session.refresh(audit)
    monkeypatch.setattr("app.services.ssh.get_ssh_client", lambda _server: object())
    seen = {}

    def _send(_client):
        seen["called"] = True
        return True, "Reboot command sent"

    monkeypatch.setattr("app.services.host_reboot.send_deferred_reboot", _send)
    jobs_mod._execute_host_reboot(job.id, srv.id, audit.id)
    session.expire_all()
    assert seen.get("called") is True
    assert session.get(Job, job.id).status == "success"
    assert session.get(Server, srv.id).reboot_pending is False


def _api_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'reboot.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _api_client(engine):
    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return TestClient(app, raise_server_exceptions=False)


def _token(session, user_id, plain, scopes):
    session.add(
        ApiToken(
            name=plain[:8],
            token_prefix=plain[:12],
            token_hash=tok.hash_token(plain),
            scopes=scopes,
            created_by_user_id=user_id,
        )
    )
    session.commit()


def test_api_host_reboot_gates(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_mod, "handoff_exclusive", lambda **_k: None)
    engine = _api_engine(tmp_path)
    client = _api_client(engine)
    try:
        with Session(engine) as session:
            user = User(
                email="reboot@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            off = _server(session, name="off", os_patch_enabled=False)
            on = _server(session, name="on", os_patch_enabled=True)
            _token(session, user.id, "ph_readtokenvalue0000000000000000000", "read")
            _token(session, user.id, "ph_jobstokenvalue000000000000000000", "read,jobs")
            sid_off, sid_on = off.id, on.id
        read_h = {"Authorization": "Bearer ph_readtokenvalue0000000000000000000"}
        jobs_h = {"Authorization": "Bearer ph_jobstokenvalue000000000000000000"}
        denied = client.post(
            f"/api/v1/servers/{sid_on}/jobs",
            headers=read_h,
            json={"job_type": "host_reboot"},
        )
        assert denied.status_code == 403
        disabled = client.post(
            f"/api/v1/servers/{sid_off}/jobs",
            headers=jobs_h,
            json={"job_type": "host_reboot"},
        )
        assert disabled.status_code == 400
        ok = client.post(
            f"/api/v1/servers/{sid_on}/jobs",
            headers=jobs_h,
            json={"job_type": "host_reboot"},
        )
        assert ok.status_code == 202
        assert ok.json()["job_type"] == "host_reboot"
        with Session(engine) as session:
            session.add(
                Job(
                    server_id=sid_on,
                    job_type="container_patch",
                    status="running",
                    details="{}",
                )
            )
            session.commit()
        busy = client.post(
            f"/api/v1/servers/{sid_on}/jobs",
            headers=jobs_h,
            json={"job_type": "host_reboot"},
        )
        assert busy.status_code == 409
        body = busy.json()
        assert body.get("already_active") is True
        assert body["job"]["job_type"] == "container_patch"
    finally:
        app.dependency_overrides.clear()

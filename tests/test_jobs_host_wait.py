"""Jr-2: Settings max wait, and Kuma/last_seen as a label only."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from celery.exceptions import Retry
from sqlmodel import Session, SQLModel, create_engine

from app.models import Integration, IntegrationBinding, Job, Notification, Server
from app.services import jobs as js
from app.services.jobs_exclusive import (
    host_wait_limit_sec,
    run_exclusive_job,
)


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _bind(monkeypatch, engine):
    monkeypatch.setattr(js, "engine", engine)
    monkeypatch.delenv("PIHERDER_EXCLUSIVE_HOST_WAIT_SEC", raising=False)

    @contextmanager
    def _fresh():
        session = Session(engine, expire_on_commit=False)
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(js, "_get_fresh_session", _fresh)


def _server(engine, **extra) -> Server:
    with Session(engine) as session:
        srv = Server(name="pi", hostname="pi.local", ssh_port=22, **extra)
        session.add(srv)
        session.commit()
        session.refresh(srv)
        session.expunge(srv)
        return srv


class _Task:
    def __init__(self):
        self.request = SimpleNamespace(id="wait-1")

    def retry(self, countdown=None):
        raise Retry(message="wait")


def test_settings_value_is_the_wait_unless_env_locks(monkeypatch):
    monkeypatch.delenv("PIHERDER_EXCLUSIVE_HOST_WAIT_SEC", raising=False)
    monkeypatch.setattr(
        "app.services.app_settings._load_raw_from_db",
        lambda: {"exclusive_host_wait_sec": 120},
    )
    assert host_wait_limit_sec() == 120
    monkeypatch.setenv("PIHERDER_EXCLUSIVE_HOST_WAIT_SEC", "90")
    assert host_wait_limit_sec() == 90


def test_kuma_down_labels_pending_job_and_does_not_write_it(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    srv = _server(engine, last_seen=datetime.utcnow())
    with Session(engine) as session:
        integ = Integration(type="uptime_kuma", name="kuma", base_url="http://kuma")
        session.add(integ)
        session.commit()
        session.refresh(integ)
        session.add(
            IntegrationBinding(
                integration_id=integ.id,
                server_id=srv.id,
                role="ssh_reachability",
                external_id="1",
                last_state="down",
            )
        )
        job = Job(
            server_id=srv.id,
            job_type="os_patch",
            status="pending",
            details='{"current":"queued"}',
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.id
    pub = js.job_public_dict(session.get(Job, jid) if False else _reload(engine, jid))
    assert pub["status"] == "pending"
    assert pub["current"] == "waiting on host"
    assert pub["host_signal"] == "kuma"
    with Session(engine) as session:
        row = session.get(Job, jid)
        assert row.status == "pending"
        assert "waiting_for_host" not in (row.details or "")


def test_stale_last_seen_labels_without_kuma(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    srv = _server(engine, last_seen=datetime.utcnow() - timedelta(hours=1))
    with Session(engine) as session:
        job = Job(
            server_id=srv.id,
            job_type="container_patch",
            status="pending",
            details='{"current":"queued"}',
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.id
    pub = js.job_public_dict(_reload(engine, jid))
    assert pub["host_signal"] == "last_seen"
    assert pub["current"] == "waiting on host"
    assert pub["status"] == "pending"


def test_fresh_host_has_no_signal(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    srv = _server(engine, last_seen=datetime.utcnow())
    with Session(engine) as session:
        job = Job(
            server_id=srv.id,
            job_type="os_update_check",
            status="pending",
            details='{"current":"queued"}',
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.id
    pub = js.job_public_dict(_reload(engine, jid))
    assert pub.get("host_signal") is None
    assert pub["current"] == "queued"


def test_open_host_down_notification_is_a_kuma_signal(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    srv = _server(engine, last_seen=datetime.utcnow())
    with Session(engine) as session:
        session.add(
            Notification(
                server_id=srv.id,
                type="host_down",
                status="open",
                title="down",
                fingerprint="kuma_down:test",
            )
        )
        job = Job(
            server_id=srv.id,
            job_type="os_patch",
            status="pending",
            details="{}",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.id
    pub = js.job_public_dict(_reload(engine, jid))
    assert pub["host_signal"] == "kuma"


def test_ssh_up_runs_even_when_kuma_says_down(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    srv = _server(engine)
    with Session(engine) as session:
        integ = Integration(type="uptime_kuma", name="kuma", base_url="http://kuma")
        session.add(integ)
        session.commit()
        session.refresh(integ)
        session.add(
            IntegrationBinding(
                integration_id=integ.id,
                server_id=srv.id,
                role="ssh_reachability",
                external_id="1",
                last_state="down",
            )
        )
        job = Job(server_id=srv.id, job_type="os_patch", status="pending", details="{}")
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.id
    called = {}
    monkeypatch.setattr("app.services.ssh.test_connection", lambda *a, **k: True)
    monkeypatch.setattr(
        js,
        "_execute_os_patch_sync",
        lambda *a, **k: called.setdefault("ran", True),
    )
    result = run_exclusive_job(_Task(), jid, srv.id, 1, "os_patch", {})
    assert result["status"] == "ok"
    assert called.get("ran") is True
    with Session(engine) as session:
        assert session.get(Job, jid).status == "pending"


def test_ssh_down_still_waits_when_last_seen_is_fresh(monkeypatch):
    engine = _engine()
    _bind(monkeypatch, engine)
    srv = _server(engine, last_seen=datetime.utcnow())
    with Session(engine) as session:
        job = Job(server_id=srv.id, job_type="os_patch", status="pending", details="{}")
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.id
    monkeypatch.setattr("app.services.ssh.test_connection", lambda *a, **k: False)
    with pytest.raises(Retry):
        run_exclusive_job(_Task(), jid, srv.id, 1, "os_patch", {})
    with Session(engine) as session:
        row = session.get(Job, jid)
        assert row.status == "pending"
        assert "waiting_for_host" in (row.details or "")


def _reload(engine, job_id: int) -> Job:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        session.expunge(job)
        return job

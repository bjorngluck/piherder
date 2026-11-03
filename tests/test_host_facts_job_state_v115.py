"""Down-host facts backoff, probe window, and job summary state."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.celery_app import celery
from app.models import Job, Server
from app.services import host_facts as facts
from app.services import jobs as js
from app.services.api_summary import fleet_summary


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_probe_window_matches_the_task_time_limit():
    assert facts.HOST_FACTS_PROBE_SEC == int(celery.conf.task_time_limit)


def test_down_host_is_not_requeued_inside_the_probe_window(monkeypatch):
    from app.services import scheduler as sched

    engine = _engine()
    with Session(engine) as session:
        server = Server(
            name="host-a",
            hostname="host-a.example.com",
            ip_address="192.0.2.10",
            host_facts_status="error",
            host_facts_error="Host did not respond",
        )
        session.add(server)
        session.commit()
        session.refresh(server)
        session.add(
            Job(
                server_id=server.id,
                job_type="host_facts",
                status="failed",
                details="{}",
                created_at=datetime.utcnow() - timedelta(minutes=5),
                finished_at=datetime.utcnow() - timedelta(minutes=5),
            )
        )
        session.commit()
        assert facts.down_host_backoff(session, server) is True
        later = datetime.utcnow() + timedelta(seconds=facts.HOST_FACTS_PROBE_SEC + 5)
        assert facts.down_host_backoff(session, server, now=later) is False

    monkeypatch.setattr("app.database.get_engine", lambda: engine)
    queued: list[int] = []
    monkeypatch.setattr(
        "app.services.host_facts.request_refresh",
        lambda sid, force=False: queued.append(sid),
    )
    sched.schedule_host_facts_fleet()
    sched.schedule_host_facts_fleet()
    assert queued == []


def test_failed_job_does_not_keep_a_success_summary():
    deploy = js._human_job_summary(
        "docker_stack_deploy",
        "failed",
        json.dumps({"project": "app", "success": True}),
    )
    assert "deploy ok" not in deploy
    assert "failed" in deploy
    patch = js._human_job_summary(
        "os_patch",
        "failed",
        json.dumps({"summary": "all packages updated", "success": True}),
    )
    assert "all packages updated" not in patch
    assert patch == "failed"
    drift = js._human_job_summary(
        "template_drift_check",
        "failed",
        json.dumps({"project_name": "app", "drift_status": "in_sync", "success": True}),
    )
    assert "in sync" not in drift


def test_pending_jobs_are_not_counted_as_running():
    out = fleet_summary(
        [],
        [
            type("J", (), {"status": "pending", "job_type": "backup"})(),
            type("J", (), {"status": "running", "job_type": "os_patch"})(),
        ],
    )
    assert out["jobs_running"] == 1

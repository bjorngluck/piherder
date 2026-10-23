"""Stuck queue alert, read-only alerts, and inventory update_available."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_engine, get_session
from app.main import app
from app.models import Notification, Server, User
from app.security.auth import get_password_hash
from app.services import api_tokens as tok_svc
from app.services import docker_inventory as inv
from app.services import queue_watch as qw
from app.services.mcp_hosted import tool_catalog


def test_stuck_queue_state_needs_age_and_a_dead_worker():
    assert qw.stuck_queue_state(60, False) is False
    assert qw.stuck_queue_state(20 * 60, True) is False
    assert qw.stuck_queue_state(20 * 60, None) is None
    assert qw.stuck_queue_state(20 * 60, False) is True


def test_oldest_pending_age_reads_the_created_time():
    created = datetime.utcnow() - timedelta(minutes=20)

    class _Query:
        def first(self):
            return created

    session = SimpleNamespace(exec=lambda _query: _Query())
    assert qw.oldest_pending_age_seconds(session) >= 19 * 60


def test_sync_opens_one_alert_and_resolves_when_the_queue_drains():
    with Session(get_engine()) as session:
        qw.sync_stuck_queue_alert(session, oldest_seconds=0, heartbeat=False)
        assert qw.sync_stuck_queue_alert(session, oldest_seconds=20 * 60, heartbeat=False) is True
        assert qw.sync_stuck_queue_alert(session, oldest_seconds=20 * 60, heartbeat=False) is True
        open_rows = session.exec(
            select(Notification).where(
                Notification.fingerprint == qw.STUCK_QUEUE_FINGERPRINT,
                Notification.status == "open",
            )
        ).all()
        assert len(open_rows) == 1
        assert open_rows[0].type == "stuck_queue"
        assert qw.sync_stuck_queue_alert(session, oldest_seconds=20 * 60, heartbeat=None) is None
        still = session.exec(
            select(Notification).where(
                Notification.fingerprint == qw.STUCK_QUEUE_FINGERPRINT,
                Notification.status == "open",
            )
        ).all()
        assert len(still) == 1
        assert qw.sync_stuck_queue_alert(session, oldest_seconds=30, heartbeat=False) is False
        cleared = session.exec(
            select(Notification).where(
                Notification.fingerprint == qw.STUCK_QUEUE_FINGERPRINT,
                Notification.status == "open",
            )
        ).all()
        assert cleared == []


def test_queue_gauges(monkeypatch):
    monkeypatch.setattr(qw, "oldest_pending_age_seconds", lambda _session: 1000)
    monkeypatch.setattr(qw, "workers_alive", lambda: False)
    assert qw.queue_gauges(None) == (1000, 1)
    monkeypatch.setattr(qw, "workers_alive", lambda: True)
    assert qw.queue_gauges(None) == (1000, 0)
    monkeypatch.setattr(qw, "oldest_pending_age_seconds", lambda _session: 10)
    assert qw.queue_gauges(None) == (10, 0)


def test_list_alerts_is_a_read_tool():
    names = [tool["name"] for tool in tool_catalog()]
    assert names[names.index("get_job") + 1] == "list_alerts"


def test_inventory_names_project_and_image_updates():
    server = Server(
        name="Lab",
        hostname="lab.example.com",
        ssh_username="pi",
        docker_inventory_json=json.dumps(
            {
                "v": 2,
                "projects": [
                    {
                        "name": "web",
                        "path": "/opt/web",
                        "has_pending_update": True,
                        "containers": [
                            {
                                "name": "web",
                                "image": "nginx:1",
                                "running": True,
                                "has_pending_update": True,
                            },
                            {
                                "name": "web-2",
                                "image": "nginx:1",
                                "running": True,
                                "has_pending_update": False,
                            },
                            {"name": "db", "image": "postgres:16", "running": True},
                        ],
                    }
                ],
                "orphan_containers": [
                    {"name": "extra", "image": "redis:7", "has_pending_update": True}
                ],
            }
        ),
    )
    projects = inv.snapshot_update_targets(server)
    assert projects[0]["name"] == "web"
    assert projects[0]["update_available"] is True
    images = {row["image"]: row["update_available"] for row in projects[0]["images"]}
    assert images == {"nginx:1": True, "postgres:16": False}
    assert projects[1]["images"][0]["update_available"] is True
    containers = inv.snapshot_containers(server)
    assert containers[0]["update_available"] is True


def test_alerts_http_requires_read(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'alerts.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with Session(engine) as session:
            user = User(
                email="alerts-v113@example.com",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            session.add(user)
            session.add(
                Notification(
                    type="backup_failed",
                    severity="critical",
                    title="Backup failed",
                    body="host-a",
                    fingerprint="backup-failed-host-a",
                    status="open",
                    server_id=None,
                )
            )
            session.add(
                Notification(
                    type="os_updates",
                    title="Closed",
                    fingerprint="os-updates-closed",
                    status="resolved",
                )
            )
            session.commit()
            session.refresh(user)
            _row, plain = tok_svc.create_api_token(
                session, name="read", created_by=user, scopes=["read"]
            )
            _jobs, jobs_plain = tok_svc.create_api_token(
                session, name="jobs", created_by=user, scopes=["jobs"]
            )
        denied = client.get(
            "/api/v1/alerts", headers={"Authorization": f"Bearer {jobs_plain}"}
        )
        assert denied.status_code == 403
        allowed = client.get(
            "/api/v1/alerts", headers={"Authorization": f"Bearer {plain}"}
        )
        assert allowed.status_code == 200
        body = allowed.json()
        assert body["total"] == 1
        assert body["alerts"][0]["title"] == "Backup failed"
        assert body["alerts"][0]["type"] == "backup_failed"
        assert "token" not in json.dumps(body["alerts"])
    finally:
        app.dependency_overrides.clear()

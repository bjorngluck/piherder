"""Unreachable hosts and two missed backups alert once."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Job, Notification, Server
from app.routers.api_v1 import _host_posture_fields
from app.services import host_posture as posture
from app.services import push as push_svc


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _utc(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)


NOW = _utc(2026, 10, 19, 2, 20)


def test_posture_states_and_duration():
    host = Server(
        id=1,
        name="host-a",
        hostname="host-a.example.com",
        last_seen=NOW - timedelta(minutes=5),
        created_at=datetime(2026, 1, 1),
    )
    healthy = posture.host_posture(host, NOW)
    assert healthy["status"] == "healthy"
    assert healthy["duration_seconds"] == 300

    host.last_seen = NOW - timedelta(hours=2)
    degraded = posture.host_posture(host, NOW)
    assert degraded["status"] == "degraded"
    assert degraded["duration_seconds"] == 7200

    host.last_seen = NOW - timedelta(minutes=5)
    missed = posture.host_posture(
        host, NOW, missed_backups=1, missed_at=NOW - timedelta(hours=1)
    )
    assert missed["status"] == "degraded"

    host.last_seen = NOW - timedelta(hours=25)
    down = posture.host_posture(host, NOW)
    assert down["status"] == "unreachable"
    assert down["duration_seconds"] >= 25 * 3600

    fresh = Server(id=2, name="host-b", hostname="host-b.example.com", created_at=NOW)
    assert posture.host_posture(fresh, NOW)["status"] == "healthy"
    fields = _host_posture_fields(
        host,
        [{"type": "host_unreachable", "title": "down", "severity": "critical"}],
    )
    assert fields["host_status"] == "unreachable"


def test_unreachable_and_two_missed_backups_push_once(monkeypatch):
    sent: list[str] = []

    def _send(session, notification):
        sent.append(notification.type)
        return 1

    monkeypatch.setattr(push_svc, "send_for_notification", _send)
    engine = _engine()
    with Session(engine) as session:
        quiet = Server(
            name="host-a",
            hostname="host-a.example.com",
            last_seen=NOW - timedelta(hours=26),
            created_at=datetime(2026, 1, 1),
            backup_enabled=False,
        )
        missed = Server(
            name="host-b",
            hostname="host-b.example.com",
            last_seen=NOW - timedelta(minutes=5),
            created_at=datetime(2026, 1, 1),
            backup_enabled=True,
            backup_schedule="0 2 * * *",
        )
        session.add(quiet)
        session.add(missed)
        session.commit()
        session.refresh(quiet)
        session.refresh(missed)

        assert posture.sync_host_posture_alerts(session, now=NOW) == 2
        assert posture.sync_host_posture_alerts(session, now=NOW) == 2
        rows = list(session.exec(select(Notification).where(Notification.status == "open")).all())
        kinds = sorted(row.type for row in rows)
        assert kinds == ["backup_missed_repeat", "host_unreachable"]
        assert all(row.severity == "critical" for row in rows)
        assert sent == ["host_unreachable", "backup_missed_repeat"]

        session.add(
            Job(
                server_id=missed.id,
                job_type="backup",
                status="success",
                created_at=NOW - timedelta(minutes=10),
                started_at=NOW - timedelta(minutes=9),
                finished_at=NOW - timedelta(minutes=8),
            )
        )
        quiet.last_seen = NOW
        session.add(quiet)
        session.commit()
        assert posture.sync_host_posture_alerts(session, now=NOW) == 0
        open_rows = list(
            session.exec(select(Notification).where(Notification.status == "open")).all()
        )
        assert open_rows == []
        assert sent == ["host_unreachable", "backup_missed_repeat"]

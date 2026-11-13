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


def test_a_recent_answer_keeps_an_old_host_healthy():
    host = Server(
        id=3,
        name="host-a",
        hostname="host-a.example.com",
        created_at=datetime(2020, 1, 1),
        host_facts_at=NOW - timedelta(minutes=10),
    )
    assert posture.host_posture(host, NOW)["status"] == "healthy"


def test_quiet_time_starts_at_the_answer_not_the_added_date():
    host = Server(
        id=4,
        name="host-a",
        hostname="host-a.example.com",
        created_at=datetime(2020, 1, 1),
        docker_inventory_at=NOW - timedelta(hours=78),
    )
    down = posture.host_posture(host, NOW)
    assert down["status"] == "unreachable"
    assert 77 * 3600 <= down["duration_seconds"] <= 79 * 3600
    held = posture.host_posture(host, NOW, unreachable_after=timedelta(hours=96))
    assert held["status"] == "degraded"


def test_no_answer_yet_does_not_count_as_unreachable():
    host = Server(
        id=5,
        name="host-a",
        hostname="host-a.example.com",
        created_at=datetime(2020, 1, 1),
    )
    assert posture.host_posture(host, NOW)["status"] == "healthy"


def test_quiet_amount_accepts_hours_and_days():
    assert posture.quiet_hours_from_amount("2", "days") == 48
    assert posture.quiet_hours_from_amount("36", "hours") == 36
    assert posture.quiet_hours_from_amount("0", "hours") == 1
    assert posture.quiet_hours_from_amount("400", "days") == 24 * 90


def _policy(monkeypatch, payload: dict) -> None:
    from app.services import alert_policy as apol

    monkeypatch.setattr(apol.app_cfg, "load_settings", lambda: {"alert_type_policy": payload})


def test_recent_facts_do_not_open_an_alert(monkeypatch):
    monkeypatch.setattr(push_svc, "send_for_notification", lambda *_a, **_k: 0)
    _policy(monkeypatch, {})
    engine = _engine()
    with Session(engine) as session:
        session.add(
            Server(
                name="host-a",
                hostname="host-a.example.com",
                created_at=datetime(2020, 1, 1),
                host_facts_at=NOW - timedelta(minutes=10),
            )
        )
        session.commit()
        assert posture.sync_host_posture_alerts(session, now=NOW) == 0
        rows = list(session.exec(select(Notification).where(Notification.status == "open")).all())
        assert rows == []


def test_longer_quiet_time_holds_the_alert(monkeypatch):
    monkeypatch.setattr(push_svc, "send_for_notification", lambda *_a, **_k: 0)
    _policy(
        monkeypatch,
        {"types": {"host_unreachable": {"enabled": True, "quiet_hours": 96, "quiet_unit": "days"}}},
    )
    engine = _engine()
    with Session(engine) as session:
        session.add(
            Server(
                name="host-a",
                hostname="host-a.example.com",
                created_at=datetime(2020, 1, 1),
                host_facts_at=NOW - timedelta(hours=78),
                backup_enabled=False,
            )
        )
        session.commit()
        assert posture.sync_host_posture_alerts(session, now=NOW) == 0


def test_turning_the_alert_off_clears_open_cards(monkeypatch):
    monkeypatch.setattr(push_svc, "send_for_notification", lambda *_a, **_k: 0)
    _policy(monkeypatch, {"types": {"host_unreachable": {"enabled": True, "quiet_hours": 24}}})
    engine = _engine()
    with Session(engine) as session:
        session.add(
            Server(
                name="host-a",
                hostname="host-a.example.com",
                created_at=datetime(2020, 1, 1),
                host_facts_at=NOW - timedelta(hours=30),
                backup_enabled=False,
            )
        )
        session.commit()
        assert posture.sync_host_posture_alerts(session, now=NOW) == 1
        _policy(monkeypatch, {"types": {"host_unreachable": {"enabled": False, "quiet_hours": 24}}})
        assert posture.sync_host_posture_alerts(session, now=NOW) == 0
        rows = list(session.exec(select(Notification).where(Notification.status == "open")).all())
        assert rows == []


def test_settings_card_offers_hours_days_and_off():
    from starlette.requests import Request

    from app.templates import templates

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/herder-backups",
        "raw_path": b"/herder-backups",
        "query_string": b"tab=alerts",
        "headers": [],
        "client": ("127.0.0.1", 123),
        "server": ("testserver", 80),
    }
    html = templates.get_template("partials/settings_alerts.html").render(
        request=Request(scope),
        tab="alerts",
        is_admin=True,
        alert_policy_ui={"categories": []},
        host_unreachable={"enabled": True, "amount": 2, "unit": "days"},
        settings_hub={},
        webhook_cfg={},
        smtp_cfg={},
        mute_rules=[],
        mute_hosts=[],
        mute_types=[],
        alert_categories=[],
    )
    assert 'data-testid="host-unreachable-settings"' in html
    assert 'name="quiet_amount"' in html
    assert 'value="2"' in html
    assert "days" in html
    assert "Raise this alert" in html
    assert "The date the host was added does not." in html

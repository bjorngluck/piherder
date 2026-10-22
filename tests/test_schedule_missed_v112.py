"""Missed scheduled host backup and container check."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Job, Notification, Server
from app.services import alert_policy as apol
from app.services import schedule_watch as watch
from app.services.scheduler import sync_missed_schedule_watch


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
SLOT = _utc(2026, 10, 19, 2, 0)
BORN = datetime(2026, 1, 1, 0, 0, 0)


def test_previous_fire_finds_the_latest_slot():
    assert watch.previous_fire("0 2 * * *", "UTC", NOW) == SLOT
    assert watch.previous_fire("0 2 * * *", "UTC", SLOT) == SLOT
    every = watch.previous_fire("* * * * *", "UTC", _utc(2026, 10, 19, 2, 20, 30))
    assert every == _utc(2026, 10, 19, 2, 20)
    assert watch.previous_fire("not a cron", "UTC", NOW) is None


def test_due_slot_waits_out_the_grace_and_skips_a_new_host():
    still_early = _utc(2026, 10, 19, 2, 10)
    assert watch.due_slot("0 2 * * *", "UTC", still_early, BORN) == _utc(2026, 10, 18, 2, 0)
    assert watch.due_slot("0 2 * * *", "UTC", NOW, BORN) == SLOT
    assert watch.due_slot("0 2 * * *", "UTC", NOW, datetime(2026, 10, 19, 2, 5)) is None


def test_classify_slot_on_time_late_success_and_miss():
    on_time = SimpleNamespace(
        started_at=datetime(2026, 10, 19, 2, 5),
        finished_at=None,
        status="running",
    )
    late_ok = SimpleNamespace(
        started_at=datetime(2026, 10, 19, 2, 16),
        finished_at=datetime(2026, 10, 19, 2, 18),
        status="success",
    )
    late_fail = SimpleNamespace(
        started_at=datetime(2026, 10, 19, 2, 16),
        finished_at=datetime(2026, 10, 19, 2, 18),
        status="failed",
    )
    assert watch.classify_slot(SLOT, [on_time]) == "on_time"
    assert watch.classify_slot(SLOT, [late_ok]) == "succeeded"
    assert watch.classify_slot(SLOT, [late_fail]) == "missed"
    assert watch.classify_slot(SLOT, []) == "missed"


def test_hosts_queued_together_stay_on_time_when_workers_start_late(monkeypatch):
    monkeypatch.setattr(
        "app.services.app_settings.get_app_timezone", lambda: "UTC"
    )
    engine = _engine()
    queued = datetime(2026, 10, 19, 2, 0)
    with Session(engine) as session:
        hosts = [
            _host(session, name="host-a", hostname="host-a.local"),
            _host(session, name="host-b", hostname="host-b.local"),
            _host(session, name="host-c", hostname="host-c.local"),
        ]
        for host, minute in zip(hosts, (1, 16, 40)):
            session.add(
                Job(
                    server_id=host.id,
                    job_type="backup",
                    status="running",
                    created_at=queued,
                    started_at=datetime(2026, 10, 19, 2, minute),
                )
            )
        session.commit()
        assert watch.sync_missed_schedule_alerts(session, now=NOW) == 0
        open_rows = list(
            session.exec(
                select(Notification).where(Notification.status == "open")
            ).all()
        )
        assert open_rows == []


def _host(session, **kwargs):
    fields = dict(
        name="host-a",
        hostname="host-a.local",
        backup_enabled=True,
        backup_schedule="0 2 * * *",
        created_at=BORN,
    )
    fields.update(kwargs)
    server = Server(**fields)
    session.add(server)
    session.commit()
    session.refresh(server)
    return server


def test_watch_opens_one_warning_and_a_later_success_resolves(monkeypatch):
    monkeypatch.setattr(
        "app.services.app_settings.get_app_timezone", lambda: "UTC"
    )
    engine = _engine()
    with Session(engine) as session:
        server = _host(session)
        assert watch.sync_missed_schedule_alerts(session, now=NOW) == 1
        assert watch.sync_missed_schedule_alerts(session, now=NOW) == 1
        rows = list(
            session.exec(select(Notification).where(Notification.status == "open")).all()
        )
        assert len(rows) == 1
        note = rows[0]
        assert note.type == "backup_schedule_missed"
        assert note.severity == "warning"
        assert note.title == "Scheduled backup did not start: host-a"
        assert "15 minutes" in (note.body or "")
        assert note.link_url == f"/servers/{server.id}/backups"
        assert "backup_schedule_missed" not in apol.PINNED_CRITICAL

        started = Job(
            server_id=server.id,
            job_type="backup",
            status="running",
            started_at=datetime(2026, 10, 19, 2, 4),
            created_at=datetime(2026, 10, 19, 2, 0),
        )
        session.add(started)
        session.commit()
        assert watch.sync_missed_schedule_alerts(session, now=NOW) == 0
        session.refresh(note)
        assert note.status == "open"

        started.status = "success"
        started.finished_at = datetime(2026, 10, 19, 2, 10)
        session.add(started)
        session.commit()
        assert watch.sync_missed_schedule_alerts(session, now=NOW) == 0
        session.refresh(note)
        assert note.status == "resolved"


def test_turning_backups_off_resolves_and_fleet_cron_counts(monkeypatch):
    monkeypatch.setattr(
        "app.services.app_settings.get_app_timezone", lambda: "UTC"
    )
    monkeypatch.setattr(
        "app.services.update_check_config.effective_fleet_cron",
        lambda server_id: "0 2 * * *",
    )
    engine = _engine()
    with Session(engine) as session:
        server = _host(session, backup_schedule=None)
        assert watch.sync_missed_schedule_alerts(session, now=NOW) == 1
        server.backup_enabled = False
        session.add(server)
        session.commit()
        assert watch.sync_missed_schedule_alerts(session, now=NOW) == 0
        note = session.exec(select(Notification)).one()
        assert note.status == "resolved"


def test_container_check_miss_and_metric(monkeypatch):
    monkeypatch.setattr(
        "app.services.app_settings.get_app_timezone", lambda: "UTC"
    )
    engine = _engine()
    moment = _utc(2026, 10, 19, 3, 20)
    with Session(engine) as session:
        server = _host(
            session,
            backup_enabled=False,
            backup_schedule=None,
            container_check_enabled=True,
            container_check_schedule="0 3 * * *",
        )
        lines = watch.missed_schedule_lines(session, now=moment)
        assert lines == [
            f'piherder_schedule_missed{{kind="container_check",server_id="{server.id}"}} 1'
        ]
        assert watch.sync_missed_schedule_alerts(session, now=moment) == 1
        note = session.exec(
            select(Notification).where(Notification.status == "open")
        ).one()
        assert note.type == "container_check_missed"
        assert note.link_url == f"/servers/{server.id}/docker"
        watch.resolve_container_check_missed(session, server.id)
        session.refresh(note)
        assert note.status == "resolved"


def test_success_helper_clears_a_backup_miss(monkeypatch):
    monkeypatch.setattr(
        "app.services.app_settings.get_app_timezone", lambda: "UTC"
    )
    engine = _engine()
    with Session(engine) as session:
        server = _host(session)
        watch.sync_missed_schedule_alerts(session, now=NOW)
        watch.resolve_backup_schedule_missed(session, server.id)
        note = session.exec(select(Notification)).one()
        assert note.status == "resolved"


def test_missed_schedule_watch_is_registered():
    added = {}

    class _Scheduler:
        def remove_job(self, job_id):
            added["removed"] = job_id

        def add_job(self, **kwargs):
            added["job"] = kwargs

    sync_missed_schedule_watch(_Scheduler(), True)
    assert added["removed"] == watch.MISSED_SCHEDULE_WATCH_JOB_ID
    assert added["job"]["id"] == watch.MISSED_SCHEDULE_WATCH_JOB_ID
    assert added["job"]["func"].__name__ == "watch_missed_schedules"

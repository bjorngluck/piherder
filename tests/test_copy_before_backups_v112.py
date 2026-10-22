"""Destination copy scheduled before the host backups it ships."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import BackupDestination, Notification, Server
from app.services import alert_policy as apol
from app.services import copy_schedule_order as order
from app.services.backup_replicate import selection_json


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


NOW = datetime(2026, 10, 19, 0, 30, tzinfo=timezone.utc)


def test_a_ticked_child_still_counts_the_host_folder():
    assert order.folder_selected("host-a", ["host-a/docker"], [])
    assert not order.folder_selected("host-a", ["host-b"], [])
    assert not order.folder_selected("host-a", ["host-a"], ["host-a"])


def test_same_day_order():
    assert order.copy_runs_before_host("0 1 * * *", "30 2 * * *", "UTC", NOW)
    assert not order.copy_runs_before_host("0 3 * * *", "30 2 * * *", "UTC", NOW)
    assert not order.copy_runs_before_host("0 2 * * *", "0 2 * * *", "UTC", NOW)
    assert order.copy_runs_before_host("0 3 * * 1", "0 4 * * 1", "UTC", NOW)
    assert not order.copy_runs_before_host("0 3 * * 0", "0 2 * * 1", "UTC", NOW)
    assert not order.copy_runs_before_host("not a cron", "0 2 * * *", "UTC", NOW)


def _dest(session, schedule="0 1 * * *", checked=None, **kwargs):
    fields = dict(
        name="Google Drive",
        provider="drive",
        enabled=True,
        credentials_encrypted="saved",
        schedule=schedule,
        selection_json=selection_json(checked if checked is not None else ["host-a"], []),
    )
    fields.update(kwargs)
    dest = BackupDestination(**fields)
    session.add(dest)
    session.commit()
    session.refresh(dest)
    return dest


def _host(session, **kwargs):
    fields = dict(
        name="host-a",
        hostname="host-a.local",
        backup_enabled=True,
        backup_schedule="30 2 * * *",
        backup_folder_name="host-a",
        backup_direct=False,
    )
    fields.update(kwargs)
    server = Server(**fields)
    session.add(server)
    session.commit()
    session.refresh(server)
    return server


def test_warning_names_the_host_and_clears_when_the_copy_is_later(monkeypatch):
    monkeypatch.setattr("app.services.app_settings.get_app_timezone", lambda: "UTC")
    engine = _engine()
    with Session(engine) as session:
        server = _host(session)
        other = _host(
            session,
            name="host-b",
            hostname="host-b.local",
            backup_folder_name="host-b",
            backup_schedule="45 2 * * *",
        )
        dest = _dest(session, checked=["host-a", "host-b"])
        assert order.sync_copy_order_alerts(session, now=NOW) == 1
        note = session.exec(
            select(Notification).where(Notification.status == "open")
        ).one()
        assert note.type == "backup_copy_early"
        assert note.severity == "warning"
        assert note.title == "Copy runs before the backups: Google Drive"
        assert "host-a" in (note.body or "")
        assert "host-b" in (note.body or "")
        assert note.link_url == "/herder-backups?tab=backup"
        assert "backup_copy_early" not in apol.PINNED_CRITICAL
        text = order.early_text_by_provider(session, now=NOW)
        assert text["drive"] == note.body

        direct = _host(
            session,
            name="host-c",
            hostname="host-c.local",
            backup_folder_name="host-c",
            backup_direct=True,
            backup_schedule="50 3 * * *",
        )
        dest.selection_json = selection_json(["host-a", "host-b", "host-c"], [])
        session.add(dest)
        session.commit()
        order.sync_copy_order_alerts(session, now=NOW)
        session.refresh(note)
        assert "host-c" not in (note.body or "")

        server.backup_schedule = "0 0 * * *"
        other.backup_schedule = "15 0 * * *"
        session.add(server)
        session.add(other)
        session.commit()
        assert order.sync_copy_order_alerts(session, now=NOW) == 0
        session.refresh(note)
        assert note.status == "resolved"
        assert direct.id


def test_unticked_direct_and_unsaved_copies_stay_quiet(monkeypatch):
    monkeypatch.setattr("app.services.app_settings.get_app_timezone", lambda: "UTC")
    monkeypatch.setattr(
        "app.services.update_check_config.effective_fleet_cron",
        lambda server_id: "30 2 * * *",
    )
    engine = _engine()
    with Session(engine) as session:
        _host(session, backup_schedule=None)
        quiet = _dest(session, checked=["other-host"])
        assert order.sync_copy_order_alerts(session, now=NOW) == 0
        quiet.selection_json = selection_json(["host-a"], [])
        quiet.credentials_encrypted = ""
        session.add(quiet)
        session.commit()
        assert order.sync_copy_order_alerts(session, now=NOW) == 0
        quiet.credentials_encrypted = "saved"
        session.add(quiet)
        session.commit()
        assert order.sync_copy_order_alerts(session, now=NOW) == 1
        _host(
            session,
            name="straight",
            hostname="host-d.local",
            backup_folder_name="host-d",
            backup_direct=True,
        )
        only_direct = _dest(
            session,
            provider="smb",
            name="LAN share",
            checked=["host-d"],
        )
        assert order.early_hosts_for(
            only_direct,
            list(session.exec(select(Server)).all()),
            tz_name="UTC",
            now=NOW,
        ) == []

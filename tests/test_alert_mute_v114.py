"""Alert mute rules: delivery only, windows, audit, and the alerts API."""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import AuditLog, Notification, Server, User
from app.security.auth import get_admin_user, get_password_hash
from app.services import alert_mute as mute
from app.services import api_tokens as tok_svc
from app.services import metrics as metrics_svc
from app.services import notifications as ntf


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'mute.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _session(engine):
    def _open():
        with Session(engine) as session:
            yield session

    return _open


def _host(session: Session, hostname: str = "host-a") -> Server:
    row = Server(name="Host A", hostname=hostname, ssh_username="pi", ip_address="192.0.2.10")
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _user(session: Session) -> User:
    row = User(
        email="user@example.com",
        hashed_password=get_password_hash("SmokeTest1ok"),
        role="admin",
        is_active=True,
        must_change_password=False,
        totp_enabled=False,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_module_does_not_import_the_scheduler():
    source = inspect.getsource(mute)
    assert "scheduler" not in source
    text = Path("migrations/versions/20261027_1800-053_alert_mute.py").read_text()
    assert 'revision: str = "053_alert_mute"' in text
    assert 'down_revision: Union[str, None] = "052_schedule_catchup"' in text
    assert "alertmuterule" in text


def test_type_mute_skips_delivery_and_marks_the_open_row(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        mute.create_rule(
            session,
            kind="type",
            server_id=None,
            alert_type="os_updates",
            starts_at=None,
            ends_at=None,
        )
        with (
            patch.object(ntf, "_maybe_webhook") as webhook,
            patch.object(ntf, "_maybe_email") as email,
            patch.object(ntf, "_maybe_push") as push,
        ):
            row = ntf.upsert_notification(
                session,
                fingerprint="fp-os",
                type="os_updates",
                title="OS updates",
                severity="warning",
            )
        assert row is not None
        assert row.status == "open"
        assert row.payload is None or "_last_notified_at" not in (row.payload or "")
        webhook.assert_not_called()
        email.assert_not_called()
        push.assert_not_called()
        assert mute.is_muted(session, alert_type="os_updates", server_id=None) is True


def test_host_mute_skips_a_mutable_type_and_not_a_backup_failure(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        host = _host(session)
        mute.create_rule(
            session,
            kind="host",
            server_id=host.id,
            alert_type=None,
            starts_at=None,
            ends_at=None,
        )
        with patch.object(ntf, "_maybe_webhook") as webhook, patch.object(ntf, "_maybe_push"):
            muted = ntf.upsert_notification(
                session,
                fingerprint="fp-host",
                type="os_updates",
                title="OS",
                server_id=host.id,
            )
            loud = ntf.upsert_notification(
                session,
                fingerprint="fp-bak",
                type="backup_failed",
                title="Backup",
                server_id=host.id,
            )
        assert muted is not None and loud is not None
        assert webhook.call_count == 1
        assert mute.is_muted(session, alert_type="backup_failed", server_id=host.id) is False
        assert mute.is_muted(session, alert_type="os_updates", server_id=host.id) is True


def test_unmutable_types_are_rejected_and_a_stale_backup_warning_is_allowed(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        for name in (
            "backup_failed",
            "herder_backup_failed",
            "backup_destination_auth_failed",
            "drive_oauth_refresh_failed",
            "drive_oauth_token_aging",
            "admin_change",
        ):
            try:
                mute.create_rule(
                    session,
                    kind="type",
                    server_id=None,
                    alert_type=name,
                    starts_at=None,
                    ends_at=None,
                )
            except mute.MuteRejected as exc:
                assert str(exc) == "That alert cannot be muted"
            else:
                raise AssertionError(name)
        row = mute.create_rule(
            session,
            kind="type",
            server_id=None,
            alert_type="backup_stale",
            starts_at=None,
            ends_at=None,
        )
        assert row.alert_type == "backup_stale"


def test_windows_reject_a_missing_end_and_an_end_before_the_start(tmp_path):
    engine = _engine(tmp_path)
    start = datetime(2026, 10, 27, 12, 0, 0)
    with Session(engine) as session:
        try:
            mute.create_rule(
                session,
                kind="type",
                server_id=None,
                alert_type="os_updates",
                starts_at=start,
                ends_at=None,
            )
        except mute.MuteRejected as exc:
            assert "end" in str(exc)
        else:
            raise AssertionError("start without end")
        try:
            mute.create_rule(
                session,
                kind="type",
                server_id=None,
                alert_type="os_updates",
                starts_at=start,
                ends_at=start,
            )
        except mute.MuteRejected as exc:
            assert "before" in str(exc)
        else:
            raise AssertionError("end before start")


def test_end_only_is_active_until_the_end_and_an_expired_rule_does_not_count(tmp_path):
    engine = _engine(tmp_path)
    now = datetime.utcnow()
    with Session(engine) as session:
        live = mute.create_rule(
            session,
            kind="type",
            server_id=None,
            alert_type="os_updates",
            starts_at=None,
            ends_at=now + timedelta(hours=1),
        )
        expired = mute.create_rule(
            session,
            kind="type",
            server_id=None,
            alert_type="container_updates",
            starts_at=None,
            ends_at=now - timedelta(minutes=1),
        )
        assert mute.is_active(live, now) is True
        assert mute.is_active(expired, now) is False
        assert mute.is_muted(session, alert_type="os_updates", server_id=None, now=now) is True
        assert mute.is_muted(session, alert_type="container_updates", server_id=None, now=now) is False
        assert mute.active_count(session, now) == 1
        future = mute.create_rule(
            session,
            kind="type",
            server_id=None,
            alert_type="reboot_pending",
            starts_at=now + timedelta(hours=2),
            ends_at=now + timedelta(hours=3),
        )
        assert mute.is_active(future, now) is False
        body = metrics_svc.render_prometheus(metrics_svc.collect_samples(session))
        assert "piherder_alerts_muted 1" in body


def test_unknown_host_unknown_type_and_duplicates_are_rejected(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        host = _host(session)
        mute.create_rule(
            session,
            kind="host",
            server_id=host.id,
            alert_type=None,
            starts_at=None,
            ends_at=None,
        )
        mute.create_rule(
            session,
            kind="type",
            server_id=None,
            alert_type="os_updates",
            starts_at=None,
            ends_at=None,
        )
        for kwargs, message in (
            ({"kind": "host", "server_id": 999, "alert_type": None}, "Host not found"),
            ({"kind": "type", "server_id": None, "alert_type": "not_a_type"}, "Unknown alert type"),
            ({"kind": "host", "server_id": host.id, "alert_type": None}, "already has a mute rule"),
            (
                {"kind": "type", "server_id": None, "alert_type": "os_updates"},
                "already has a mute rule",
            ),
            ({"kind": "both", "server_id": None, "alert_type": None}, "one host or one alert type"),
        ):
            try:
                mute.create_rule(session, starts_at=None, ends_at=None, **kwargs)
            except mute.MuteRejected as exc:
                assert message in str(exc)
            else:
                raise AssertionError(kwargs)


def test_resolve_skips_push_while_muted(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        mute.create_rule(
            session,
            kind="type",
            server_id=None,
            alert_type="os_updates",
            starts_at=None,
            ends_at=None,
        )
        with patch.object(ntf, "_maybe_webhook"), patch.object(ntf, "_maybe_push"):
            ntf.upsert_notification(
                session,
                fingerprint="fp-res",
                type="os_updates",
                title="OS",
            )
        with patch.object(ntf, "_maybe_push_resolved") as push:
            assert ntf.resolve_by_fingerprint(session, "fp-res") == 1
        push.assert_not_called()
        closed = session.exec(
            select(Notification).where(Notification.fingerprint == "fp-res")
        ).first()
        assert closed is not None
        assert closed.status == "resolved"


def test_api_scopes_audit_and_muted_flag(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    engine = _engine(tmp_path)
    app.dependency_overrides[get_session] = _session(engine)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with Session(engine) as session:
            user = _user(session)
            host = _host(session)
            host_id = host.id
            _read, read_plain = tok_svc.create_api_token(
                session, name="read", created_by=user, scopes=["read"]
            )
            _edit, edit_plain = tok_svc.create_api_token(
                session, name="edit", created_by=user, scopes=["edit"]
            )
            with patch.object(ntf, "_maybe_webhook"), patch.object(ntf, "_maybe_push"):
                ntf.upsert_notification(
                    session,
                    fingerprint="fp-api",
                    type="os_updates",
                    title="OS updates",
                    server_id=host.id,
                )
        read = {"Authorization": f"Bearer {read_plain}"}
        edit = {"Authorization": f"Bearer {edit_plain}"}
        denied = client.post(
            "/api/v1/alert-mutes",
            headers=read,
            json={"kind": "type", "alert_type": "os_updates"},
        )
        assert denied.status_code == 403
        created = client.post(
            "/api/v1/alert-mutes",
            headers=edit,
            json={"kind": "host", "server_id": host_id, "alert_type": "os_updates"},
        )
        assert created.status_code == 400
        created = client.post(
            "/api/v1/alert-mutes",
            headers=edit,
            json={"kind": "type", "alert_type": "os_updates"},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["active"] is True
        assert body["alert_type"] == "os_updates"
        assert "host-a" not in created.text
        rule_id = body["id"]
        listed = client.get("/api/v1/alerts", headers=read)
        assert listed.status_code == 200
        alert = listed.json()["alerts"][0]
        assert alert["muted"] is True
        assert alert["title"] == "OS updates"
        assert "token" not in alert
        patched = client.patch(
            f"/api/v1/alert-mutes/{rule_id}",
            headers=edit,
            json={"ends_at": "2026-10-28T00:00:00Z"},
        )
        assert patched.status_code == 200
        assert patched.json()["ends_at"].startswith("2026-10-28T00:00:00")
        assert patched.json()["starts_at"] is None
        removed = client.delete(f"/api/v1/alert-mutes/{rule_id}", headers=edit)
        assert removed.status_code == 200
        missing = client.delete("/api/v1/alert-mutes/999", headers=edit)
        assert missing.status_code == 404
        with Session(engine) as session:
            actions = [
                row.action
                for row in session.exec(select(AuditLog).order_by(AuditLog.id)).all()
            ]
            details = " ".join(
                row.details or ""
                for row in session.exec(select(AuditLog)).all()
            )
        assert actions == [
            "alert_mute_created",
            "alert_mute_updated",
            "alert_mute_deleted",
        ]
        assert "host-a" not in details
        assert "alert_type=os_updates" in details
    finally:
        app.dependency_overrides.clear()


def test_settings_form_audits_a_host_rule(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    engine = _engine(tmp_path)
    with Session(engine) as session:
        user = _user(session)
        host = _host(session)
        admin_id = user.id
        host_id = host.id
    app.dependency_overrides[get_session] = _session(engine)
    app.dependency_overrides[get_admin_user] = lambda: User(
        id=admin_id,
        email="user@example.com",
        hashed_password="x",
        role="admin",
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        created = client.post(
            "/herder-backups/alerts/mute",
            data={
                "kind": "host",
                "server_id": str(host_id),
                "alert_type": "",
                "starts_at": "",
                "ends_at": "",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        assert "mute_saved=1" in created.headers["location"]
        missing_end = client.post(
            "/herder-backups/alerts/mute",
            data={
                "kind": "type",
                "server_id": "",
                "alert_type": "container_updates",
                "starts_at": "2026-10-27T12:00",
                "ends_at": "",
            },
            follow_redirects=False,
        )
        assert missing_end.status_code == 303
        assert "alerts_error=" in missing_end.headers["location"]
        with Session(engine) as session:
            rows = list(session.exec(select(AuditLog)).all())
            assert len(rows) == 1
            assert rows[0].action == "alert_mute_created"
            assert f"server_id={host_id}" in (rows[0].details or "")
            assert "host-a" not in (rows[0].details or "")
    finally:
        app.dependency_overrides.clear()

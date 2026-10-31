"""Drive OAuth health: refresh failure and a testing token older than 5 days."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.database import get_engine
from app.models import BackupDestination, Notification
from app.services import alert_policy as apol
from app.services import backup_replicate as copies
from app.security.encryption import encrypt_str


def _dest(session: Session, **cfg) -> BackupDestination:
    row = BackupDestination(
        name="Google Drive",
        provider="drive",
        config_json=json.dumps(cfg),
        credentials_encrypted=encrypt_str(json.dumps({"auth": "oauth", "refresh_token": "rt-example"})),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _open(session: Session, type_id: str) -> list[Notification]:
    return list(
        session.exec(
            select(Notification).where(Notification.type == type_id, Notification.status == "open")
        ).all()
    )


def test_types_cannot_be_muted_and_refresh_stays_critical():
    assert "drive_oauth_refresh_failed" in apol.NEVER_MUTE
    assert "drive_oauth_token_aging" in apol.NEVER_MUTE
    failed = apol.effective("drive_oauth_refresh_failed")
    aging = apol.effective("drive_oauth_token_aging")
    assert failed.enabled is True and failed.severity == "critical"
    assert aging.enabled is True and aging.severity == "warning"


def test_refresh_failure_records_age_and_not_the_token():
    with Session(get_engine()) as session:
        saved = (datetime.utcnow() - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        dest = _dest(session, oauth_token_saved_at=saved, google_app_testing=True)
        copies.note_copy_auth_failure(session, dest, "invalid_grant rt-example ya29.SECRET")
        rows = _open(session, "drive_oauth_refresh_failed")
        assert rows
        body = rows[-1].body or ""
        assert "rt-example" not in body
        assert "ya29" not in body
        assert "Age 6 days" in body
        assert "A token is saved" in body
        copies.resolve_copy_auth_failure(session, dest.id)
        assert _open(session, "drive_oauth_refresh_failed") == []


def test_aging_needs_testing_mode_and_a_saved_time():
    now = datetime.utcnow()
    old = (now - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    young = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with Session(get_engine()) as session:
        for row in session.exec(select(BackupDestination).where(BackupDestination.provider == "drive")).all():
            cfg = json.loads(row.config_json or "{}")
            if not isinstance(cfg, dict):
                cfg = {}
            cfg["google_app_testing"] = False
            row.config_json = json.dumps(cfg)
            session.add(row)
        session.commit()
        quiet = _dest(session, google_app_testing=True)
        testing = _dest(session, google_app_testing=True, oauth_token_saved_at=old)
        fresh = _dest(session, google_app_testing=True, oauth_token_saved_at=young)
        production = _dest(session, google_app_testing=False, oauth_token_saved_at=old)
        opened = copies.sync_drive_token_aging(session, now)
        assert opened == 1
        bodies = [row.body or "" for row in _open(session, "drive_oauth_token_aging")]
        assert len(bodies) == 1
        assert "rt-example" not in bodies[0]
        assert "Age 6 days" in bodies[0]
        assert copies.drive_token_age_days(quiet, now) is None
        copies.mark_drive_token_saved(fresh, now)
        session.add(fresh)
        session.commit()
        copies.resolve_drive_token_aging(session, testing.id)
        copies.sync_drive_token_aging(session, now)
        still = _open(session, "drive_oauth_token_aging")
        assert all(row.fingerprint != f"drive_oauth_token_aging:{production.id}" for row in still)
        assert all(row.fingerprint != f"drive_oauth_token_aging:{quiet.id}" for row in still)

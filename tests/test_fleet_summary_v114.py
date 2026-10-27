"""Fleet summary counts and states. No addresses, tokens, or paths."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app.database import get_engine
from app.models import BackupDestination, Job, Notification, Server
from app.services import fleet_summary as summary
from app.services.mcp_hosted import tool_catalog


def _walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def test_since_rejects_junk_and_keeps_a_blank():
    assert summary.parse_since("") is None
    assert summary.parse_since(None) is None
    moment = summary.parse_since("2026-10-27T12:00:00Z")
    assert moment is not None and moment.tzinfo is not None
    with pytest.raises(ValueError):
        summary.parse_since("yesterday")


def test_snapshot_hides_addresses_tokens_and_paths():
    now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    with Session(get_engine()) as session:
        for row in session.exec(select(Server)).all():
            row.last_seen = now - timedelta(days=2)
            session.add(row)
        session.commit()
        host = Server(
            name="Host A",
            hostname="host-a.example.com",
            ip_address="192.0.2.10",
            ssh_username="user",
            last_seen=now,
            backup_enabled=True,
            last_backup_at=now - timedelta(hours=1),
            os_updates_count=2,
            last_os_check_at=now,
        )
        session.add(host)
        session.commit()
        session.refresh(host)
        session.add(Job(server_id=host.id, job_type="backup", status="failed", finished_at=now))
        session.add(Job(server_id=host.id, job_type="backup_replicate", status="failed", finished_at=now))
        session.add(
            Notification(
                type="drive_oauth_refresh_failed",
                severity="critical",
                title="Google Drive refresh failed",
                fingerprint=f"drive-test-{host.id}",
                status="open",
                body="A token is saved. Age 6 days.",
            )
        )
        session.add(
            BackupDestination(
                name="Google Drive",
                provider="drive",
                credentials_encrypted="secret-token-value",
                config_json='{"path": "/srv/backups"}',
            )
        )
        session.commit()
        payload = summary.build_fleet_summary(session, since=None, now=now)
    text = json.dumps(payload)
    assert "192.0.2.10" not in text
    assert "host-a.example.com" not in text
    assert "secret-token-value" not in text
    assert "/srv/backups" not in text
    assert payload["since"] is None
    assert host.id in payload["areas"]["hosts"]["ids"]
    assert payload["areas"]["hosts"]["states"]["seen"] == 1
    assert payload["areas"]["failed_jobs"]["count"] >= 1
    assert payload["areas"]["drive"]["state"] == "failed"
    assert payload["areas"]["updates"]["pending"] >= 1
    for key, _item in _walk(payload):
        assert key not in {"hostname", "ip_address", "token", "path", "webhook"}


def test_since_drops_areas_that_did_not_change():
    now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    with Session(get_engine()) as session:
        for row in session.exec(select(Server)).all():
            row.created_at = now - timedelta(days=3)
            row.last_seen = now - timedelta(days=2)
            row.last_backup_at = None
            row.last_os_check_at = None
            row.last_container_check_at = None
            session.add(row)
        session.commit()
        host = Server(
            name="Host B",
            hostname="host-b.example.com",
            ssh_username="user",
            created_at=now - timedelta(days=2),
            last_seen=now - timedelta(days=1),
            last_backup_at=None,
            last_os_check_at=None,
            last_container_check_at=None,
        )
        session.add(host)
        session.commit()
        payload = summary.build_fleet_summary(
            session,
            since=now - timedelta(minutes=5),
            now=now,
        )
    assert "hosts" not in payload["areas"]
    assert payload["since"].startswith("2026-10-27T11:55:00")


def test_hosted_tool_is_listed_beside_summary():
    names = [tool["name"] for tool in tool_catalog()]
    assert "summary" in names
    assert "fleet_summary" in names
    tool = next(item for item in tool_catalog() if item["name"] == "fleet_summary")
    assert tool["scope"] == "read"
    assert tool["annotations"]["readOnlyHint"] is True

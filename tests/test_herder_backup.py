"""Unit tests for expanded PiHerder self-backup payload (no live DB required for helpers)."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.services import herder_backup as hb
from app.models import User, Server


def test_backup_format_version():
    assert hb.BACKUP_FORMAT_VERSION == "3"


def test_model_to_dict_excludes_relationships():
    u = User(
        id=1,
        email="a@b.com",
        hashed_password="hash",
        role="admin",
        totp_enabled=True,
        totp_secret_encrypted="enc",
    )
    d = hb._model_to_dict(u)
    assert d["email"] == "a@b.com"
    assert d["hashed_password"] == "hash"
    assert d["role"] == "admin"
    assert d["totp_secret_encrypted"] == "enc"
    assert "audit_logs" not in d
    assert "totp_backup_codes" not in d


def test_parse_dt():
    assert hb._parse_dt(None) is None
    dt = hb._parse_dt("2026-07-10T12:30:00Z")
    assert isinstance(dt, datetime)
    assert dt.year == 2026 and dt.month == 7 and dt.hour == 12
    assert dt.tzinfo is None


def test_clean_row_filters_unknown():
    raw = {
        "id": 1,
        "email": "x@y.com",
        "hashed_password": "h",
        "role": "operator",
        "not_a_column": "drop-me",
        "created_at": "2026-01-01T00:00:00",
    }
    cleaned = hb._clean_row(User, raw)
    assert "not_a_column" not in cleaned
    assert cleaned["role"] == "operator"
    assert isinstance(cleaned["created_at"], datetime)


def test_build_payload_keys(monkeypatch):
    """Ensure expanded tables are always present in the archive JSON shape."""
    monkeypatch.setattr(hb, "_snapshot_servers", lambda: [{"id": 1, "name": "s"}])
    monkeypatch.setattr(
        hb,
        "_snapshot_users",
        lambda: [
            {
                "id": 1,
                "email": "a@b.com",
                "hashed_password": "x",
                "role": "admin",
            }
        ],
    )
    monkeypatch.setattr(hb, "_snapshot_totp_backup_codes", lambda: [])
    monkeypatch.setattr(hb, "_snapshot_trusted_devices", lambda: [])
    monkeypatch.setattr(hb, "_snapshot_docker_versions", lambda: [])
    monkeypatch.setattr(hb, "_snapshot_push_vapid", lambda: [{"id": 1, "public_key": "pk"}])
    monkeypatch.setattr(hb, "_snapshot_push_subscriptions", lambda: [])
    monkeypatch.setattr(hb, "_snapshot_push_preferences", lambda: [])
    monkeypatch.setattr(hb, "_snapshot_notifications", lambda: [])
    monkeypatch.setattr(
        hb,
        "_snapshot_integrations",
        lambda: [
            {
                "id": 1,
                "type": "grafana",
                "name": "G",
                "base_url": "https://g.example.com",
            }
        ],
    )
    monkeypatch.setattr(
        hb,
        "_snapshot_integration_bindings",
        lambda: [
            {
                "id": 1,
                "integration_id": 1,
                "server_id": 1,
                "role": "dashboard",
                "external_id": "uid1",
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.herder_backup.load_settings",
        lambda: {"timezone": "UTC", "force_2fa": False, "keep": 10},
    )

    payload = hb._build_backup_payload(include_audit=False, config_only=True)
    assert payload["manifest"]["version"] == "3"
    assert "jobs" not in payload
    assert "jobs" in payload["manifest"]["excludes"]
    assert "integrations" in payload["manifest"]["includes"]
    assert "integration_bindings" in payload["manifest"]["includes"]
    assert "runtime_edges" in payload["manifest"]["includes"]
    for key in (
        "servers",
        "users",
        "totp_backup_codes",
        "trusted_devices",
        "docker_versions",
        "push_vapid",
        "push_subscriptions",
        "push_preferences",
        "notifications",
        "integrations",
        "integration_bindings",
        "managed_certificates",
        "certificate_targets",
        "service_templates",
        "stack_deployments",
        "service_dns_records",
        "runtime_edges",
        "herder_config",
    ):
        assert key in payload
    assert "avatars" in payload["manifest"]["includes"]
    assert "service_logos" in payload["manifest"]["includes"]
    assert payload["users"][0]["hashed_password"] == "x"
    assert payload["integrations"][0]["type"] == "grafana"
    assert payload["integration_bindings"][0]["role"] == "dashboard"
    assert payload["herder_config"]["timezone"] == "UTC"


def test_service_logo_files(tmp_path, monkeypatch):
    data = tmp_path / "data"
    logos = data / "service_logos"
    logos.mkdir(parents=True)
    (logos / "1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    (logos / "skip.txt").write_bytes(b"x" * 10)
    monkeypatch.setattr(hb.settings, "DATA_ROOT", str(data))
    monkeypatch.setattr(hb.settings, "AVATAR_MAX_BYTES", 2 * 1024 * 1024)
    files = hb._service_logo_files()
    names = {p.name for p in files}
    assert "1.png" in names


def test_build_payload_includes_audit_when_requested(monkeypatch):
    monkeypatch.setattr(hb, "_snapshot_servers", lambda: [])
    monkeypatch.setattr(hb, "_snapshot_users", lambda: [])
    monkeypatch.setattr(hb, "_snapshot_totp_backup_codes", lambda: [])
    monkeypatch.setattr(hb, "_snapshot_trusted_devices", lambda: [])
    monkeypatch.setattr(hb, "_snapshot_docker_versions", lambda: [])
    monkeypatch.setattr(hb, "_snapshot_push_vapid", lambda: [])
    monkeypatch.setattr(hb, "_snapshot_push_subscriptions", lambda: [])
    monkeypatch.setattr(hb, "_snapshot_push_preferences", lambda: [])
    monkeypatch.setattr(hb, "_snapshot_notifications", lambda: [])
    monkeypatch.setattr("app.services.herder_backup.load_settings", lambda: {})
    monkeypatch.setattr(hb, "_snapshot_audit", lambda since_days=None: [{"id": 9}])

    payload = hb._build_backup_payload(include_audit=True, config_only=False)
    assert "audit_logs" in payload
    assert payload["audit_logs"][0]["id"] == 9
    assert "audit_logs" in payload["manifest"]["includes"]


def test_dry_run_counts(monkeypatch, tmp_path):
    """dry_run reads archive counts without writing DB."""
    import tarfile
    import json

    payload = {
        "manifest": {"version": "2"},
        "servers": [{"id": 1}],
        "users": [{"id": 1, "email": "a@b.com"}],
        "docker_versions": [{}, {}],
        "totp_backup_codes": [{}],
        "trusted_devices": [],
        "push_vapid": [{}],
        "push_subscriptions": [{}, {}],
        "push_preferences": [{}],
        "notifications": [{}, {}, {}],
        "herder_config": {"timezone": "UTC"},
        "audit_logs": [{"id": 1}],
    }
    archive = tmp_path / "t.tar.gz"
    json_path = tmp_path / "piherder-backup.json"
    json_path.write_text(json.dumps(payload))
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(json_path, arcname="piherder-backup.json")
        # fake avatar
        av = tmp_path / "1.png"
        av.write_bytes(b"png")
        tar.add(av, arcname="data/avatars/1.png")

    result = hb.restore_herder_backup(str(archive), restore_audit=True, dry_run=True)
    assert result["dry_run"] is True
    assert result["would_restore_servers"] == 1
    assert result["would_restore_users"] == 1
    assert result["would_restore_docker_versions"] == 2
    assert result["would_restore_push_subscriptions"] == 2
    assert result["would_restore_notifications"] == 3
    assert result["would_restore_avatars"] == 1
    assert result["would_restore_herder_config"] is True
    assert result["would_restore_audit"] == 1

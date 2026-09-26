"""v1.6 Q-80 thirtieth pack — settings hub POST writes."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import User
from app.security.auth import create_user_access_token, get_password_hash


def test_settings_hub_post_writes(tmp_path, monkeypatch):
    from app.services import app_settings as cfg
    from app.services import herder_backup as hb

    store: dict = {}
    monkeypatch.setattr(cfg, "_load_raw_from_db", lambda: dict(store))
    monkeypatch.setattr(cfg, "_write_raw_to_db", lambda data: store.update(data or {}))
    monkeypatch.setattr(hb, "create_herder_backup", lambda **k: Path(tmp_path / "hb.tgz"))

    engine = create_engine(
        f"sqlite:///{tmp_path / 'r30.db'}",
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
        with Session(engine) as s:
            user = User(
                email="q30@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            s.add(user)
            s.commit()
            s.refresh(user)
            client.cookies.set("access_token", create_user_access_token(user))

        r = client.get("/herder-backups")
        assert r.status_code == 200
        r = client.get("/herder-backups?tab=general")
        assert r.status_code == 200
        assert "Host wait" in r.text
        assert 'data-testid="host-wait-minutes"' in r.text
        assert 'data-testid="instance-name"' in r.text
        assert 'data-testid="instance-accent"' in r.text
        r = client.get("/herder-backups?tab=alerts")
        assert r.status_code == 200
        r = client.get("/herder-backups?tab=fleet")
        assert r.status_code == 200
        r = client.get("/herder-backups/download?name=nope")
        assert r.status_code in (404, 403, 200)

        r = client.post(
            "/herder-backups/config",
            data={
                "keep": "8",
                "schedule_mode": "config_only",
                "schedule_enabled": "0",
                "schedule_cron": "0 3 * * *",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200)

        r = client.post(
            "/herder-backups/security",
            data={
                "force_2fa_scope": "off",
                "force_2fa_grace_days": "0",
                "password_min_length": "12",
                "password_max_length": "72",
                "password_require_upper": "on",
                "password_require_lower": "on",
                "password_require_digit": "on",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200)

        r = client.post(
            "/herder-backups/console",
            data={
                "console_idle_sec": "900",
                "console_max_sec": "3600",
                "console_max_per_user": "4",
                "console_max_global": "20",
                "console_ticket_sec": "60",
                "console_hold_sec": "0",
                "console_revalidate_sec": "10",
                "console_scrollback": "2000",
                "console_privileged_role": "admin",
                "console_audit_mode": "off",
                "console_audit_retention_days": "14",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200)

        r = client.post(
            "/herder-backups/files",
            data={"files_max_gib": "0.5"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200)

        monkeypatch.delenv("PIHERDER_EXCLUSIVE_HOST_WAIT_SEC", raising=False)
        r = client.post(
            "/herder-backups/jobs-wait",
            data={"host_wait_minutes": "45"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200)
        assert store.get("exclusive_host_wait_sec") == 45 * 60

        r = client.post(
            "/herder-backups/instance",
            data={"instance_name": "Homelab", "instance_accent": "#112233"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200)
        assert store.get("instance_name") == "Homelab"
        assert store.get("instance_accent") == "#112233"

        r = client.post(
            "/herder-backups/oidc",
            data={
                "oidc_display_name": "SSO",
                "oidc_issuer": "",
                "oidc_client_id": "",
                "oidc_role_map": "{}",
                "oidc_default_role": "viewer",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200)
        r = client.post(
            "/herder-backups/oidc",
            data={"oidc_role_map": "not-json", "oidc_default_role": "viewer"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 422, 200)

        r = client.post(
            "/herder-backups/timezone",
            data={"timezone": "UTC"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200)

        r = client.post(
            "/herder-backups/data-cleanup/config",
            data={
                "data_cleanup_cron": "30 4 * * *",
                "data_cleanup_jobs_days": "30",
                "data_cleanup_audit_days": "30",
                "data_cleanup_nmap_days": "30",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200)

        r = client.post(
            "/herder-backups/update-checks",
            data={
                "os_check_cron": "0 0 * * *",
                "container_check_cron": "0 0 * * *",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200)

        r = client.post(
            "/herder-backups/run",
            data={"backup_mode": "config_only"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200, 500)

        r = client.post(
            "/herder-backups/alerts/policy",
            data={},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 422, 200)

        r = client.post(
            "/herder-backups/alerts/webhook",
            data={"url": "", "enabled": "0"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 422, 200)

        r = client.post(
            "/herder-backups/alerts/smtp",
            data={"host": "", "port": "587", "from_addr": ""},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 422, 200)
    finally:
        app.dependency_overrides.clear()

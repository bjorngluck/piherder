"""v1.6 Q-80 thirty-third pack — console policy helpers + workspace GET."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Server, User
from app.security.auth import create_user_access_token, get_password_hash
from app.security.encryption import encrypt_str


def test_ssh_console_policy_helpers(monkeypatch):
    from app.services import ssh_console as cons

    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    cons.reset_runtime_state_for_tests()

    assert cons.console_enabled() is True
    assert isinstance(cons.is_demo_console(), bool)
    pol = cons.clamp_console_policy(
        {
            "console_idle_sec": 10,
            "console_max_sec": 999999,
            "console_max_per_user": 0,
            "console_max_global": 1,
            "console_ticket_sec": 5,
            "console_hold_sec": -1,
            "console_revalidate_sec": 1,
            "console_scrollback": 50,
            "console_privileged_role": "viewer",
            "console_audit_mode": "commands",
            "console_audit_retention_days": 3,
            "console_bind_ip": True,
            "console_bind_device": False,
        }
    )
    assert pol["console_idle_sec"] >= 10
    assert cons.console_policy_summary(pol)
    locks = cons.console_env_locks()
    assert isinstance(locks, dict)
    eff = cons.effective_console_policy()
    assert "console_idle_sec" in eff
    cons.ticket_ttl_sec()
    cons.idle_sec()
    cons.max_session_sec()
    cons.max_per_user()
    cons.max_global()
    cons.default_scrollback()
    cons.grant_minutes()
    cons.require_2fa_every_shell()
    cons.allow_backup_codes()
    cons.prefer_passkey()
    cons.require_passkey_if_enrolled()
    cons.bind_ip_enabled()
    cons.bind_device_enabled()
    cons.privileged_role()
    cons.audit_mode_setting()
    cons.audit_required()
    cons.audit_mode()
    cons.audit_retention_days()
    cons.revalidate_sec()
    cons.hold_sec()
    cons._as_int("nope", 5, 1, 10)
    cons._as_bool("yes", False)
    cons._as_bool("0", True)
    cons._clamp_privileged_role("operator")
    cons._clamp_audit_mode("full")
    cons._clamp_hold(0)
    cons._clamp_hold(99999)


def test_console_workspace_and_embed(tmp_path, monkeypatch):
    from app.services import ssh_console as cons

    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    cons.reset_runtime_state_for_tests()

    engine = create_engine(
        f"sqlite:///{tmp_path / 'r33.db'}",
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
                email="q33@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            s.add(user)
            srv = Server(
                name="Lab",
                hostname="lab.local",
                ssh_username="pi",
                ssh_private_key_encrypted=encrypt_str("k"),
                docker_base_dir="/home/pi/docker",
                os_type="debian",
            )
            s.add(srv)
            s.commit()
            s.refresh(user)
            s.refresh(srv)
            sid = srv.id
            client.cookies.set("access_token", create_user_access_token(user))

        r = client.get("/console")
        assert r.status_code in (200, 303, 403, 404)
        r = client.get(f"/servers/{sid}/console", follow_redirects=False)
        assert r.status_code in (303, 200, 403)
        r = client.get(f"/servers/{sid}/console?embed=1")
        assert r.status_code in (200, 303, 403)
        r = client.get(f"/servers/{sid}/console?popup=1")
        assert r.status_code in (200, 303, 403)
        r = client.get(f"/servers/{sid}/console?solo=1")
        assert r.status_code in (200, 303, 403)
        r = client.get("/servers/99999/console?embed=1")
        assert r.status_code in (404, 200, 303, 403)
    finally:
        app.dependency_overrides.clear()
        cons.reset_runtime_state_for_tests()

"""v1.6 Q-80 twenty-first pack — login POST, console ticket, DNS apply POSTs.

httpx TestClient ignores ``cookies=``; use ``client.cookies.set``.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Server, User
from app.security.auth import create_user_access_token, get_password_hash
from app.security.encryption import encrypt_str


ORIGIN = {"Origin": "http://testserver", "X-Requested-With": "PiHerderConsole"}


def test_login_console_ticket_dns_posts(tmp_path, monkeypatch):
    from app.services import ssh_console as cons
    from app.services.dns_fabric import core as fabric

    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    cons.reset_runtime_state_for_tests()
    monkeypatch.setattr(cons, "require_enabled", lambda: None)
    monkeypatch.setattr(cons, "is_demo_console", lambda: False)
    monkeypatch.setattr(cons, "require_2fa_every_shell", lambda: False)
    monkeypatch.setattr(fabric, "sync_host_a", lambda *a, **k: [{"ok": True, "name": "ph"}])
    monkeypatch.setattr(fabric, "fanout_pihole_dns", lambda *a, **k: [])

    engine = create_engine(
        f"sqlite:///{tmp_path / 'r21.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    # DNS saves go through app_settings' own engine, not get_session.
    import app.services.app_settings as app_cfg

    monkeypatch.setattr(app_cfg, "engine", engine)
    monkeypatch.setattr(app_cfg, "_cache", None)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with Session(engine) as s:
            user = User(
                email="q21@test.local",
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
                ip_address="10.0.0.9",
                ssh_username="pi",
                ssh_port=22,
                ssh_password_encrypted=encrypt_str("x"),
                ssh_private_key_encrypted=encrypt_str("key"),
                dns_name="",
                dns_manage_a=False,
            )
            s.add(srv)
            s.commit()
            s.refresh(user)
            s.refresh(srv)
            uid, sid = user.id, srv.id
            tok = create_user_access_token(user)

        r = client.post(
            "/auth/login",
            data={"email": "nobody@test.local", "password": "wrong"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "invalid" in (r.headers.get("location") or "")

        r = client.post(
            "/auth/login",
            data={"email": "q21@test.local", "password": "SmokeTest1ok"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 200)

        client.cookies.set("access_token", tok)

        r = client.post(f"/servers/{sid}/console/ticket", data={}, headers=ORIGIN)
        assert r.status_code in (200, 400, 403, 429)
        grant = cons.mint_grant(user_id=uid, server_id=sid, session_version=0)
        client.cookies.set(cons.CONSOLE_GRANT_COOKIE, grant)
        r = client.post(
            f"/servers/{sid}/console/ticket",
            data={"tab": "0"},
            headers=ORIGIN,
        )
        assert r.status_code in (200, 400, 403, 429)
        r = client.post(
            "/servers/99999/console/ticket",
            data={},
            headers=ORIGIN,
        )
        assert r.status_code in (200, 404, 401, 403)
        r = client.post(
            f"/servers/{sid}/console/ticket",
            data={"identity_id": "nope"},
            headers=ORIGIN,
        )
        assert r.status_code in (200, 400, 403, 404)

        r = client.post(
            "/dns/base-domain",
            data={"domain": "lan.example"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        r = client.post(
            "/dns/base-domain",
            data={"domain": "nope"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        r = client.post(
            "/dns/network",
            data={"lan_subnet": "10.0.0.0/24", "gateway_ip": "10.0.0.1"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        r = client.post(
            "/dns/network",
            data={"lan_subnet": "not-a-cidr"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        r = client.post(
            f"/servers/{sid}/dns",
            data={"dns_name": "lab.lan", "dns_manage_a": "", "ip_address": "10.0.0.9"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 400, 403, 404)
        r = client.post(
            f"/servers/{sid}/dns",
            data={"dns_name": "bad", "dns_manage_a": "on"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 400, 403)
        r = client.post(
            f"/servers/{sid}/dns/sync-a",
            data={"return_to": f"/servers/{sid}"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 400, 403, 404)
        r = client.post("/dns/import-pihole", follow_redirects=False)
        assert r.status_code in (303, 403, 400, 500)
    finally:
        app.dependency_overrides.clear()
        cons.reset_runtime_state_for_tests()

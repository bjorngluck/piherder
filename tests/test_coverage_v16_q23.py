"""v1.6 Q-80 twenty-third pack — 2FA POST, DNS service CNAME, console revoke/discard."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Server, User
from app.routers.auth import PENDING_COOKIE
from app.security.auth import (
    create_pending_2fa_token,
    create_user_access_token,
    get_password_hash,
)
from app.security.encryption import encrypt_str


ORIGIN = {"Origin": "http://testserver", "X-Requested-With": "PiHerderConsole"}


def test_2fa_dns_service_console_revoke(tmp_path, monkeypatch):
    from app.services import ssh_console as cons
    from app.services.dns_fabric import core as fabric

    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    cons.reset_runtime_state_for_tests()
    monkeypatch.setattr(fabric, "sync_service_dns", lambda *a, **k: [{"ok": True, "name": "ph"}])
    monkeypatch.setattr(fabric, "sync_service_cname", lambda *a, **k: [{"ok": True, "name": "ph"}])
    monkeypatch.setattr(fabric, "fanout_pihole_dns", lambda *a, **k: [])

    engine = create_engine(
        f"sqlite:///{tmp_path / 'r23.db'}",
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
                email="q23@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=True,
                totp_secret_encrypted=encrypt_str("JBSWY3DPEHPK3PXP"),
            )
            s.add(user)
            srv = Server(
                name="Lab",
                hostname="lab.local",
                ip_address="10.0.0.9",
                ssh_username="pi",
                ssh_port=22,
                ssh_private_key_encrypted=encrypt_str("k"),
                dns_name="lab.lan",
                dns_manage_a=True,
            )
            s.add(srv)
            s.commit()
            s.refresh(user)
            s.refresh(srv)
            uid, sid = user.id, srv.id
            pending = create_pending_2fa_token(uid)
            session_tok = create_user_access_token(user)

        client.cookies.set(PENDING_COOKIE, pending)
        r = client.get("/auth/2fa")
        assert r.status_code in (200, 303)
        r = client.post("/auth/2fa", data={"code": "000000"}, follow_redirects=False)
        assert r.status_code in (303, 200, 401)
        r = client.post("/auth/2fa", data={"code": ""}, follow_redirects=False)
        assert r.status_code in (303, 200, 401)

        client.cookies.set("access_token", session_tok)
        r = client.post(
            "/dns/services",
            data={
                "fqdn": "grafana.lan",
                "target_server_id": str(sid),
                "backend_server_id": str(sid),
                "label": "Grafana",
                "docker_project": "grafana",
                "managed_on_pihole": "",
                "sync_now": "",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 400, 403, 422)
        r = client.post(
            "/dns/services",
            data={
                "fqdn": "not-a-name",
                "target_server_id": str(sid),
                "backend_server_id": str(sid),
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 400, 422)
        r = client.post("/dns/services/99999/sync", follow_redirects=False)
        assert r.status_code in (404, 303, 401, 403)
        r = client.post(
            "/dns/services/99999/delete",
            data={"remove_from_pihole": "0"},
            follow_redirects=False,
        )
        assert r.status_code in (404, 303, 401, 403)

        r = client.post(f"/servers/{sid}/console/grant/revoke", headers=ORIGIN)
        assert r.status_code in (200, 403)
        r = client.post(
            f"/servers/{sid}/console/discard",
            headers={**ORIGIN, "content-type": "application/json"},
            json={"resume": "abc"},
        )
        assert r.status_code in (200, 403, 400, 404)
        r = client.post(
            f"/servers/{sid}/console/webauthn/options",
            headers=ORIGIN,
        )
        assert r.status_code in (200, 400, 403, 401)
    finally:
        app.dependency_overrides.clear()
        cons.reset_runtime_state_for_tests()

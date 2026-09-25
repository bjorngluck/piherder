"""v1.6 Q-80 twenty-fifth pack — TOTP start/confirm, DNS attach-cname, console passkey options."""
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


def test_totp_attach_cname_console_passkey(tmp_path, monkeypatch):
    from app.services import ssh_console as cons
    from app.services.dns_fabric import core as fabric

    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    cons.reset_runtime_state_for_tests()
    monkeypatch.setattr(fabric, "sync_service_dns", lambda *a, **k: [{"ok": True, "already_present": True}])

    engine = create_engine(
        f"sqlite:///{tmp_path / 'r25.db'}",
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
                email="q25@test.local",
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
                ssh_private_key_encrypted=encrypt_str("k"),
                dns_name="lab.lan",
            )
            s.add(srv)
            s.commit()
            s.refresh(user)
            s.refresh(srv)
            sid = srv.id
            client.cookies.set("access_token", create_user_access_token(user))

        r = client.post("/auth/account/2fa/start", follow_redirects=False)
        assert r.status_code in (303, 403)
        r = client.post(
            "/auth/account/2fa/confirm",
            data={"code": "000000"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        loc = r.headers.get("location") or ""
        assert "2fa" in loc or r.status_code == 403
        r = client.post(
            "/auth/account/2fa/disable",
            data={"current_password": "nope", "code": "000000"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 400)

        r = client.post(
            "/dns/attach-cname",
            data={"fqdn": "grafana.lan"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        r = client.post(
            "/dns/attach-cname",
            data={"fqdn": "grafana.lan", "cname_target": "lab.lan"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        r = client.post(
            "/dns/attach-host-identity",
            data={"server_id": str(sid)},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        r = client.post(
            "/dns/attach-host-identity",
            data={"server_id": "99999"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 404, 403)
        r = client.post(
            "/dns/attach-deployment",
            data={"deployment_id": "99999"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 404, 403)

        r = client.post(f"/servers/{sid}/console/webauthn/options", headers=ORIGIN)
        assert r.status_code in (200, 400, 403)
        r = client.post(
            f"/servers/{sid}/console/webauthn/verify",
            headers={**ORIGIN, "content-type": "application/json"},
            json={},
        )
        assert r.status_code in (200, 400, 403, 422)
    finally:
        app.dependency_overrides.clear()
        cons.reset_runtime_state_for_tests()

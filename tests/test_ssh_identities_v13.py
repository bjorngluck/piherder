"""v1.3 slice 4 Deep — W-id fleet + privileged SSH identities."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import Server, ServerSshIdentity, User
from app.security.auth import create_access_token, get_password_hash
from app.services import app_settings as cfg
from app.services import ssh as ssh_service
from app.services import ssh_console as cons
from app.services import ssh_identities as ident_svc
from app.services import ssh_onboarding


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'wid.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture()
def client(engine, monkeypatch):
    store: dict = {}

    def fake_load():
        return dict(store)

    def fake_write(data: dict):
        store.clear()
        store.update(data)

    monkeypatch.setattr(cfg, "_load_raw_from_db", fake_load)
    monkeypatch.setattr(cfg, "_write_raw_to_db", fake_write)
    cfg.clear_cache()
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_BIND_IP", False)
    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE_BIND_DEVICE", False)
    monkeypatch.setattr(cons, "bind_ip_enabled", lambda: False)
    monkeypatch.setattr(cons, "bind_device_enabled", lambda: False)
    cons.reset_runtime_state_for_tests()

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    tc = TestClient(app, raise_server_exceptions=False)
    try:
        yield tc, engine
    finally:
        app.dependency_overrides.clear()
        cons.reset_runtime_state_for_tests()
        cfg.clear_cache()


def _user(session: Session, *, role: str = "admin", email: str = "wid@test.local") -> User:
    u = User(
        email=email,
        hashed_password=get_password_hash("SmokeTest1ok"),
        role=role,
        is_active=True,
        must_change_password=False,
        totp_enabled=True,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _cookie(uid: int) -> dict[str, str]:
    return {"access_token": create_access_token({"sub": str(uid)})}


def _origin_headers() -> dict[str, str]:
    return {
        "Origin": "http://testserver",
        "X-Requested-With": "PiHerderConsole",
    }


def _server_with_key(session: Session) -> Server:
    pub, priv = ssh_service.generate_keypair(comment="test-fleet")
    from app.security import encryption

    srv = Server(
        name="lab-core",
        hostname="lab.local",
        ssh_username="piherder",
        ssh_private_key_encrypted=encryption.encrypt_str(priv),
        ssh_public_key=pub,
    )
    session.add(srv)
    session.commit()
    session.refresh(srv)
    ident_svc.ensure_fleet_identity(session, srv)
    session.commit()
    session.refresh(srv)
    return srv


def test_ensure_fleet_dual_write(engine):
    with Session(engine) as s:
        srv = _server_with_key(s)
        row = ident_svc.get_by_role(s, srv.id, ident_svc.ROLE_FLEET)
        assert row is not None
        assert row.username == "piherder"
        assert row.private_key_encrypted == srv.ssh_private_key_encrypted
        assert row.key_fingerprint
        assert row.key_fingerprint.startswith("SHA256:")


def test_add_and_remove_privileged(engine):
    with Session(engine) as s:
        srv = _server_with_key(s)
        priv = ident_svc.add_privileged(s, srv, username="piherder-admin", generate=True)
        s.commit()
        assert priv.role == ident_svc.ROLE_PRIVILEGED
        assert priv.username == "piherder-admin"
        assert priv.private_key_encrypted
        assert priv.key_fingerprint
        fleet = ident_svc.get_by_role(s, srv.id, ident_svc.ROLE_FLEET)
        assert fleet.username == "piherder"
        with pytest.raises(ident_svc.IdentityError):
            ident_svc.add_privileged(s, srv, username="root", generate=True)
        ident_svc.remove_privileged(s, priv)
        s.commit()
        assert ident_svc.get_by_role(s, srv.id, ident_svc.ROLE_PRIVILEGED) is None
        with pytest.raises(ident_svc.IdentityError):
            ident_svc.remove_privileged(s, fleet)


def test_overlay_privileged_drops_password(engine):
    from app.security import encryption

    with Session(engine) as s:
        srv = _server_with_key(s)
        srv.ssh_password_encrypted = encryption.encrypt_str("secret")
        s.add(srv)
        priv = ident_svc.add_privileged(s, srv, username="rootish", generate=True)
        s.commit()
        fleet_snap = ident_svc.overlay_server_for_identity(srv, None)
        assert fleet_snap.ssh_username == "piherder"
        assert fleet_snap.ssh_password_encrypted
        priv_snap = ident_svc.overlay_server_for_identity(srv, priv)
        assert priv_snap.ssh_username == "rootish"
        assert priv_snap.ssh_password_encrypted is None
        assert priv_snap.ssh_private_key_encrypted != srv.ssh_private_key_encrypted


def test_mint_ticket_carries_identity():
    tok = cons.mint_ticket(
        user_id=1,
        server_id=2,
        session_version=0,
        identity_id=9,
        identity_role="privileged",
        reason="need apt",
    )
    payload = cons.consume_ticket(tok, user_id=1, server_id=2, session_version=0)
    assert int(payload["iid"]) == 9
    assert payload["role"] == "privileged"
    assert payload["why"] == "need apt"


def test_legacy_ticket_without_iid_still_consumes():
    tok = cons.mint_ticket(user_id=1, server_id=2, session_version=0)
    payload = cons.consume_ticket(tok, user_id=1, server_id=2, session_version=0)
    assert payload.get("console") is True
    assert payload.get("iid") is None


def test_can_open_privileged_rbac(monkeypatch):
    monkeypatch.setattr(cons, "is_demo_console", lambda: False)
    monkeypatch.setattr(cons, "privileged_role", lambda: "admin")
    admin = SimpleNamespace(role="admin", is_active=True)
    op = SimpleNamespace(role="operator", is_active=True)
    viewer = SimpleNamespace(role="viewer", is_active=True)
    assert cons.can_open_privileged(admin) is True
    assert cons.can_open_privileged(op) is False
    assert cons.can_open_privileged(viewer) is False
    monkeypatch.setattr(cons, "privileged_role", lambda: "operator")
    assert cons.can_open_privileged(op) is True
    monkeypatch.setattr(cons, "is_demo_console", lambda: True)
    assert cons.can_open_privileged(admin) is False


def test_stepup_proof_single_use():
    proof = cons.mint_stepup_proof(user_id=4, session_version=1)
    assert cons.consume_stepup_proof(proof, user_id=4, session_version=1) is True
    assert cons.consume_stepup_proof(proof, user_id=4, session_version=1) is False


def test_privileged_setup_script_mentions_sudo():
    pub, _priv = ssh_service.generate_keypair(comment="priv")
    script = ssh_onboarding.build_privileged_user_script("piherder-admin", pub)
    assert "piherder-admin" in script
    assert "NOPASSWD" in script
    assert "HAOS" in script
    assert "console-only" in script.lower() or "console only" in script.lower()


def test_http_add_privileged_and_detail_card(client):
    tc, engine = client
    with Session(engine) as s:
        uid = _user(s).id
        srv = _server_with_key(s)
        sid = srv.id
    r = tc.post(
        f"/servers/{sid}/ssh/identities/privileged",
        data={"ssh_username": "piherder-admin", "key_mode": "generate", "label": "Privileged"},
        cookies=_cookie(uid),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "identity_added" in (r.headers.get("location") or "")
    page = tc.get(f"/servers/{sid}", cookies=_cookie(uid))
    assert page.status_code == 200
    assert 'data-testid="ssh-fleet-card"' in page.text
    assert 'data-testid="ssh-privileged-card"' in page.text
    assert "piherder-admin" in page.text
    assert 'data-testid="ssh-fleet-pubkey"' in page.text
    assert 'data-testid="ssh-privileged-pubkey"' in page.text
    assert "Fleet public key" in page.text
    assert "Privileged public key" in page.text
    with Session(engine) as s:
        row = s.exec(
            select(ServerSshIdentity).where(
                ServerSshIdentity.server_id == sid,
                ServerSshIdentity.role == "privileged",
            )
        ).first()
        assert row is not None
        assert row.username == "piherder-admin"


def test_http_privileged_mint_rules(client, monkeypatch):
    tc, engine = client
    with Session(engine) as s:
        admin = _user(s, role="admin", email="adm@test.local")
        op = _user(s, role="operator", email="op@test.local")
        srv = _server_with_key(s)
        priv = ident_svc.add_privileged(s, srv, username="piherder-admin", generate=True)
        s.commit()
        sid, iid = srv.id, priv.id
        admin_id, op_id = admin.id, op.id

    headers = _origin_headers()

    # Missing confirm
    r = tc.post(
        f"/servers/{sid}/console/ticket",
        data={"identity_id": str(iid), "totp_code": ""},
        cookies=_cookie(admin_id),
        headers=headers,
    )
    assert r.status_code == 400
    assert r.json().get("error") == "privileged_confirm"

    # Confirm but no 2FA / step-up — fleet grant is not enough
    grant = cons.mint_grant(user_id=admin_id, server_id=sid, session_version=0)
    r = tc.post(
        f"/servers/{sid}/console/ticket",
        data={"identity_id": str(iid), "confirm_privileged": "1"},
        cookies={**_cookie(admin_id), cons.CONSOLE_GRANT_COOKIE: grant},
        headers=headers,
    )
    assert r.status_code == 403
    assert r.json().get("error") in ("2fa_required", "enroll_2fa", "2fa_bad_code")

    # Operator blocked when knob is admin
    cfg.save_settings({"console_privileged_role": "admin"})
    r = tc.post(
        f"/servers/{sid}/console/ticket",
        data={"identity_id": str(iid), "confirm_privileged": "1"},
        cookies=_cookie(op_id),
        headers=headers,
    )
    assert r.status_code == 403
    assert r.json().get("error") == "privileged_forbidden"

    # Operator allowed when knob is operator + step-up proof
    cfg.save_settings({"console_privileged_role": "operator"})
    proof = cons.mint_stepup_proof(user_id=op_id, session_version=0)
    r = tc.post(
        f"/servers/{sid}/console/ticket",
        data={"identity_id": str(iid), "confirm_privileged": "1", "reason": "apt upgrade"},
        cookies={**_cookie(op_id), cons.CONSOLE_STEPUP_COOKIE: proof},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("identity_role") == "privileged"
    from app.security.auth import decode_token_payload

    payload = decode_token_payload(body["ticket"])
    assert payload.get("role") == "privileged"
    assert payload.get("why") == "apt upgrade"


def test_http_demo_privileged_denied(client, monkeypatch):
    from app.services import demo as demo_svc

    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    tc, engine = client
    with Session(engine) as s:
        uid = _user(s).id
        srv = _server_with_key(s)
        priv = ident_svc.add_privileged(s, srv, username="piherder-admin", generate=True)
        s.commit()
        sid, iid = srv.id, priv.id
    r = tc.post(
        f"/servers/{sid}/console/ticket",
        data={"identity_id": str(iid), "confirm_privileged": "1"},
        cookies=_cookie(uid),
        headers=_origin_headers(),
    )
    assert r.status_code == 403
    assert r.json().get("error") == "demo_privileged"


def test_http_console_settings_privileged_role(client):
    tc, engine = client
    with Session(engine) as s:
        uid = _user(s).id
    r = tc.post(
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
            "console_bind_ip": "1",
            "console_bind_device": "1",
            "console_privileged_role": "operator",
        },
        cookies=_cookie(uid),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert cons.privileged_role() == "operator"


def test_http_cannot_remove_without_name(client):
    tc, engine = client
    with Session(engine) as s:
        uid = _user(s).id
        srv = _server_with_key(s)
        priv = ident_svc.add_privileged(s, srv, username="piherder-admin", generate=True)
        s.commit()
        sid, iid = srv.id, priv.id
    r = tc.post(
        f"/servers/{sid}/ssh/identities/{iid}/remove",
        data={"confirm_name": "wrong"},
        cookies=_cookie(uid),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "identity_fail" in (r.headers.get("location") or "")
    r = tc.post(
        f"/servers/{sid}/ssh/identities/{iid}/remove",
        data={"confirm_name": "lab-core"},
        cookies=_cookie(uid),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "identity_removed" in (r.headers.get("location") or "")
    with Session(engine) as s:
        assert ident_svc.get_by_role(s, sid, ident_svc.ROLE_PRIVILEGED) is None


def test_purge_on_delete(engine):
    from app.services.server_lifecycle import delete_server_from_fleet

    with Session(engine) as s:
        u = _user(s)
        srv = _server_with_key(s)
        ident_svc.add_privileged(s, srv, username="piherder-admin", generate=True)
        s.commit()
        sid = srv.id
        delete_server_from_fleet(s, srv, confirm_name="lab-core", user_id=u.id)
    with Session(engine) as s:
        rows = s.exec(
            select(ServerSshIdentity).where(ServerSshIdentity.server_id == sid)
        ).all()
        assert rows == []

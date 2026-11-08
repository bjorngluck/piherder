"""Cover missing console, wizard, SSH, and patch route lines (v1.15).

SSH, host probes, and the job queue are stubbed. The database is sqlite.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, WebSocketDisconnect
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.requests import Request

from app.database import get_session
from app.main import app
from app.models import Job, Server, ServerSshIdentity, User
from app.routers import server_console as console_mod
from app.routers import server_patch as patch_mod
from app.routers import server_ssh as ssh_mod
from app.routers import server_wizard as wiz
from app.security import encryption
from app.security.auth import (
    _auth_attempts,
    create_access_token,
    create_pending_2fa_token,
    get_password_hash,
)
from app.services import host_deps as host_deps_svc
from app.services import jobs as job_service
from app.services import ssh as ssh_service
from app.services import ssh_console as cons
from app.services import ssh_identities as ident_svc
from app.services import ssh_onboarding
from app.services import webauthn_svc as wa_svc
from app.services.ssh_onboarding import OnboardingResult

PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterialForTests user@example.com"
ORIGIN = {"origin": "http://testserver"}


def _engine(path):
    eng = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


def _ok(message="ok", **details):
    return OnboardingResult(True, message, details or {})


def _bad(message="failed", **details):
    return OnboardingResult(False, message, details or {})


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = _engine(tmp_path / "q90.db")
    monkeypatch.setattr(encryption, "encrypt_str", lambda text: "enc:" + (text or ""))
    monkeypatch.setattr(encryption, "decrypt_str", lambda text: str(text or "")[4:])
    monkeypatch.setattr(
        ssh_service,
        "generate_keypair",
        lambda comment="": (PUB, "FAKE-PRIVATE-KEY"),
    )
    monkeypatch.setattr(
        ssh_service,
        "get_ssh_client",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ssh blocked")),
    )
    monkeypatch.setattr(
        ssh_service,
        "get_private_key_plain",
        lambda *a, **k: "FAKE-PRIVATE-KEY",
    )

    def _clear_pin(server):
        server.ssh_hostkey_type = None
        server.ssh_hostkey_b64 = None
        server.ssh_hostkey_fp = None

    monkeypatch.setattr(ssh_service, "clear_host_key_pin", _clear_pin)
    monkeypatch.setattr(
        ssh_onboarding,
        "test_connection_detail",
        lambda *a, **k: _ok("connected"),
    )
    monkeypatch.setattr(
        ssh_onboarding,
        "deploy_public_key",
        lambda *a, **k: _ok(
            "deployed",
            public_key=PUB + " derived",
            already_auth=False,
            installed=True,
            already_present=False,
        ),
    )
    monkeypatch.setattr(
        ssh_onboarding,
        "rotate_keypair",
        lambda *a, **k: _ok(
            "rotated",
            new_public_key=PUB,
            new_private_key="FAKE-PRIVATE-KEY",
            removed_old=True,
            installed=True,
        ),
    )
    monkeypatch.setattr(
        ssh_onboarding,
        "provision_least_priv_user",
        lambda *a, **k: _ok("provisioned", new_username="svc", os={"name": "debian"}),
    )
    monkeypatch.setattr(
        host_deps_svc,
        "check_and_persist",
        lambda *a, **k: {
            "overall": "ok",
            "checks": [{"id": "rsync", "status": "ok", "required": True}],
        },
    )
    patch_mod._orig_sync_for_tests = patch_mod.sync_server_schedules
    monkeypatch.setattr(patch_mod, "sync_server_schedules", lambda server: None)
    monkeypatch.setattr(cons, "console_enabled", lambda: True)
    monkeypatch.setattr(console_mod, "get_engine", lambda: engine)

    def _create(background_tasks, session, server, job_type, user_id=None, **kwargs):
        job = Job(
            server_id=getattr(server, "id", None),
            job_type=job_type,
            status="pending",
            details=json.dumps({"summary": "queued"}),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job

    monkeypatch.setattr(job_service, "create_job_and_run", _create)
    monkeypatch.setattr(
        job_service,
        "enqueue_os_update_check",
        lambda *a, **k: SimpleNamespace(id=1, status="pending"),
    )
    monkeypatch.setattr(
        job_service,
        "enqueue_container_update_check",
        lambda *a, **k: SimpleNamespace(id=2, status="pending"),
    )

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    cons.reset_runtime_state_for_tests()
    _auth_attempts.clear()
    tc = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    with Session(engine) as session:
        user = User(
            email="user@example.com",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
            totp_secret_encrypted="enc:totp",
        )
        server = Server(
            name="host-a",
            hostname="host-a.example.com",
            ip_address="192.0.2.10",
            ssh_port=22,
            ssh_username="user",
            ssh_private_key_encrypted="enc:key",
            ssh_public_key=PUB,
            os_type="debian",
            docker_base_dir="/home/user/docker",
        )
        session.add(user)
        session.add(server)
        session.commit()
        session.refresh(user)
        session.refresh(server)
        uid, sid = int(user.id), int(server.id)
    tc.cookies.set("access_token", create_access_token({"sub": str(uid)}))
    try:
        yield tc, engine, uid, sid
    finally:
        app.dependency_overrides.clear()
        cons.reset_runtime_state_for_tests()
        _auth_attempts.clear()
        engine.dispose()


def _user(engine, uid) -> User:
    with Session(engine) as session:
        return session.get(User, uid)


def test_console_helpers_and_workspace(client, monkeypatch):
    tc, engine, uid, sid = client
    user = _user(engine, uid)
    with Session(engine) as session:
        fresh = session.get(User, uid)
        assert console_mod._verify_console_2fa(session, fresh, totp_code="") == (
            False,
            "2fa_required",
        )
        monkeypatch.setattr(wa_svc, "has_passkeys", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pk")))
        assert console_mod._verify_console_2fa(session, fresh, totp_code="123456")[0] is False
        monkeypatch.setattr(wa_svc, "has_passkeys", lambda *a, **k: True)
        monkeypatch.setattr(cons, "require_passkey_if_enrolled", lambda: True)
        assert console_mod._verify_console_2fa(session, fresh, totp_code="123456") == (
            False,
            "passkey_required",
        )
        monkeypatch.setattr(cons, "require_passkey_if_enrolled", lambda: False)
        monkeypatch.setattr("app.security.auth.decrypt_totp_secret", lambda cipher: "SECRET")
        monkeypatch.setattr("app.security.auth.verify_totp_code", lambda secret, code: code == "123456")
        monkeypatch.setattr("app.security.auth.consume_backup_code", lambda *a, **k: True)
        assert console_mod._verify_console_2fa(session, fresh, totp_code="123456") == (True, "")
        monkeypatch.setattr(
            "app.security.auth.decrypt_totp_secret",
            lambda cipher: (_ for _ in ()).throw(RuntimeError("bad secret")),
        )
        monkeypatch.setattr(cons, "allow_backup_codes", lambda: True)
        assert console_mod._verify_console_2fa(session, fresh, totp_code="backup-code") == (True, "")
        monkeypatch.setattr("app.security.auth.consume_backup_code", lambda *a, **k: False)
        assert console_mod._verify_console_2fa(session, fresh, totp_code="backup-code")[1] == "2fa_bad_code"
        monkeypatch.setattr(cons, "allow_backup_codes", lambda: False)
        assert console_mod._verify_console_2fa(session, fresh, totp_code="backup-code") == (
            False,
            "backup_not_allowed",
        )
        assert console_mod._verify_console_2fa(session, fresh, totp_code="000000")[1] == "2fa_bad_code"
        bare = User(
            email="plain@example.com",
            hashed_password="x",
            role="admin",
            totp_enabled=False,
        )
        assert console_mod._verify_console_2fa(session, bare, totp_code="123456") == (False, "enroll_2fa")
        console_mod._audit_2fa_denied(
            session, user_id=uid, server_id=sid, err="2fa_required", ip="192.0.2.10"
        )
        console_mod._audit_2fa_denied(
            session,
            user_id=uid,
            server_id=sid,
            err="2fa_bad_code",
            ip="192.0.2.10",
            privileged=True,
        )

    assert console_mod._parse_identity_id(None) is None
    assert console_mod._parse_identity_id("  ") is None
    assert console_mod._parse_identity_id("nope") is None
    assert console_mod._parse_identity_id("0") is None
    assert console_mod._parse_identity_id("4") == 4
    url = wiz.wizard_path("nope", 5, msg="m" * 250, error="", detail="detail")
    assert "step=identity" in url and "server_id=5" in url

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/servers/1/console/ticket",
        "raw_path": b"/servers/1/console/ticket",
        "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"sec-fetch-site", b"cross-site")],
        "client": ("192.0.2.10", 1111),
        "server": ("testserver", 80),
        "app": app,
    }
    blocked = console_mod._reject_cross_site(Request(scope))
    assert blocked is not None and blocked.status_code == 403

    monkeypatch.setattr(cons, "is_demo_console", lambda: True)
    seen = {"n": 0}

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return list(self._rows)

    extra = SimpleNamespace(
        id=7,
        name="host-b",
        hostname="host-b.example.com",
        ip_address="192.0.2.20",
        ssh_private_key_encrypted=None,
        ssh_password_encrypted=None,
    )
    listed = SimpleNamespace(
        id=sid,
        name="host-a",
        hostname="",
        ip_address="192.0.2.10",
        ssh_private_key_encrypted=None,
        ssh_password_encrypted=None,
    )

    class _Split:
        def exec(self, *a, **k):
            seen["n"] += 1
            if seen["n"] == 1:
                raise RuntimeError("no sort_order")
            return _Rows([listed])

        def get(self, model, key):
            if int(key) == 7:
                return extra
            return None

    ws_scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/console",
        "raw_path": b"/console",
        "query_string": b"hosts=bad,%2C,0,-2,7,7,8",
        "headers": [(b"host", b"testserver")],
        "client": ("192.0.2.8", 1111),
        "server": ("testserver", 80),
        "app": app,
    }
    response = asyncio.run(
        console_mod.console_workspace(
            Request(ws_scope),
            _Split(),
            user,
            host="zz",
            hosts="bad, , 0, -2, 7, 7, 8",
        )
    )
    assert response.status_code == 200

    page = tc.get(f"/servers/{sid}/console?embed=1&popup=1")
    assert page.status_code == 200, page.text[:200]
    monkeypatch.setattr(wa_svc, "has_passkeys", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pk")))
    demo_page = tc.get(f"/servers/{sid}/console?embed=1")
    assert demo_page.status_code == 200, demo_page.text[:200]
    missing = tc.get("/servers/99999/console?embed=1")
    assert missing.status_code == 303


def test_console_http_routes(client, monkeypatch):
    tc, engine, uid, sid = client
    ticket = f"/servers/{sid}/console/ticket"
    cross = tc.post(ticket, data={"totp_code": ""})
    assert cross.status_code == 403
    monkeypatch.setattr(console_mod, "rate_limit_auth", lambda *a, **k: False)
    limited = tc.post(ticket, data={"totp_code": ""}, headers=ORIGIN)
    assert limited.status_code == 429
    monkeypatch.setattr(console_mod, "rate_limit_auth", lambda *a, **k: True)
    missing = tc.post("/servers/99999/console/ticket", data={}, headers=ORIGIN)
    assert missing.status_code == 404
    monkeypatch.setattr(cons, "console_enabled", lambda: False)
    disabled = tc.post(ticket, data={}, headers=ORIGIN)
    assert disabled.status_code == 403
    monkeypatch.setattr(cons, "console_enabled", lambda: True)

    ghost = tc.post(ticket, data={"identity_id": "99999"}, headers=ORIGIN)
    assert ghost.status_code == 400

    with Session(engine) as session:
        row = ServerSshIdentity(
            server_id=sid,
            role="privileged",
            label="Privileged",
            username="admin",
            private_key_encrypted=None,
            enabled=True,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        priv_id = int(row.id)
    bare = tc.post(
        ticket,
        data={"identity_id": str(priv_id), "confirm_privileged": "1"},
        headers=ORIGIN,
    )
    assert bare.status_code == 400

    with Session(engine) as session:
        server = session.get(Server, sid)
        server.ssh_private_key_encrypted = None
        server.ssh_password_encrypted = None
        server.ssh_public_key = None
        session.add(server)
        session.commit()
    no_ssh = tc.post(ticket, data={"identity_id": ""}, headers=ORIGIN)
    assert no_ssh.status_code == 400
    with Session(engine) as session:
        server = session.get(Server, sid)
        server.ssh_private_key_encrypted = "enc:key"
        server.ssh_public_key = PUB
        session.add(server)
        session.commit()

    monkeypatch.setattr(cons, "is_demo_console", lambda: True)
    monkeypatch.setattr(cons, "mint_ticket", lambda **k: "ticket-demo")
    monkeypatch.setattr(cons, "slots_remaining", lambda user_id: 2)
    demo = tc.post(ticket, data={"tab": "1"}, headers=ORIGIN)
    assert demo.status_code == 200 and demo.json()["ok"] is True
    monkeypatch.setattr(cons, "is_demo_console", lambda: False)

    monkeypatch.setattr(console_mod, "_verify_console_2fa", lambda *a, **k: (True, ""))
    monkeypatch.setattr(cons, "grant_valid", lambda *a, **k: False)
    monkeypatch.setattr(cons, "require_2fa_every_shell", lambda: False)
    monkeypatch.setattr(cons, "slots_remaining", lambda user_id: 0)
    limited_slots = tc.post(ticket, data={"totp_code": "123456"}, headers=ORIGIN)
    assert limited_slots.status_code == 429
    monkeypatch.setattr(cons, "slots_remaining", lambda user_id: 3)
    monkeypatch.setattr(cons, "mint_ticket", lambda **k: "ticket-ok")
    minted = tc.post(ticket, data={"totp_code": "123456", "tab": "nope"}, headers=ORIGIN)
    assert minted.status_code == 200, minted.text[:300]
    assert minted.json()["ticket"] == "ticket-ok"

    def _raise_disabled(**k):
        raise cons.ConsoleDisabled("off")

    def _raise_denied(**k):
        raise cons.ConsoleDenied("no")

    monkeypatch.setattr(cons, "mint_ticket", _raise_disabled)
    assert tc.post(ticket, data={"tab": "0"}, headers=ORIGIN).status_code == 403
    monkeypatch.setattr(cons, "mint_ticket", _raise_denied)
    assert tc.post(ticket, data={"tab": "0"}, headers=ORIGIN).status_code == 403

    opt = f"/servers/{sid}/console/webauthn/options"
    verify = f"/servers/{sid}/console/webauthn/verify"
    assert tc.post(opt).status_code == 403
    monkeypatch.setattr(cons, "is_demo_console", lambda: True)
    assert tc.post(opt, headers=ORIGIN).status_code == 400
    monkeypatch.setattr(cons, "is_demo_console", lambda: False)
    monkeypatch.setattr(cons, "console_enabled", lambda: False)
    assert tc.post(opt, headers=ORIGIN).status_code == 403
    monkeypatch.setattr(cons, "console_enabled", lambda: True)
    monkeypatch.setattr(
        wa_svc,
        "authentication_options_json",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no passkey")),
    )
    assert tc.post(opt, headers=ORIGIN).status_code == 400
    monkeypatch.setattr(
        wa_svc,
        "authentication_options_json",
        lambda *a, **k: ('{"publicKey": {"challenge": "x"}}', "chal"),
    )
    ready = tc.post(opt, headers=ORIGIN)
    assert ready.status_code == 200 and ready.json()["ok"] is True

    assert tc.post(verify).status_code == 403
    monkeypatch.setattr(cons, "is_demo_console", lambda: True)
    assert tc.post(verify, headers=ORIGIN).status_code == 400
    monkeypatch.setattr(cons, "is_demo_console", lambda: False)
    monkeypatch.setattr(cons, "console_enabled", lambda: False)
    assert tc.post(verify, headers=ORIGIN, json={"credential": {}}).status_code == 403
    monkeypatch.setattr(cons, "console_enabled", lambda: True)
    bad_json = tc.post(
        verify,
        headers={**ORIGIN, "content-type": "application/json"},
        content=b"not-json",
    )
    assert bad_json.status_code == 400
    assert tc.post(verify, headers=ORIGIN, json={"credential": "x"}).status_code == 400
    monkeypatch.setattr(
        wa_svc,
        "verify_authentication",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad signature")),
    )
    assert tc.post(verify, headers=ORIGIN, json={"credential": {"id": "a"}}).status_code == 403
    monkeypatch.setattr(wa_svc, "verify_authentication", lambda *a, **k: None)
    monkeypatch.setattr(cons, "require_2fa_every_shell", lambda: True)
    monkeypatch.setattr(cons, "slots_remaining", lambda user_id: 0)
    assert tc.post(verify, headers=ORIGIN, json={"credential": {"id": "a"}}).status_code == 429
    monkeypatch.setattr(cons, "slots_remaining", lambda user_id: 2)

    def _mint_denied(**k):
        raise cons.ConsoleDenied("no")

    monkeypatch.setattr(cons, "mint_ticket", _mint_denied)
    assert tc.post(verify, headers=ORIGIN, json={"credential": {"id": "a"}}).status_code == 403
    monkeypatch.setattr(cons, "mint_ticket", lambda **k: "ticket-wa")
    issued = tc.post(verify, headers=ORIGIN, json={"credential": {"id": "a"}})
    assert issued.status_code == 200 and issued.json().get("ticket") == "ticket-wa"
    monkeypatch.setattr(cons, "require_2fa_every_shell", lambda: False)
    granted = tc.post(verify, headers=ORIGIN, json={"credential": {"id": "a"}})
    assert granted.status_code == 200 and granted.json()["grant_active"] is True

    assert tc.post(f"/servers/{sid}/console/grant/revoke").status_code == 403
    assert tc.post(f"/servers/{sid}/console/grant/revoke", headers=ORIGIN).status_code == 200
    discard = f"/servers/{sid}/console/discard"
    assert tc.post(discard).status_code == 403
    monkeypatch.setattr(cons, "console_enabled", lambda: False)
    assert tc.post(discard, headers=ORIGIN, json={"resume": "abc"}).status_code == 403
    monkeypatch.setattr(cons, "console_enabled", lambda: True)
    monkeypatch.setattr(cons, "discard_parked_for_user", lambda *a, **k: True)
    freed = tc.post(discard, headers=ORIGIN, json={"resume": "abc", "resumes": ["def", " "]})
    assert freed.status_code == 200 and freed.json()["freed"] >= 1
    formed = tc.post(
        discard,
        headers=ORIGIN,
        data={"resume": "abc", "resumes": "def"},
    )
    assert formed.status_code == 200, formed.text[:300]
    broken = tc.post(
        discard,
        headers={**ORIGIN, "content-type": "application/json"},
        content=b"not-json",
    )
    assert broken.status_code == 400
    assert tc.post(discard, headers=ORIGIN, json={}).status_code == 400

    with Session(engine) as session:
        viewer = User(
            email="viewer@example.com",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="viewer",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
            totp_secret_encrypted="enc:totp",
        )
        quiet = User(
            email="quiet@example.com",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="operator",
            is_active=False,
            must_change_password=False,
            totp_enabled=True,
            totp_secret_encrypted="enc:totp",
        )
        bumped = User(
            email="bumped@example.com",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="operator",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
            totp_secret_encrypted="enc:totp",
            session_version=4,
        )
        session.add(viewer)
        session.add(quiet)
        session.add(bumped)
        session.commit()
        session.refresh(viewer)
        session.refresh(quiet)
        session.refresh(bumped)
        admin = session.get(User, uid)

        class _Sock:
            def __init__(self, cookies):
                self.cookies = cookies

        assert console_mod._user_from_cookie(_Sock({}), session) is None
        token = create_access_token({"sub": str(uid)})
        assert console_mod._user_from_cookie(_Sock({"access_token": "Bearer " + token}), session).id == uid
        assert console_mod._user_from_cookie(_Sock({"access_token": "not-a-token"}), session) is None
        pending = create_pending_2fa_token(uid)
        assert console_mod._user_from_cookie(_Sock({"access_token": pending}), session) is None
        bad_sub = create_access_token({"sub": "abc"})
        assert console_mod._user_from_cookie(_Sock({"access_token": bad_sub}), session) is None
        quiet_tok = create_access_token({"sub": str(quiet.id)})
        assert console_mod._user_from_cookie(_Sock({"access_token": quiet_tok}), session) is None
        bump_tok = create_access_token({"sub": str(bumped.id), "sv": 0})
        assert console_mod._user_from_cookie(_Sock({"access_token": bump_tok}), session) is None
        view_tok = create_access_token({"sub": str(viewer.id)})
        assert console_mod._user_from_cookie(_Sock({"access_token": view_tok}), session) is None
        monkeypatch.setattr(cons, "is_demo_console", lambda: True)
        assert console_mod._user_from_cookie(_Sock({"access_token": view_tok}), session).id == viewer.id
        monkeypatch.setattr(cons, "is_demo_console", lambda: False)
        assert console_mod._user_from_cookie(_Sock({"access_token": token}), session).id == admin.id

    async def _hold(tag):
        seen = {"n": 0}

        def _get(resume_id):
            seen["n"] += 1
            if tag == "none":
                return None
            if tag == "eof":
                return object()
            if tag == "why":
                return object()
            if tag == "boom":
                raise RuntimeError("held")
            return None

        def _drain(held):
            return tag != "eof"

        def _why(held):
            return "idle" if tag == "why" else None

        monkeypatch.setattr(cons, "get_held", _get)
        monkeypatch.setattr(cons, "drain_held_channel", _drain)
        monkeypatch.setattr(cons, "held_should_expire", _why)
        monkeypatch.setattr(cons, "destroy_held", lambda *a, **k: True)
        orig = asyncio.sleep

        async def _fast(_delay=0):
            if tag == "cancel":
                raise asyncio.CancelledError()
            return None

        console_mod.asyncio.sleep = _fast
        try:
            await console_mod._console_hold_watch("resume-token")
        finally:
            console_mod.asyncio.sleep = orig

    for tag in ("none", "eof", "why", "cancel", "boom"):
        asyncio.run(_hold(tag))


def test_console_websocket_branches(client, monkeypatch):
    tc, engine, uid, sid = client
    token = tc.cookies.get("access_token")
    with Session(engine) as session:
        server = session.get(Server, sid)
        ident = ident_svc.ensure_fleet_identity(session, server)
        session.commit()
        session.refresh(ident)
        iid = int(ident.id)

    holder = {
        "idle": 10**6,
        "max": 10**6,
        "revalidate": 10**6,
        "demo": False,
        "audit": "commands",
        "audit_req": False,
        "bind_dev": True,
        "session_ok": (True, ""),
        "bind_ok": (True, ""),
        "payload": {"role": "fleet", "n": 0},
        "consume_err": "",
        "claim_err": False,
        "held": None,
        "start": "rec",
        "flush_err": True,
        "kill_err": False,
        "audit_close_err": True,
        "drop_user": False,
        "user_calls": 0,
        "mux": False,
        "open_raises": False,
        "note": "pty",
        "chan": None,
        "client": None,
    }

    class _Rec:
        transcript_id = None

        def feed_stdout(self, data):
            raise RuntimeError("stdout")

        def feed_stdin(self, data):
            raise RuntimeError("stdin")

    class _Chan:
        def __init__(self):
            self.out = []
            self.err = []
            self.exit_after = False
            self.exit_raises = False
            self.fail_alternate = False
            self._odd = False
            self.closed = False
            self.close_raises = False
            self.boomed = False
            self.boom = False
            self.sent = []

        def recv_ready(self):
            if self.boom and not self.boomed:
                self.boomed = True
                raise RuntimeError("recv")
            return bool(self.out)

        def recv(self, _n):
            return self.out.pop(0) if self.out else b""

        def recv_stderr_ready(self):
            return bool(self.err) and not self.out

        def recv_stderr(self, _n):
            return self.err.pop(0) if self.err else b""

        def exit_status_ready(self):
            if self.exit_raises:
                raise RuntimeError("exit")
            if not self.exit_after:
                return False
            return not self.out and not self.err

        def send(self, data):
            if self.fail_alternate:
                self._odd = not self._odd
                if self._odd:
                    raise RuntimeError("send")
            self.sent.append(data)

        def resize_pty(self, width=80, height=24):
            return None

        def close(self):
            if self.close_raises:
                raise RuntimeError("channel")
            self.closed = True

    class _Client:
        def __init__(self, note="pty"):
            self._ph_mux_note = note
            self._ph_mux_name = "ph-u1-s1-n0-f"
            self._ph_mux_backend = "tmux"
            self.close_raises = False

        def close(self):
            if self.close_raises:
                raise RuntimeError("client")

    class _Held:
        def __init__(self, data=b"buf"):
            self.client = _Client()
            self.channel = _Chan()
            self.ticket_payload = {"role": "fleet"}
            self.started_mono = 0
            self.last_activity_mono = 0
            self.resume_id = "resume-token"
            self.recorder = _Rec()
            self._data = data

        def take_out(self):
            return self._data

    class _BoomHeaders(dict):
        def get(self, key, default=None):
            return {"host": "testserver", "origin": "http://testserver"}.get(key, default)

        def keys(self):
            raise RuntimeError("keys")

        def __iter__(self):
            raise RuntimeError("iter")

    class _BoomClient:
        @property
        def host(self):
            raise RuntimeError("host")

    class _BadBytes:
        def decode(self, *a, **k):
            raise RuntimeError("decode")

        def __bool__(self):
            return True

    class _WS:
        def __init__(self, messages, headers=None, cookies=None, client_obj=None, fail_subs=None, fail_bytes=False, close_raises=False, pause_after=0):
            self.messages = list(messages)
            self.headers = {"host": "testserver", "origin": "http://testserver"} if headers is None else headers
            self.cookies = {"access_token": token} if cookies is None else cookies
            self.client = SimpleNamespace(host="192.0.2.10") if client_obj is None else client_obj
            self.fail_subs = list(fail_subs or [])
            self.fail_bytes = fail_bytes
            self.close_raises = close_raises
            self.pause_after = pause_after
            self._n = 0
            self.sent = []

        async def accept(self):
            return None

        async def close(self, code=1000):
            if self.close_raises:
                raise RuntimeError("ws close")

        async def send_text(self, text):
            for sub in list(self.fail_subs):
                if sub in str(text):
                    self.fail_subs.remove(sub)
                    raise RuntimeError("text")
            self.sent.append(text)

        async def send_bytes(self, data):
            if self.fail_bytes:
                self.fail_bytes = False
                raise RuntimeError("bytes")
            self.sent.append(data)

        async def receive(self):
            self._n += 1
            if self.pause_after and self._n > self.pause_after:
                self.pause_after = 0
                await asyncio.sleep(0.05)
            if not self.messages:
                raise WebSocketDisconnect()
            msg = self.messages.pop(0)
            if msg == "TIMEOUT":
                raise asyncio.TimeoutError()
            if msg == "DISC":
                raise WebSocketDisconnect()
            return msg

    def _frame(obj):
        return {"type": "websocket.receive", "text": json.dumps(obj) if not isinstance(obj, str) else obj}

    auth = _frame({"type": "auth", "ticket": "t"})
    resume = _frame({"type": "resume", "resume": "r"})

    monkeypatch.setattr(cons, "console_enabled", lambda: True)
    monkeypatch.setattr(cons, "idle_sec", lambda: holder["idle"])
    monkeypatch.setattr(cons, "max_session_sec", lambda: holder["max"])
    monkeypatch.setattr(cons, "revalidate_sec", lambda: holder["revalidate"])
    monkeypatch.setattr(cons, "is_demo_console", lambda: holder["demo"])
    monkeypatch.setattr(cons, "audit_mode", lambda: holder["audit"])
    monkeypatch.setattr(cons, "audit_required", lambda: holder["audit_req"])
    monkeypatch.setattr(cons, "bind_device_enabled", lambda: holder["bind_dev"])
    monkeypatch.setattr(cons, "mux_allowed_for_server", lambda server: holder["mux"])
    monkeypatch.setattr(cons, "mux_session_name", lambda **k: "ph-u1-s1-n0-f")
    monkeypatch.setattr(cons, "session_still_valid", lambda *a, **k: holder["session_ok"])
    monkeypatch.setattr(cons, "binding_still_valid", lambda *a, **k: holder["bind_ok"])
    monkeypatch.setattr(cons, "try_acquire_slot", lambda *a, **k: None)
    monkeypatch.setattr(cons, "release_slot", lambda *a, **k: None)
    monkeypatch.setattr(cons, "park_console", lambda held: held.resume_id)
    real_create = console_mod.asyncio.create_task

    def _create_task(coro, **kwargs):
        if holder.get("bad_task"):
            coro.close()

            async def _boom():
                raise RuntimeError("pump")

            return real_create(_boom())
        return real_create(coro, **kwargs)

    monkeypatch.setattr(console_mod.asyncio, "create_task", _create_task)
    monkeypatch.setattr(cons, "kill_mux_session", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kill")) if holder["kill_err"] else None)

    def _consume(ticket, **k):
        if holder["consume_err"] == "denied":
            raise cons.ConsoleDenied("no")
        if holder["consume_err"] == "disabled":
            raise cons.ConsoleDisabled("off")
        return dict(holder["payload"])

    def _claim(*a, **k):
        if holder["claim_err"]:
            raise cons.ConsoleDenied("expired")
        return holder["held"]

    def _open(*a, **k):
        if holder["open_raises"]:
            raise RuntimeError("open failed")
        return holder["client"], holder["chan"]

    def _start(*a, **k):
        if holder["start"] == "raise":
            raise RuntimeError("audit start")
        if holder["start"] == "none":
            return None
        return _Rec()

    def _flush(*a, **k):
        if holder["flush_err"]:
            raise RuntimeError("flush")
        return None

    real_audit = console_mod._audit
    real_user = console_mod._user_from_cookie

    def _audit(*a, **k):
        if holder["audit_close_err"] and k.get("action") == "ssh_console_close":
            raise RuntimeError("close audit")
        return real_audit(*a, **k)

    def _user_from(ws, session):
        if holder["drop_user"]:
            holder["user_calls"] += 1
            if holder["user_calls"] > 1:
                return None
        return real_user(ws, session)

    monkeypatch.setattr(cons, "consume_ticket", _consume)
    monkeypatch.setattr(cons, "claim_resume", _claim)
    monkeypatch.setattr(cons, "open_session_channel", _open)
    monkeypatch.setattr("app.services.console_audit.start_session", _start)
    monkeypatch.setattr("app.services.console_audit.flush_recorder", _flush)
    monkeypatch.setattr(console_mod, "_audit", _audit)
    monkeypatch.setattr(console_mod, "_user_from_cookie", _user_from)

    def _reset(**opts):
        holder.update(
            {
                "idle": 10**6,
                "max": 10**6,
                "revalidate": 10**6,
                "demo": False,
                "audit": "commands",
                "audit_req": False,
                "bind_dev": True,
                "session_ok": (True, ""),
                "bind_ok": (True, ""),
                "payload": {"role": "fleet", "n": 0},
                "consume_err": "",
                "claim_err": False,
                "held": None,
                "start": "rec",
                "flush_err": True,
                "kill_err": False,
                "audit_close_err": True,
                "drop_user": False,
                "user_calls": 0,
                "mux": False,
                "open_raises": False,
                "note": "pty",
                "bad_task": False,
            }
        )
        holder.update(opts)
        chan = _Chan()
        if opts.get("out"):
            chan.out = list(opts["out"])
        if opts.get("err"):
            chan.err = list(opts["err"])
        chan.exit_after = bool(opts.get("exit_after"))
        chan.exit_raises = bool(opts.get("exit_raises"))
        chan.fail_alternate = bool(opts.get("fail_alternate"))
        chan.boom = bool(opts.get("boom"))
        chan.close_raises = bool(opts.get("close_raises"))
        client_obj = _Client(holder["note"])
        client_obj.close_raises = bool(opts.get("close_raises"))
        holder["chan"] = chan
        holder["client"] = client_obj
        cons.reset_runtime_state_for_tests()

    async def _run(name, messages, ws_kw=None, **opts):
        _reset(**opts)
        ws = _WS(messages, **(ws_kw or {}))
        try:
            if holder.get("bad_task"):
                await console_mod.console_websocket(ws, opts.get("server_id", sid))
            else:
                await asyncio.wait_for(
                    console_mod.console_websocket(ws, opts.get("server_id", sid)),
                    timeout=3,
                )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            raise AssertionError(name) from exc
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _all():
        await _run("origin", [], ws_kw={"headers": {"host": "testserver"}})
        await _run("timeout", ["TIMEOUT"])
        await _run("disc-frame", [{"type": "websocket.disconnect"}])
        await _run("bad-bytes", [{"type": "websocket.receive", "bytes": _BadBytes()}])
        await _run("no-auth", [{"type": "websocket.receive", "text": "nope"}])
        await _run("no-user", [auth], ws_kw={"cookies": {}})
        await _run("no-server", [auth], server_id=99999)
        await _run("consume-denied", [auth], consume_err="denied")
        await _run("consume-disabled", [auth], consume_err="disabled")
        await _run(
            "priv-missing",
            [auth],
            payload={"role": "privileged", "iid": 99999, "n": 0},
        )
        await _run(
            "ident-note",
            [auth, "DISC"],
            payload={"role": "fleet", "iid": iid, "n": 1, "why": "maintenance"},
            pause_after=0,
        )
        await _run("audit-raise", [auth], start="raise", audit_req=True)
        await _run("audit-off", [auth], audit="off", audit_req=True)
        await _run("audit-none", [auth], start="none", audit_req=True)
        await _run("demo", [auth, "DISC"], demo=True, pause_after=1)
        await _run("mux-none", [auth, "DISC"], mux=True, note="no_binary", pause_after=1)
        await _run("mux-fail", [auth, "DISC"], mux=True, note="mux_failed", pause_after=1)
        await _run("plain", [auth, "DISC"], note="pty", pause_after=1)
        await _run(
            "output",
            [auth, "DISC"],
            out=[b"hello"],
            err=[b"e"],
            exit_after=True,
            pause_after=1,
            ws_kw={"fail_bytes": True, "pause_after": 1},
        )
        await _run("empty", [auth, "DISC"], out=[b""], pause_after=1, ws_kw={"pause_after": 1})
        await _run("boom", [auth, "DISC"], boom=True, pause_after=1, ws_kw={"pause_after": 1})
        await _run("idle", [auth, "DISC"], idle=-1, pause_after=1, ws_kw={"pause_after": 1})
        await _run("max", [auth, "DISC"], idle=10**6, max=-1, pause_after=1, ws_kw={"pause_after": 1})
        await _run(
            "revalidate-dead",
            [auth, "TIMEOUT", "DISC"],
            revalidate=0,
            session_ok=(False, "revoked"),
            pause_after=1,
            ws_kw={"pause_after": 1},
        )
        await _run(
            "device",
            [auth, "TIMEOUT", "DISC"],
            revalidate=0,
            bind_ok=(False, "device_changed"),
            ws_kw={"pause_after": 1},
        )
        await _run(
            "ip-kill",
            [auth, "TIMEOUT", "DISC"],
            revalidate=0,
            bind_ok=(False, "ip_changed"),
            bind_dev=False,
            ws_kw={"pause_after": 1},
        )
        await _run(
            "ip-ok",
            [auth, "TIMEOUT", "DISC"],
            revalidate=0,
            bind_ok=(False, "ip_changed"),
            bind_dev=True,
            ws_kw={
                "pause_after": 1,
                "cookies": {"access_token": token, cons.CONSOLE_DEVICE_COOKIE: "device-token-0001"},
            },
        )
        await _run(
            "user-drop",
            [auth, "TIMEOUT", "DISC"],
            revalidate=0,
            drop_user=True,
            ws_kw={"pause_after": 1},
        )
        await _run(
            "inputs",
            [
                auth,
                {"type": "websocket.receive", "bytes": b"xyz"},
                _frame({"type": "ping"}),
                _frame({"type": "stdin", "hex": "6a"}),
                _frame({"type": "stdin", "hex": "zz", "data": "ab"}),
                _frame({"type": "wat"}),
                {"type": "websocket.receive", "text": '{"type":'},
                {"type": "websocket.receive", "text": "echo hi"},
                _frame({"type": "bye"}),
            ],
            fail_alternate=True,
            kill_err=True,
            ws_kw={"fail_subs": ["pong"]},
        )
        await _run(
            "open-fail",
            [auth],
            open_raises=True,
            ws_kw={
                "headers": _BoomHeaders(),
                "client_obj": _BoomClient(),
                "fail_subs": ["Connection failed"],
            },
        )
        await _run(
            "park",
            [auth, "DISC"],
            boom=True,
            exit_raises=True,
            ws_kw={"pause_after": 1},
        )
        await _run(
            "park-clear",
            [auth, "DISC"],
            boom=True,
            exit_after=True,
            close_raises=True,
            ws_kw={"pause_after": 1, "close_raises": True, "fail_subs": ["ended"]},
        )
        await _run(
            "resume-fail",
            [resume],
            claim_err=True,
            ws_kw={"fail_subs": ["resume_failed"]},
        )
        held = _Held(b"buf")
        await _run(
            "resume-ok",
            [resume, _frame({"type": "bye"})],
            held=held,
            kill_err=True,
        )
        await _run(
            "resume-bytes",
            [resume, "DISC"],
            held=_Held(b"buf"),
            ws_kw={"fail_bytes": True, "fail_subs": ["buf"]},
        )
        await _run(
            "disc-msg",
            [auth, {"type": "websocket.disconnect"}],
        )
        await _run("bad-pump", [auth, "DISC"], bad_task=True, ws_kw={"pause_after": 1})

    real_mono = console_mod.time.monotonic
    state = {"n": 0, "base": None}

    def _jump():
        state["n"] += 1
        now = real_mono()
        if state["base"] is None:
            state["base"] = now
        extra = 1.0 if state["n"] > 12 else 0.0
        return now + extra

    async def _with_jump():
        await _all()
        console_mod.time.monotonic = _jump
        try:
            await _run(
                "slow-poll",
                [auth, "DISC"],
                idle=10**6,
                max=10**6,
                ws_kw={"pause_after": 1},
            )
        finally:
            console_mod.time.monotonic = real_mono

    asyncio.run(_with_jump())


def test_wizard_routes(client, monkeypatch):
    tc, engine, uid, sid = client
    with Session(engine) as session:
        user = session.get(User, uid)

        async def _identity():
            with pytest.raises(HTTPException):
                await wiz.wizard_identity_post("  ", "  ", "user", 22, session, user)
            monkeypatch.setattr(
                patch_mod,
                "sync_server_schedules",
                lambda server: (_ for _ in ()).throw(RuntimeError("sync")),
            )
            first = await wiz.wizard_identity_post(
                "host-b", "host-b.example.com", "user", 0, session, user
            )
            second = await wiz.wizard_identity_post(
                "host-c", "host-c.example.com", "user", "nope", session, user
            )
            assert first.status_code == 303 and second.status_code == 303

        asyncio.run(_identity())
        with pytest.raises(HTTPException) as missing_id:
            wiz._require_server(session, None)
        assert missing_id.value.status_code == 400
        with pytest.raises(HTTPException) as missing_row:
            wiz._require_server(session, 99999)
        assert missing_row.value.status_code == 404

    monkeypatch.setattr(patch_mod, "sync_server_schedules", lambda server: None)
    assert tc.get("/servers/new?step=bogus").status_code == 303
    assert tc.get("/servers/new?step=connect").status_code == 303
    assert tc.get("/servers/new?step=connect&server_id=99999").status_code == 303
    resume = tc.get(f"/servers/new?step=identity&server_id={sid}")
    assert resume.status_code == 303
    page = tc.get(
        f"/servers/new?step=connect&server_id={sid}&hostname=host-a.example.com&name=host-a"
    )
    assert page.status_code == 200, page.text[:200]
    monkeypatch.setattr(
        ssh_onboarding,
        "build_key_install_script",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("script")),
    )
    broken = tc.get(f"/servers/new?step=trust&server_id={sid}")
    assert broken.status_code == 200
    monkeypatch.setattr(wiz, "reject_if_demo", lambda action="": "demo lock")
    assert tc.get("/servers/new/advanced").status_code == 303
    monkeypatch.setattr(wiz, "reject_if_demo", lambda action="": None)
    assert tc.get("/servers/new/advanced").status_code == 200

    made = tc.post(
        "/servers/new/identity",
        data={"name": "host-d", "hostname": "host-d.example.com", "ssh_username": "user", "ssh_port": "22"},
    )
    assert made.status_code == 303
    with Session(engine) as session:
        row = session.exec(
            __import__("sqlmodel").select(Server).where(Server.name == "host-d")
        ).first()
        wid = int(row.id)
    assert tc.post(
        "/servers/new/identity",
        data={"name": " ", "hostname": " ", "ssh_username": "user", "ssh_port": "22"},
    ).status_code == 400

    assert tc.post(
        "/servers/new/trust",
        data={"server_id": str(wid), "key_mode": "generate", "ssh_password": "test-secret"},
    ).status_code == 303
    assert tc.post(
        "/servers/new/trust",
        data={"server_id": str(wid), "key_mode": "password", "ssh_password": ""},
    ).status_code == 400
    assert tc.post(
        "/servers/new/trust",
        data={"server_id": str(wid), "key_mode": "password", "ssh_password": "test-secret"},
    ).status_code == 303
    assert tc.post(
        "/servers/new/trust",
        data={"server_id": str(wid), "key_mode": "upload", "private_key": ""},
    ).status_code == 400
    monkeypatch.setattr(
        ssh_onboarding,
        "public_key_from_private",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad key")),
    )
    assert tc.post(
        "/servers/new/trust",
        data={"server_id": str(wid), "key_mode": "upload", "private_key": "not-a-key", "ssh_password": "test-secret"},
    ).status_code == 303
    monkeypatch.setattr(ssh_onboarding, "public_key_from_private", lambda *a, **k: PUB)
    assert tc.post(
        "/servers/new/trust",
        data={"server_id": str(wid), "key_mode": "upload", "private_key": "not-a-key"},
    ).status_code == 303

    assert tc.post(
        "/servers/new/continue",
        data={"server_id": str(wid), "step": "network"},
    ).status_code == 303
    assert tc.post(
        "/servers/new/continue",
        data={"server_id": str(wid), "step": "done"},
    ).status_code == 303

    monkeypatch.setattr(
        host_deps_svc,
        "check_and_persist",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("deps")),
    )
    assert tc.post("/servers/new/connect/test", data={"server_id": str(wid)}).status_code == 303
    monkeypatch.setattr(ssh_onboarding, "test_connection_detail", lambda *a, **k: _bad("no route"))
    assert tc.post("/servers/new/connect/test", data={"server_id": str(wid)}).status_code == 303
    monkeypatch.setattr(ssh_onboarding, "test_connection_detail", lambda *a, **k: _ok("connected"))

    assert tc.post(
        "/servers/new/connect/deploy-key",
        data={"server_id": str(wid), "ssh_password": "test-secret", "clear_password_after": "1"},
    ).status_code == 303
    monkeypatch.setattr(ssh_onboarding, "deploy_public_key", lambda *a, **k: _bad("denied"))
    assert tc.post(
        "/servers/new/connect/deploy-key",
        data={"server_id": str(wid)},
    ).status_code == 303
    monkeypatch.setattr(
        ssh_onboarding,
        "deploy_public_key",
        lambda *a, **k: _ok("deployed", public_key=PUB, installed=True, already_auth=False, already_present=False),
    )

    with Session(engine) as session:
        server = session.get(Server, wid)
        server.ssh_password_encrypted = ""
        session.add(server)
        session.commit()
    assert tc.post("/servers/new/connect/clear-password", data={"server_id": str(wid)}).status_code == 303
    with Session(engine) as session:
        server = session.get(Server, wid)
        server.ssh_password_encrypted = "enc:pw"
        server.os_type = "haos"
        server.backup_enabled = False
        server.container_patch_enabled = False
        session.add(server)
        session.commit()
    assert tc.post("/servers/new/connect/clear-password", data={"server_id": str(wid)}).status_code == 303
    monkeypatch.setattr(
        patch_mod,
        "sync_server_schedules",
        lambda server: (_ for _ in ()).throw(RuntimeError("sync")),
    )
    featured = tc.post(
        "/servers/new/features",
        data={
            "server_id": str(wid),
            "backup_enabled": "1",
            "os_patch_enabled": "on",
            "container_patch_enabled": "yes",
            "console_mux_enabled": "1",
        },
    )
    assert featured.status_code == 303
    assert tc.get("/servers/new/exit?server_id=99999").status_code == 303
    assert tc.get(f"/servers/new/exit?server_id={wid}").status_code == 303


def test_ssh_routes(client, monkeypatch):
    tc, engine, uid, sid = client
    assert tc.get("/servers/99999/ssh/cleanup-script").status_code == 404
    with Session(engine) as session:
        server = session.get(Server, sid)
        server.ssh_username = "user name"
        server.hostname = "host a"
        server.docker_base_dir = "/home/user/docker"
        session.add(server)
        session.commit()
    script = tc.get(f"/servers/{sid}/ssh/cleanup-script")
    assert script.status_code == 200 and b"#!/bin/" in script.content or script.status_code == 200
    with Session(engine) as session:
        server = session.get(Server, sid)
        server.ssh_username = "user"
        server.hostname = "host-a.example.com"
        session.add(server)
        session.commit()

    assert tc.post("/servers/99999/audit/ssh-key-viewed").status_code == 404
    assert tc.post(f"/servers/{sid}/audit/ssh-key-viewed").status_code == 200
    assert tc.post("/servers/99999/ssh/generate-key").status_code == 404
    exists = tc.post(f"/servers/{sid}/ssh/generate-key")
    assert exists.status_code == 303
    with Session(engine) as session:
        bare = Server(
            name="host-e",
            hostname="host-e.example.com",
            ip_address="192.0.2.30",
            ssh_username="user",
            os_type="debian",
            docker_base_dir="~/docker",
        )
        session.add(bare)
        session.commit()
        session.refresh(bare)
        eid = int(bare.id)
    assert tc.post(f"/servers/{eid}/ssh/generate-key").status_code == 303

    assert tc.post("/servers/99999/ssh/test").status_code == 404
    monkeypatch.setattr(
        host_deps_svc,
        "check_and_persist",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("deps")),
    )
    assert tc.post(f"/servers/{sid}/ssh/test").status_code == 303
    monkeypatch.setattr(ssh_onboarding, "test_connection_detail", lambda *a, **k: _bad("no route"))
    assert tc.post(f"/servers/{sid}/ssh/test").status_code == 303
    monkeypatch.setattr(ssh_onboarding, "test_connection_detail", lambda *a, **k: _ok("connected"))
    monkeypatch.setattr(
        host_deps_svc,
        "check_and_persist",
        lambda *a, **k: {"overall": "bad", "checks": []},
    )

    assert tc.post("/servers/99999/ssh/reset-host-key", data={"confirm_name": "host-a"}).status_code == 404
    assert tc.post(f"/servers/{sid}/ssh/reset-host-key", data={"confirm_name": "nope"}).status_code == 303
    with Session(engine) as session:
        server = session.get(Server, sid)
        server.ssh_hostkey_fp = "SHA256:abc"
        session.add(server)
        session.commit()
    assert tc.post(f"/servers/{sid}/ssh/reset-host-key", data={"confirm_name": "host-a"}).status_code == 303

    haos = None
    with Session(engine) as session:
        row = Server(
            name="host-h",
            hostname="host-h.example.com",
            ip_address="192.0.2.40",
            ssh_username="user",
            os_type="haos",
            ssh_private_key_encrypted="enc:key",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        haos = int(row.id)
    assert tc.post(f"/servers/{haos}/ssh/mux-sessions").status_code == 303
    assert tc.post("/servers/99999/ssh/mux-sessions").status_code == 404

    class _Mux:
        def close(self):
            raise RuntimeError("close")

    monkeypatch.setattr(ssh_service, "get_ssh_client", lambda *a, **k: _Mux())
    monkeypatch.setattr(
        ssh_mod.ssh_console,
        "list_host_mux_sessions",
        lambda client, server_id: (_ for _ in ()).throw(RuntimeError("list")),
    )
    assert tc.post(f"/servers/{sid}/ssh/mux-sessions").status_code == 303
    name = f"ph-u1-s{sid}-n0-f"
    monkeypatch.setattr(
        ssh_mod.ssh_console,
        "list_host_mux_sessions",
        lambda client, server_id: [
            {"server_id": int(server_id), "backend": "tmux", "name": f"ph-u1-s{int(server_id)}-n0-f"},
            {"server_id": 0, "backend": "tmux", "name": "other"},
        ],
    )
    assert tc.post(f"/servers/{sid}/ssh/mux-sessions").status_code == 303
    assert tc.post(f"/servers/{haos}/ssh/mux-sessions/kill", data={"session_name": name, "backend": "tmux"}).status_code == 303
    assert tc.post("/servers/99999/ssh/mux-sessions/kill", data={"session_name": name, "backend": "tmux"}).status_code == 404
    assert tc.post(
        f"/servers/{sid}/ssh/mux-sessions/kill",
        data={"session_name": "nope", "backend": "tmux"},
    ).status_code == 303
    monkeypatch.setattr(
        ssh_mod.ssh_console,
        "kill_mux_session",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kill")),
    )
    assert tc.post(
        f"/servers/{sid}/ssh/mux-sessions/kill",
        data={"session_name": name, "backend": "tmux"},
    ).status_code == 303
    monkeypatch.setattr(ssh_mod.ssh_console, "kill_mux_session", lambda *a, **k: None)
    assert tc.post(
        f"/servers/{sid}/ssh/mux-sessions/kill",
        data={"session_name": name, "backend": "screen"},
    ).status_code == 303

    assert tc.post("/servers/99999/host-deps/check").status_code == 404
    monkeypatch.setattr(
        host_deps_svc,
        "check_and_persist",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("deps")),
    )
    assert tc.post(f"/servers/{sid}/host-deps/check").status_code == 303
    monkeypatch.setattr(
        host_deps_svc,
        "check_and_persist",
        lambda *a, **k: {"overall": "warn", "checks": [{"id": "docker", "status": "warn", "required": False}]},
    )
    assert tc.post(f"/servers/{sid}/host-deps/check").status_code == 303

    assert tc.post("/servers/99999/host-deps/bootstrap", data={}).status_code == 404
    bad_user = tc.post(
        f"/servers/{sid}/host-deps/bootstrap",
        data={"bootstrap_username": "bad user"},
        headers={"X-PiHerder-Async": "1"},
    )
    assert bad_user.status_code == 400
    monkeypatch.setattr(
        host_deps_svc,
        "parse_host_deps",
        lambda server: {"bootstrap": {"install_docker_ce": True}},
    )
    need_async = tc.post(
        f"/servers/{sid}/host-deps/bootstrap",
        data={"bootstrap_username": "svc"},
        headers={"X-PiHerder-Async": "1"},
    )
    assert need_async.status_code == 400 and need_async.json().get("need_confirm") is True
    need = tc.post(f"/servers/{sid}/host-deps/bootstrap", data={"bootstrap_username": "svc"})
    assert need.status_code == 303
    monkeypatch.setattr(host_deps_svc, "parse_host_deps", lambda server: {})

    def _active(*a, **k):
        raise job_service.JobAlreadyActive(SimpleNamespace(id=9, status="running", details="{"))

    monkeypatch.setattr(job_service, "create_job_and_run", _active)
    reused = tc.post(
        f"/servers/{sid}/host-deps/bootstrap",
        data={"bootstrap_username": "svc", "confirm_docker_bootstrap": "1", "sudo_password": "test-sudo"},
        headers={"X-PiHerder-Async": "1"},
    )
    assert reused.status_code == 409

    def _ns(*a, **k):
        return SimpleNamespace(id=4, status="failed", details="not-json")

    monkeypatch.setattr(job_service, "create_job_and_run", _ns)
    failed = tc.post(
        f"/servers/{sid}/host-deps/bootstrap",
        data={"sudo_password": "test-sudo"},
    )
    assert failed.status_code == 303

    def _pending(*a, **k):
        return SimpleNamespace(id=5, status="pending", details=json.dumps({"summary": "queued"}))

    monkeypatch.setattr(job_service, "create_job_and_run", _pending)
    queued = tc.post(f"/servers/{sid}/host-deps/bootstrap", data={})
    assert queued.status_code == 303

    def _success(*a, **k):
        return SimpleNamespace(id=6, status="success", details=json.dumps({"summary": "done"}))

    monkeypatch.setattr(job_service, "create_job_and_run", _success)
    done = tc.post(f"/servers/{sid}/host-deps/bootstrap", data={})
    assert done.status_code == 303

    assert tc.post("/servers/99999/ssh/deploy-key", data={}).status_code == 404
    monkeypatch.setattr(
        host_deps_svc,
        "check_and_persist",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("deps")),
    )
    deployed = tc.post(
        f"/servers/{sid}/ssh/deploy-key",
        data={"ssh_password": "test-secret", "store_password": "1", "clear_password_after": "1"},
    )
    assert deployed.status_code == 303
    monkeypatch.setattr(ssh_onboarding, "deploy_public_key", lambda *a, **k: _bad("denied"))
    assert tc.post(f"/servers/{sid}/ssh/deploy-key", data={}).status_code == 303
    monkeypatch.setattr(
        ssh_onboarding,
        "deploy_public_key",
        lambda *a, **k: _ok("deployed", public_key=PUB + " b", installed=True, already_auth=False, already_present=False),
    )

    assert tc.post("/servers/99999/ssh/rotate-key", data={"confirm": "rotate"}).status_code == 404
    assert tc.post(f"/servers/{sid}/ssh/rotate-key", data={"confirm": "no"}).status_code == 303
    monkeypatch.setattr(ssh_onboarding, "rotate_keypair", lambda *a, **k: _bad("denied"))
    assert tc.post(
        f"/servers/{sid}/ssh/rotate-key",
        data={"confirm": "rotate", "ssh_password": "test-secret"},
    ).status_code == 303
    monkeypatch.setattr(
        ssh_onboarding,
        "rotate_keypair",
        lambda *a, **k: _ok(
            "rotated",
            new_public_key=PUB,
            new_private_key="FAKE-PRIVATE-KEY",
            removed_old=True,
            installed=True,
        ),
    )
    assert tc.post(f"/servers/{sid}/ssh/rotate-key", data={"confirm": "rotate"}).status_code == 303

    assert tc.post("/servers/99999/ssh/set-username", data={"ssh_username": "svc"}).status_code == 404
    assert tc.post(f"/servers/{sid}/ssh/set-username", data={"ssh_username": " "}).status_code == 303
    assert tc.post(f"/servers/{sid}/ssh/set-username", data={"ssh_username": "user"}).status_code == 303
    with Session(engine) as session:
        server = session.get(Server, sid)
        server.ssh_password_encrypted = "enc:pw"
        server.docker_base_dir = "~/docker"
        session.add(server)
        session.commit()
    changed = tc.post(f"/servers/{sid}/ssh/set-username", data={"ssh_username": "svc"})
    assert changed.status_code == 303

    assert tc.post("/servers/99999/ssh/provision-user", data={}).status_code == 404
    assert tc.post(f"/servers/{sid}/ssh/provision-user", data={"new_username": "svc"}).status_code == 303
    monkeypatch.setattr(
        ssh_onboarding,
        "provision_least_priv_user",
        lambda *a, **k: _bad("denied", os={"name": "debian"}),
    )
    assert tc.post(
        f"/servers/{sid}/ssh/provision-user",
        data={"new_username": "svc", "run_on_host": "1", "ssh_password": "test-secret"},
    ).status_code == 303
    monkeypatch.setattr(
        ssh_onboarding,
        "provision_least_priv_user",
        lambda *a, **k: _ok("provisioned", new_username="svc", os={"name": "debian"}),
    )
    with Session(engine) as session:
        server = session.get(Server, sid)
        server.ssh_password_encrypted = "enc:pw"
        session.add(server)
        session.commit()
    monkeypatch.setattr(
        host_deps_svc,
        "check_and_persist",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("deps")),
    )
    assert tc.post(
        f"/servers/{sid}/ssh/provision-user",
        data={
            "new_username": "svc",
            "run_on_host": "1",
            "include_backup": "1",
            "include_docker": "1",
            "include_os_patch": "1",
        },
    ).status_code == 303

    assert tc.post("/servers/99999/ssh/identities/privileged", data={}).status_code == 404
    monkeypatch.setattr(
        ssh_onboarding,
        "public_key_from_private",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad key")),
    )
    assert tc.post(
        f"/servers/{sid}/ssh/identities/privileged",
        data={"key_mode": "upload", "private_key": "not-a-key", "ssh_username": "admin", "label": "Privileged"},
    ).status_code == 303
    monkeypatch.setattr(ssh_onboarding, "public_key_from_private", lambda *a, **k: PUB)
    added = tc.post(
        f"/servers/{sid}/ssh/identities/privileged",
        data={"key_mode": "upload", "private_key": "not-a-key", "ssh_username": "admin", "label": "Privileged"},
    )
    assert added.status_code == 303
    again = tc.post(
        f"/servers/{sid}/ssh/identities/privileged",
        data={"key_mode": "generate", "ssh_username": "admin"},
    )
    assert again.status_code == 303
    with Session(engine) as session:
        priv = session.exec(
            __import__("sqlmodel").select(ServerSshIdentity).where(
                ServerSshIdentity.server_id == sid,
                ServerSshIdentity.role == "privileged",
            )
        ).first()
        fleet = ident_svc.ensure_fleet_identity(session, session.get(Server, sid))
        session.commit()
        pid, fid = int(priv.id), int(fleet.id)
        priv.public_key = ""
        session.add(priv)
        session.commit()

    assert tc.post(f"/servers/99999/ssh/identities/{pid}/test").status_code == 404
    assert tc.post(f"/servers/{sid}/ssh/identities/99999/test").status_code == 303
    monkeypatch.setattr(
        host_deps_svc,
        "check_and_persist",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("deps")),
    )
    assert tc.post(f"/servers/{sid}/ssh/identities/{fid}/test").status_code == 303
    monkeypatch.setattr(ssh_onboarding, "test_connection_detail", lambda *a, **k: _bad("no route"))
    assert tc.post(f"/servers/{sid}/ssh/identities/{pid}/test").status_code == 303
    monkeypatch.setattr(ssh_onboarding, "test_connection_detail", lambda *a, **k: _ok("connected"))

    assert tc.post(f"/servers/99999/ssh/identities/{pid}/deploy", data={}).status_code == 404
    assert tc.post(f"/servers/{sid}/ssh/identities/99999/deploy", data={}).status_code == 303
    assert tc.post(
        f"/servers/{sid}/ssh/identities/{fid}/deploy",
        data={"ssh_password": "test-secret"},
    ).status_code == 303
    monkeypatch.setattr(ssh_onboarding, "deploy_public_key", lambda *a, **k: _bad("denied"))
    assert tc.post(f"/servers/{sid}/ssh/identities/{pid}/deploy", data={}).status_code == 303
    monkeypatch.setattr(
        ssh_onboarding,
        "deploy_public_key",
        lambda *a, **k: _ok("deployed", public_key=PUB + " c", installed=True),
    )

    assert tc.post(f"/servers/99999/ssh/identities/{pid}/rotate", data={"confirm": "rotate"}).status_code == 404
    assert tc.post(f"/servers/{sid}/ssh/identities/99999/rotate", data={"confirm": "rotate"}).status_code == 303
    assert tc.post(f"/servers/{sid}/ssh/identities/{fid}/rotate", data={"confirm": "no"}).status_code == 303
    monkeypatch.setattr(ssh_onboarding, "rotate_keypair", lambda *a, **k: _bad("denied"))
    assert tc.post(
        f"/servers/{sid}/ssh/identities/{fid}/rotate",
        data={"confirm": "rotate", "ssh_password": "test-secret"},
    ).status_code == 303
    monkeypatch.setattr(
        ssh_onboarding,
        "rotate_keypair",
        lambda *a, **k: _ok(
            "rotated",
            new_public_key=PUB,
            new_private_key="FAKE-PRIVATE-KEY",
            removed_old=False,
            installed=True,
        ),
    )
    assert tc.post(f"/servers/{sid}/ssh/identities/{fid}/rotate", data={"confirm": "rotate"}).status_code == 303

    monkeypatch.setattr(ssh_onboarding, "public_key_from_private", lambda *a, **k: PUB)
    setup = tc.get(f"/servers/{sid}/ssh/identities/{pid}/setup-script")
    assert setup.status_code == 200
    monkeypatch.setattr(
        ssh_onboarding,
        "public_key_from_private",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad key")),
    )
    setup_bad = tc.get(f"/servers/{sid}/ssh/identities/{pid}/setup-script")
    assert setup_bad.status_code == 200
    assert tc.get(f"/servers/99999/ssh/identities/{pid}/setup-script").status_code == 404
    assert tc.get(f"/servers/{sid}/ssh/identities/{fid}/setup-script").status_code == 404

    assert tc.post(f"/servers/99999/ssh/identities/{pid}/remove", data={"confirm_name": "host-a"}).status_code == 404
    assert tc.post(f"/servers/{sid}/ssh/identities/99999/remove", data={"confirm_name": "host-a"}).status_code == 303
    assert tc.post(
        f"/servers/{sid}/ssh/identities/{pid}/remove",
        data={"confirm_name": "nope"},
    ).status_code == 303
    assert tc.post(
        f"/servers/{sid}/ssh/identities/{fid}/remove",
        data={"confirm_name": "host-a"},
    ).status_code == 303
    removed = tc.post(
        f"/servers/{sid}/ssh/identities/{pid}/remove",
        data={"confirm_name": "host-a"},
    )
    assert removed.status_code == 303


def test_patch_routes(client, monkeypatch):
    tc, engine, uid, sid = client
    import app.main as mainmod

    assert tc.get("/servers/99999/os-patch-progress").status_code == 404
    monkeypatch.setattr(
        patch_mod.os_patching,
        "get_os_patch_progress",
        lambda host: {"current": "upgrade", "log_lines": ["apt started"], "done": False, "finished_ok": None},
    )
    assert tc.get(f"/servers/{sid}/os-patch-progress").status_code == 200
    assert tc.get("/servers/99999/os-patch/logs/stream").status_code == 404

    async def _pull():
        with Session(engine) as session:
            user = session.get(User, uid)
            response = await patch_mod.stream_os_patch_logs(sid, session, user)
        agen = response.body_iterator
        chunks = []
        orig = asyncio.sleep

        async def _stop(_delay=0):
            raise asyncio.CancelledError()

        patch_mod.asyncio.sleep = _stop
        try:
            chunks.append(await agen.__anext__())
            try:
                await agen.__anext__()
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
        finally:
            patch_mod.asyncio.sleep = orig
        return chunks

    chunks = asyncio.run(_pull())
    assert chunks and "apt started" in chunks[0]

    monkeypatch.setattr(
        "app.services.diagnostics.run_diagnostics",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("diag")),
    )
    failed = tc.get(f"/servers/{sid}/diagnostics?force=true")
    assert failed.status_code == 200 and "error" in failed.json()
    assert tc.get("/servers/99999/diagnostics").status_code == 404

    assert tc.post("/servers/99999/run/container_patch").status_code == 404
    queued = tc.post(
        f"/servers/{sid}/run/container_patch",
        headers={"X-PiHerder-Async": "1"},
    )
    assert queued.status_code == 200
    with Session(engine) as session:
        active = Job(server_id=sid, job_type="container_patch", status="running", details="{}")
        session.add(active)
        session.commit()
        session.refresh(active)
        active_id = active.id

    def _busy(*a, **k):
        with Session(engine) as session:
            job = session.get(Job, active_id)
        raise job_service.JobAlreadyActive(job)

    monkeypatch.setattr(job_service, "create_job_and_run", _busy)
    reused = tc.post(
        f"/servers/{sid}/run/container_patch",
        headers={"X-PiHerder-Async": "1"},
    )
    assert reused.status_code == 409
    assert tc.post(f"/servers/{sid}/run/container_patch").status_code == 303

    assert tc.post("/servers/99999/run/os_patch").status_code == 404
    monkeypatch.setattr(patch_mod.os_patching, "normalize_os_patch_steps", lambda steps: [])
    os_reused = tc.post(
        f"/servers/{sid}/run/os_patch",
        headers={"X-PiHerder-Async": "1"},
        data={"steps": "update"},
    )
    assert os_reused.status_code == 409
    assert tc.post(f"/servers/{sid}/run/os_patch", data={}).status_code == 303

    assert tc.get("/servers/99999/jobs").status_code == 404
    listed = tc.get(f"/servers/{sid}/jobs?status=running&job_type=container_patch&active_only=true")
    assert listed.status_code == 200

    assert tc.get(f"/servers/{sid}/jobs/99999").status_code == 404
    with Session(engine) as session:
        bad = Job(server_id=sid, job_type="container_patch", status="running", details="not-json", worker_hostname="host-a")
        os_job = Job(
            server_id=sid,
            job_type="os_patch",
            status="running",
            details=json.dumps({"log_lines": ["old"], "summary": "work"}),
            worker_hostname="host-a",
        )
        done = Job(
            server_id=sid,
            job_type="os_patch",
            status="success",
            details=json.dumps({"summary": "done", "done": True}),
            finished_at=datetime.utcnow(),
            worker_hostname="host-a",
        )
        session.add(bad)
        session.add(os_job)
        session.add(done)
        session.commit()
        session.refresh(bad)
        session.refresh(os_job)
        session.refresh(done)
        bad_id, os_id, done_id = bad.id, os_job.id, done.id
    monkeypatch.setattr(
        "app.services.container_patching.get_container_patch_progress",
        lambda host: (_ for _ in ()).throw(RuntimeError("tail")),
    )
    assert tc.get(f"/servers/{sid}/jobs/{bad_id}").status_code == 200
    monkeypatch.setattr(
        patch_mod.os_patching,
        "get_os_patch_progress",
        lambda host: (_ for _ in ()).throw(RuntimeError("tail")),
    )
    assert tc.get(f"/servers/{sid}/jobs/{os_id}").status_code == 200
    assert tc.get(f"/servers/{sid}/jobs/{done_id}").status_code == 200

    with pytest.raises(HTTPException):
        patch_mod.validate_cron("not a cron")
    assert patch_mod.validate_cron("") is None
    assert patch_mod.validate_cron(None) is None
    if patch_mod.pycron is not None:
        original_is_now = patch_mod.pycron.is_now
        monkeypatch.setattr(
            patch_mod.pycron,
            "is_now",
            lambda *a, **k: (_ for _ in ()).throw(ValueError("bad cron")),
        )
        with pytest.raises(HTTPException):
            patch_mod.validate_cron("0 6 * * *")
        monkeypatch.setattr(patch_mod.pycron, "is_now", original_is_now)

    def _boom_sync(*a, **k):
        raise RuntimeError("scheduler")

    monkeypatch.setattr("app.services.scheduler.sync_server_cron_jobs", _boom_sync)
    with Session(engine) as session:
        server = session.get(Server, sid)
        patch_mod._orig_sync_for_tests(server)

    import sys

    saved_main = sys.modules.get("app.main")
    sys.modules["app.main"] = None
    try:
        assert patch_mod.docker_prune_job_registered(sid) is False
    finally:
        sys.modules["app.main"] = saved_main

    old_flag, old_sched = mainmod.HAS_SCHEDULER, mainmod.scheduler
    try:
        mainmod.HAS_SCHEDULER = False
        assert patch_mod.docker_prune_job_registered(sid) is False
        mainmod.HAS_SCHEDULER = True
        mainmod.scheduler = None
        assert patch_mod.docker_prune_job_registered(sid) is False
        mainmod.scheduler = SimpleNamespace(get_job=lambda name: (_ for _ in ()).throw(RuntimeError("job")))
        assert patch_mod.docker_prune_job_registered(0) is False
        assert patch_mod.docker_prune_job_registered(sid) is False
        mainmod.scheduler = SimpleNamespace(get_job=lambda name: object())
        assert patch_mod.docker_prune_job_registered(sid) is True
    finally:
        mainmod.HAS_SCHEDULER = old_flag
        mainmod.scheduler = old_sched

    assert tc.post("/servers/99999/check/os-updates").status_code == 404
    monkeypatch.setattr(job_service, "enqueue_os_update_check", lambda *a, **k: None)
    assert tc.post(f"/servers/{sid}/check/os-updates").status_code == 500
    monkeypatch.setattr(
        job_service,
        "enqueue_os_update_check",
        lambda *a, **k: SimpleNamespace(id=3, status="pending"),
    )
    assert tc.post(
        f"/servers/{sid}/check/os-updates",
        headers={"X-PiHerder-Async": "1"},
    ).status_code == 200
    assert tc.post(f"/servers/{sid}/check/os-updates").status_code == 303

    assert tc.post("/servers/99999/check/container-updates").status_code == 404
    monkeypatch.setattr(job_service, "enqueue_container_update_check", lambda *a, **k: None)
    assert tc.post(f"/servers/{sid}/check/container-updates").status_code == 500
    monkeypatch.setattr(
        job_service,
        "enqueue_container_update_check",
        lambda *a, **k: SimpleNamespace(id=4, status="pending"),
    )
    assert tc.post(
        f"/servers/{sid}/check/container-updates",
        headers={"X-PiHerder-Async": "1"},
    ).status_code == 200
    assert tc.post(f"/servers/{sid}/check/container-updates").status_code == 303

    assert tc.post("/servers/99999/schedule/os-check", data={"os_check_schedule": ""}).status_code == 404
    assert tc.post(
        f"/servers/{sid}/schedule/os-check",
        data={"os_check_enabled": "1", "os_check_schedule": "not a cron"},
    ).status_code == 400
    saved = tc.post(
        f"/servers/{sid}/schedule/os-check",
        data={"os_check_enabled": "1", "os_check_schedule": "0 6 * * *"},
    )
    assert saved.status_code == 303
    assert tc.post(
        f"/servers/{sid}/schedule/os-check",
        data={"os_check_schedule": ""},
    ).status_code == 303
    assert tc.post("/servers/99999/schedule/container-check", data={}).status_code == 404
    assert tc.post(
        f"/servers/{sid}/schedule/container-check",
        data={
            "container_check_enabled": "on",
            "container_check_schedule": "0 7 * * *",
            "container_check_catchup": "1",
        },
    ).status_code == 303

    assert tc.post("/servers/99999/schedule/os-apply", data={}).status_code == 404
    assert tc.post(
        f"/servers/{sid}/schedule/os-apply",
        data={"os_apply_enabled": "1", "os_apply_schedule": "0 8 * * *"},
    ).status_code == 400
    with Session(engine) as session:
        server = session.get(Server, sid)
        server.os_patch_enabled = True
        server.container_patch_enabled = False
        session.add(server)
        session.commit()
    assert tc.post(
        f"/servers/{sid}/schedule/os-apply",
        data={
            "os_apply_enabled": "1",
            "os_apply_schedule": "0 8 * * *",
            "os_apply_only_if_updates": "1",
            "os_apply_use_full_upgrade": "1",
        },
    ).status_code == 303
    assert tc.post("/servers/99999/schedule/container-apply", data={}).status_code == 404
    assert tc.post(
        f"/servers/{sid}/schedule/container-apply",
        data={"container_apply_enabled": "1", "container_apply_schedule": "0 9 * * *"},
    ).status_code == 400
    with Session(engine) as session:
        server = session.get(Server, sid)
        server.container_patch_enabled = True
        session.add(server)
        session.commit()
    assert tc.post(
        f"/servers/{sid}/schedule/container-apply",
        data={
            "container_apply_enabled": "1",
            "container_apply_schedule": "0 9 * * *",
            "container_apply_only_if_updates": "1",
        },
    ).status_code == 303

    assert tc.post("/servers/99999/schedule/docker-prune", data={}).status_code == 404
    with Session(engine) as session:
        server = session.get(Server, sid)
        server.container_patch_enabled = False
        session.add(server)
        session.commit()
    assert tc.post(
        f"/servers/{sid}/schedule/docker-prune",
        data={"docker_prune_schedule": "0 1 * * *"},
    ).status_code == 400
    with Session(engine) as session:
        server = session.get(Server, sid)
        server.container_patch_enabled = True
        session.add(server)
        session.commit()

    old_flag, old_sched = mainmod.HAS_SCHEDULER, mainmod.scheduler
    try:
        mainmod.HAS_SCHEDULER = True
        mainmod.scheduler = SimpleNamespace(get_job=lambda name: None)
        missed = tc.post(
            f"/servers/{sid}/schedule/docker-prune",
            data={"docker_prune_schedule": "0 1 * * *"},
            headers={"X-PiHerder-Async": "1"},
        )
        assert missed.status_code == 409, missed.text[:200]
        missed_html = tc.post(
            f"/servers/{sid}/schedule/docker-prune",
            data={"docker_prune_schedule": "0 1 * * *"},
        )
        assert missed_html.status_code == 303
        mainmod.scheduler = SimpleNamespace(get_job=lambda name: object())
        registered = tc.post(
            f"/servers/{sid}/schedule/docker-prune",
            data={"docker_prune_schedule": "0 1 * * *"},
            headers={"X-PiHerder-Async": "1"},
        )
        assert registered.status_code == 200, registered.text[:200]
        registered_html = tc.post(
            f"/servers/{sid}/schedule/docker-prune",
            data={"docker_prune_schedule": "15 1 * * *"},
        )
        assert registered_html.status_code == 303
        off = tc.post(
            f"/servers/{sid}/schedule/docker-prune",
            data={"docker_prune_schedule": ""},
            headers={"X-PiHerder-Async": "1"},
        )
        assert off.status_code == 200
        off_html = tc.post(
            f"/servers/{sid}/schedule/docker-prune",
            data={"docker_prune_schedule": ""},
        )
        assert off_html.status_code == 303
    finally:
        mainmod.HAS_SCHEDULER = old_flag
        mainmod.scheduler = old_sched

    import builtins
    import importlib

    real_import = builtins.__import__

    def _hide_pycron(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pycron":
            raise ImportError("pycron hidden")
        return real_import(name, globals, locals, fromlist, level)

    saved_pycron = patch_mod.pycron
    builtins.__import__ = _hide_pycron
    try:
        importlib.reload(patch_mod)
    finally:
        builtins.__import__ = real_import
        patch_mod.pycron = saved_pycron

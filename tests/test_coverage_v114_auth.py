"""v1.14 coverage for auth, OIDC callback, and settings error branches.

SQLite is in-process. OIDC HTTP is a fake client. No live IdP, webhook, or SMTP.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pyotp
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.database import get_session
from app.main import app
from app.models import ApiToken, AuditLog, BackupDestination, OidcIdentity, User, WebAuthnCredential
from app.security.auth import (
    ACCOUNT_STEPUP_COOKIE,
    create_access_token,
    create_account_stepup_token,
    create_pending_2fa_token,
    create_trusted_device,
    create_user_access_token,
    encrypt_totp_secret,
    generate_backup_codes,
    get_password_hash,
    replace_backup_codes,
    trusted_cookie_name,
)
from app.security.encryption import encrypt_str
from tests.test_coverage_v17_q1 import _engine

PASSWORD = "SmokeTest1ok"
NEW_PASSWORD = "OtherPass1ok"
ISSUER = "https://idp.example.com"


class _HttpResp:
    def __init__(self, code, payload):
        self.status_code = code
        self._payload = payload
        self.text = "error-body"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("status")

    def json(self):
        return self._payload


class _OidcHttp:
    """Stand-in for httpx.Client. Never opens a socket."""

    mode = "ok"
    userinfo = {
        "sub": "sub-example",
        "email": "user@example.com",
        "email_verified": True,
        "name": "Example User",
    }

    def __init__(self, *args, **kwargs):
        del args, kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers=None):
        del headers
        if _OidcHttp.mode == "bad_discovery" and "openid-configuration" in url:
            raise RuntimeError("discovery down")
        if "openid-configuration" in url:
            return _HttpResp(
                200,
                {
                    "issuer": ISSUER,
                    "authorization_endpoint": ISSUER + "/auth",
                    "token_endpoint": ISSUER + "/token",
                    "userinfo_endpoint": ISSUER + "/userinfo",
                    "jwks_uri": ISSUER + "/jwks",
                },
            )
        if "userinfo" in url:
            if _OidcHttp.mode == "userinfo_down":
                return _HttpResp(401, {"error": "no"})
            return _HttpResp(200, dict(_OidcHttp.userinfo))
        return _HttpResp(404, {})

    def post(self, url, data=None, headers=None):
        del url, data, headers
        if _OidcHttp.mode == "reject":
            return _HttpResp(400, {"error": "invalid_grant"})
        if _OidcHttp.mode == "down":
            raise RuntimeError("token down")
        return _HttpResp(200, {"access_token": "token-example", "token_type": "Bearer"})


def _boot(tmp_path, monkeypatch, name):
    import app.services.app_settings as app_cfg
    import app.services.oidc_svc as oidc

    engine = _engine(tmp_path / name)
    monkeypatch.setattr("app.database.engine", engine)
    store = dict(app_cfg.DEFAULTS)

    def load_settings():
        return dict(store)

    def save_settings(partial):
        store.update(partial or {})
        return dict(store)

    monkeypatch.setattr(app_cfg, "load_settings", load_settings)
    monkeypatch.setattr(app_cfg, "save_settings", save_settings)
    monkeypatch.setattr(app_cfg, "_cache", None)
    monkeypatch.setattr("app.routers.auth.rate_limit_auth", lambda *a, **k: True)
    monkeypatch.setattr("app.routers.auth_oidc.rate_limit_auth", lambda *a, **k: True)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(oidc.httpx, "Client", _OidcHttp)
    oidc.clear_discovery_cache()
    _OidcHttp.mode = "ok"

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    return engine, client, store


def _done():
    app.dependency_overrides.pop(get_session, None)


def _loc(response):
    return response.headers.get("location") or ""


def _user(
    engine,
    email,
    *,
    role="admin",
    password=PASSWORD,
    totp=False,
    active=True,
    password_login=True,
    must_change=False,
):
    secret = pyotp.random_base32() if totp else None
    with Session(engine) as session:
        row = User(
            email=email,
            hashed_password=get_password_hash(password),
            role=role,
            is_active=active,
            must_change_password=must_change,
            password_login_enabled=password_login,
            totp_enabled=bool(totp),
            totp_secret_encrypted=encrypt_totp_secret(secret) if secret else None,
            totp_confirmed_at=datetime.utcnow() if totp else None,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id, secret


def _as(client, engine, uid):
    with Session(engine) as session:
        row = session.get(User, uid)
        client.cookies.set("access_token", create_user_access_token(row))


def _code(secret):
    return pyotp.TOTP(secret).now()


def _passkey(engine, uid, cred_id="cred-example"):
    with Session(engine) as session:
        row = WebAuthnCredential(
            user_id=uid,
            credential_id=cred_id,
            public_key="pk-example",
            nickname="desk",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def _identity(engine, uid, subject, issuer=ISSUER):
    with Session(engine) as session:
        row = OidcIdentity(
            user_id=uid,
            issuer=issuer,
            subject=subject,
            email_at_link="user@example.com",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def _oidc_state(mode, sp, uid=None):
    payload = {
        "oidc": True,
        "mode": mode,
        "cv": "verifier-example",
        "nonce": "nonce-example",
        "sp": sp,
    }
    if uid is not None:
        payload["uid"] = int(uid)
    return create_access_token(payload, expires_delta=timedelta(minutes=10))


def _callback(client, *, mode="login", sp="state-example", uid=None, code="code-example", error=None, cookie=True, query_state=None):
    if error:
        return client.get("/auth/oidc/callback?error=" + error, follow_redirects=False)
    if cookie:
        client.cookies.set("oidc_state", _oidc_state(mode, sp, uid))
    else:
        client.cookies.delete("oidc_state")
    qstate = sp if query_state is None else query_state
    return client.get(
        f"/auth/oidc/callback?code={code}&state={qstate}",
        follow_redirects=False,
    )


def _latest_sso(engine):
    with Session(engine) as session:
        rows = list(
            session.exec(select(AuditLog).where(AuditLog.action == "sso_login")).all()
        )
    if not rows:
        return ""
    rows.sort(key=lambda row: row.id or 0)
    return rows[-1].details or ""


def test_login_failure_branches(tmp_path, monkeypatch):
    import app.routers.auth as auth_routes
    import app.services.oidc_svc as oidc
    import app.services.turnstile as turnstile_svc

    engine, client, _store = _boot(tmp_path, monkeypatch, "login.db")
    admin_id, secret = _user(engine, "user@example.com", totp=True)
    _user(engine, "operator@example.com", role="operator", password=PASSWORD)
    _user(engine, "sso-only@example.com", password_login=False)
    change_id, _ = _user(engine, "change@example.com", totp=True, must_change=True)
    del change_id
    real_audit = auth_routes.make_audit_log
    gate = {"raise_failed": False}

    def wrapped(**kwargs):
        if gate["raise_failed"] and kwargs.get("status") == "failed":
            raise RuntimeError("audit down")
        return real_audit(**kwargs)

    monkeypatch.setattr(auth_routes, "make_audit_log", wrapped)
    try:
        missing = client.post(
            "/auth/login",
            data={"email": "missing@example.com", "password": PASSWORD},
            follow_redirects=False,
        )
        assert missing.status_code == 303 and "error=invalid" in _loc(missing)

        bad = client.post(
            "/auth/login",
            data={"email": "user@example.com", "password": "WrongPass1ok"},
            follow_redirects=False,
        )
        assert bad.status_code == 303 and "error=invalid" in _loc(bad)

        gate["raise_failed"] = True
        rolled = client.post(
            "/auth/login",
            data={"email": "user@example.com", "password": "WrongPass1ok"},
            follow_redirects=False,
        )
        assert rolled.status_code == 303 and "error=invalid" in _loc(rolled)
        gate["raise_failed"] = False

        disabled = client.post(
            "/auth/login",
            data={"email": "sso-only@example.com", "password": PASSWORD},
            follow_redirects=False,
        )
        assert disabled.status_code == 303 and "error=invalid" in _loc(disabled)

        monkeypatch.setattr(auth_routes, "rate_limit_auth", lambda *a, **k: False)
        rated = client.post(
            "/auth/login",
            data={"email": "user@example.com", "password": PASSWORD},
            follow_redirects=False,
        )
        assert rated.status_code == 303 and "error=rate" in _loc(rated)
        monkeypatch.setattr(auth_routes, "rate_limit_auth", lambda *a, **k: True)

        monkeypatch.setattr(turnstile_svc, "turnstile_enabled", lambda: True)
        monkeypatch.setattr(turnstile_svc, "visitor_ip_for_turnstile", lambda request: "192.0.2.10")
        monkeypatch.setattr(
            turnstile_svc,
            "verify_turnstile_token",
            lambda token, remoteip=None: (False, "bad code!"),
        )
        captcha = client.post(
            "/auth/login",
            data={"email": "user@example.com", "password": PASSWORD},
            follow_redirects=False,
        )
        assert captcha.status_code == 303 and "error=captcha" in _loc(captcha)
        assert "code=badcode" in _loc(captcha)

        fallback = client.post(
            "/auth/login",
            data={
                "email": "user@example.com",
                "password": PASSWORD,
                "cf_turnstile_response": "token-example",
            },
            follow_redirects=False,
        )
        assert fallback.status_code == 303 and "error=captcha" in _loc(fallback)
        monkeypatch.setattr(turnstile_svc, "turnstile_enabled", lambda: False)

        monkeypatch.setattr(oidc, "oidc_require_sso", lambda: True)
        sso = client.post(
            "/auth/login",
            data={"email": "operator@example.com", "password": PASSWORD},
            follow_redirects=False,
        )
        assert sso.status_code == 303 and "error=sso_required" in _loc(sso)
        monkeypatch.setattr(oidc, "oidc_require_sso", lambda: False)

        with Session(engine) as session:
            raw, _dev = create_trusted_device(session, admin_id, label="Browser", ip="192.0.2.10")
        client.cookies.set(trusted_cookie_name(admin_id), raw)
        trusted = client.post(
            "/auth/login",
            data={"email": "user@example.com", "password": PASSWORD},
            follow_redirects=False,
        )
        assert trusted.status_code == 303 and _loc(trusted).endswith("/")
        client.cookies.clear()

        pending = client.post(
            "/auth/login",
            data={"email": "user@example.com", "password": PASSWORD},
            follow_redirects=False,
        )
        assert pending.status_code == 303 and _loc(pending).endswith("/auth/2fa")

        forced = client.post(
            "/auth/login",
            data={"email": "change@example.com", "password": PASSWORD},
            follow_redirects=False,
        )
        assert forced.status_code == 303 and "/auth/force-password" in _loc(forced)

        plain_id, _ = _user(engine, "plain@example.com", totp=False)
        del plain_id
        plain = client.post(
            "/auth/login",
            data={"email": "plain@example.com", "password": PASSWORD},
            follow_redirects=False,
        )
        assert plain.status_code == 303 and _loc(plain).endswith("/")
        assert secret
    finally:
        _done()


def test_two_factor_submit_branches(tmp_path, monkeypatch):
    import app.routers.auth as auth_routes
    import app.services.account_stepup as step

    engine, client, _store = _boot(tmp_path, monkeypatch, "twofa.db")
    uid, secret = _user(engine, "user@example.com", totp=True)
    pass_id, _ = _user(engine, "passkey@example.com", totp=False)
    _passkey(engine, pass_id, "cred-pass")
    real_audit = auth_routes.make_audit_log
    real_decrypt = auth_routes.decrypt_totp_secret
    real_verify = auth_routes.verify_totp_code
    gate = {"raise_failed": False}

    def wrapped(**kwargs):
        if gate["raise_failed"] and kwargs.get("status") == "failed":
            raise RuntimeError("audit down")
        return real_audit(**kwargs)

    monkeypatch.setattr(auth_routes, "make_audit_log", wrapped)
    try:
        client.cookies.set("pending_2fa", create_pending_2fa_token(uid))
        monkeypatch.setattr(auth_routes, "rate_limit_auth", lambda *a, **k: False)
        rated = client.post("/auth/2fa", data={"code": "111111"}, follow_redirects=False)
        assert rated.status_code == 303 and "error=rate" in _loc(rated)
        monkeypatch.setattr(auth_routes, "rate_limit_auth", lambda *a, **k: True)

        client.cookies.clear()
        absent = client.post("/auth/2fa", data={"code": "111111"}, follow_redirects=False)
        assert absent.status_code == 303 and _loc(absent).endswith("/auth/login")

        client.cookies.set("pending_2fa", "not-a-token")
        junk = client.post("/auth/2fa", data={"code": "111111"}, follow_redirects=False)
        assert junk.status_code == 303 and _loc(junk).endswith("/auth/login")

        client.cookies.set(
            "pending_2fa",
            create_access_token({"sub": "abc", "2fa_pending": True}, expires_delta=timedelta(minutes=5)),
        )
        bad_sub = client.post("/auth/2fa", data={"code": "111111"}, follow_redirects=False)
        assert bad_sub.status_code == 303 and _loc(bad_sub).endswith("/auth/login")

        client.cookies.set(
            "pending_2fa",
            create_pending_2fa_token(999999),
        )
        gone = client.post("/auth/2fa", data={"code": "111111"}, follow_redirects=False)
        assert gone.status_code == 303 and _loc(gone).endswith("/auth/login")

        client.cookies.set("pending_2fa", create_pending_2fa_token(pass_id))
        use_pk = client.post("/auth/2fa", data={"code": "111111"}, follow_redirects=False)
        assert use_pk.status_code == 303 and "error=use_passkey" in _loc(use_pk)

        real_factor = step.factor_allowed

        def no_codes(surface, factor):
            if surface == "login" and factor in ("totp", "backup"):
                return False
            return real_factor(surface, factor)

        monkeypatch.setattr(step, "factor_allowed", no_codes)
        client.cookies.set("pending_2fa", create_pending_2fa_token(uid))
        blocked = client.post("/auth/2fa", data={"code": _code(secret)}, follow_redirects=False)
        assert blocked.status_code == 303 and "error=use_passkey" in _loc(blocked)
        monkeypatch.setattr(step, "factor_allowed", real_factor)

        client.cookies.set("pending_2fa", create_pending_2fa_token(uid))
        bad = client.post("/auth/2fa", data={"code": "111111"}, follow_redirects=False)
        assert bad.status_code == 303 and "error=invalid" in _loc(bad)

        gate["raise_failed"] = True
        rolled = client.post("/auth/2fa", data={"code": ""}, follow_redirects=False)
        assert rolled.status_code == 303 and "error=invalid" in _loc(rolled)
        gate["raise_failed"] = False

        monkeypatch.setattr(auth_routes, "decrypt_totp_secret", lambda cipher: (_ for _ in ()).throw(RuntimeError("decrypt")))
        blown = client.post("/auth/2fa", data={"code": _code(secret)}, follow_redirects=False)
        assert blown.status_code == 303 and "error=invalid" in _loc(blown)
        monkeypatch.setattr(auth_routes, "decrypt_totp_secret", real_decrypt)

        monkeypatch.setattr(auth_routes, "verify_totp_code", lambda secret, code: (_ for _ in ()).throw(RuntimeError("totp")))
        verify_boom = client.post("/auth/2fa", data={"code": "111111"}, follow_redirects=False)
        assert verify_boom.status_code == 303 and "error=invalid" in _loc(verify_boom)
        monkeypatch.setattr(auth_routes, "verify_totp_code", real_verify)

        with Session(engine) as session:
            codes = generate_backup_codes(count=2)
            replace_backup_codes(session, uid, codes)
        backup = client.post(
            "/auth/2fa",
            data={"code": codes[0], "trust_device": "on"},
            follow_redirects=False,
        )
        assert backup.status_code == 303 and "error=" not in _loc(backup)

        client.cookies.set("pending_2fa", create_pending_2fa_token(uid))
        ok = client.post("/auth/2fa", data={"code": _code(secret)}, follow_redirects=False)
        assert ok.status_code == 303 and _loc(ok).endswith("/")
    finally:
        _done()


def test_two_factor_webauthn_verify_branches(tmp_path, monkeypatch):
    import app.routers.auth as auth_routes
    import app.services.webauthn_svc as wa

    engine, client, _store = _boot(tmp_path, monkeypatch, "wa-login.db")
    uid, _secret = _user(engine, "user@example.com", totp=True)
    real_audit = auth_routes.make_audit_log
    gate = {"raise_failed": False, "kind": "ok"}

    def wrapped(**kwargs):
        if gate["raise_failed"] and kwargs.get("status") == "failed":
            raise RuntimeError("audit down")
        return real_audit(**kwargs)

    def verify(session, user, credential, chal):
        del session, user, credential, chal
        if gate["kind"] == "verify":
            raise wa.WebAuthnVerifyError("bad ceremony")
        if gate["kind"] == "boom":
            raise RuntimeError("verify down")
        return SimpleNamespace(id=1)

    monkeypatch.setattr(auth_routes, "make_audit_log", wrapped)
    monkeypatch.setattr(wa, "verify_authentication", verify)
    try:
        client.cookies.set("pending_2fa", create_pending_2fa_token(uid))
        monkeypatch.setattr(auth_routes, "rate_limit_auth", lambda *a, **k: False)
        rated = client.post("/auth/2fa/webauthn/verify", json={"credential": {"id": "x"}}, follow_redirects=False)
        assert rated.status_code == 429 and rated.json()["error"] == "rate"
        monkeypatch.setattr(auth_routes, "rate_limit_auth", lambda *a, **k: True)

        client.cookies.clear()
        missing = client.post("/auth/2fa/webauthn/verify", json={"credential": {"id": "x"}})
        assert missing.status_code == 401 and missing.json()["error"] == "session"

        client.cookies.set("pending_2fa", create_pending_2fa_token(uid))
        bad_json = client.post(
            "/auth/2fa/webauthn/verify",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
        assert bad_json.status_code == 400 and bad_json.json()["error"] == "bad_json"

        missing_cred = client.post("/auth/2fa/webauthn/verify", json={"trust_device": True})
        assert missing_cred.status_code == 400 and missing_cred.json()["error"] == "missing_credential"

        not_dict = client.post("/auth/2fa/webauthn/verify", json=["nope"])
        assert not_dict.status_code == 400 and not_dict.json()["error"] == "missing_credential"

        gate["kind"] = "verify"
        failed = client.post("/auth/2fa/webauthn/verify", json={"credential": {"id": "x"}})
        assert failed.status_code == 400 and "bad ceremony" in failed.json()["error"]

        gate["raise_failed"] = True
        rolled = client.post("/auth/2fa/webauthn/verify", json={"credential": {"id": "x"}})
        assert rolled.status_code == 400 and "bad ceremony" in rolled.json()["error"]
        gate["raise_failed"] = False

        gate["kind"] = "boom"
        blown = client.post("/auth/2fa/webauthn/verify", json={"credential": {"id": "x"}})
        assert blown.status_code == 400 and blown.json()["error"] == "verify_failed"

        gate["kind"] = "ok"
        ok = client.post("/auth/2fa/webauthn/verify", json={"credential": {"id": "x"}, "trust_device": False})
        assert ok.status_code == 200 and ok.json()["ok"] is True

        client.cookies.set("pending_2fa", create_pending_2fa_token(uid))
        trusted = client.post(
            "/auth/2fa/webauthn/verify",
            json={"credential": {"id": "x"}, "trust_device": True},
        )
        assert trusted.status_code == 200 and trusted.json()["redirect"]
    finally:
        _done()


def test_password_totp_and_backup_branches(tmp_path, monkeypatch):
    import app.routers.auth as auth_routes

    engine, client, _store = _boot(tmp_path, monkeypatch, "account.db")
    uid, secret = _user(engine, "user@example.com", totp=False)
    plain_id, _ = _user(engine, "plain@example.com", totp=False)
    real_decrypt = auth_routes.decrypt_totp_secret
    try:
        _as(client, engine, uid)
        no_setup = client.post("/auth/account/2fa/confirm", data={"code": "111111"}, follow_redirects=False)
        assert no_setup.status_code == 303 and "error=2fa_no_setup" in _loc(no_setup)

        with Session(engine) as session:
            row = session.get(User, uid)
            row.totp_secret_encrypted = "not-a-secret"
            row.totp_enabled = False
            session.add(row)
            session.commit()
        monkeypatch.setattr(
            auth_routes,
            "decrypt_totp_secret",
            lambda cipher: (_ for _ in ()).throw(RuntimeError("decrypt")),
        )
        decrypt_fail = client.post("/auth/account/2fa/confirm", data={"code": "111111"}, follow_redirects=False)
        assert decrypt_fail.status_code == 303 and "error=2fa_no_setup" in _loc(decrypt_fail)
        monkeypatch.setattr(auth_routes, "decrypt_totp_secret", real_decrypt)

        with Session(engine) as session:
            row = session.get(User, uid)
            row.totp_secret_encrypted = encrypt_totp_secret(secret or pyotp.random_base32())
            session.add(row)
            session.commit()
            stored = auth_routes.decrypt_totp_secret(row.totp_secret_encrypted)
        bad = client.post("/auth/account/2fa/confirm", data={"code": "111111"}, follow_redirects=False)
        assert bad.status_code == 303 and "error=2fa_bad_code" in _loc(bad)

        client.cookies.set("totp_setup_secret", stored)
        bad_cookie = client.post("/auth/account/2fa/confirm", data={"code": "111111"}, follow_redirects=False)
        assert bad_cookie.status_code == 303 and "error=2fa_bad_code" in _loc(bad_cookie)

        confirmed = client.post(
            "/auth/account/2fa/confirm",
            data={"code": _code(stored)},
            follow_redirects=False,
        )
        assert confirmed.status_code == 303 and "msg=2fa_enabled" in _loc(confirmed)

        _as(client, engine, plain_id)
        off = client.post(
            "/auth/account/2fa/backup-codes",
            data={"current_password": PASSWORD, "code": ""},
            follow_redirects=False,
        )
        assert off.status_code == 303 and "error=2fa_off" in _loc(off)

        wrong = client.post(
            "/auth/account/2fa/disable",
            data={"current_password": "WrongPass1ok", "code": ""},
            follow_redirects=False,
        )
        assert wrong.status_code == 303 and "error=password_required" in _loc(wrong)

        disabled = client.post(
            "/auth/account/2fa/disable",
            data={"current_password": PASSWORD, "code": ""},
            follow_redirects=False,
        )
        assert disabled.status_code == 303 and "msg=2fa_disabled" in _loc(disabled)

        _as(client, engine, uid)
        bad_regen = client.post(
            "/auth/account/2fa/backup-codes",
            data={"current_password": PASSWORD, "code": "111111"},
            follow_redirects=False,
        )
        assert bad_regen.status_code == 303 and "error=2fa_bad_code" in _loc(bad_regen)

        regen = client.post(
            "/auth/account/2fa/backup-codes",
            data={"current_password": "WrongPass1ok", "code": _code(stored)},
            follow_redirects=False,
        )
        assert regen.status_code == 303 and "msg=backup_codes" in _loc(regen)

        bad_disable = client.post(
            "/auth/account/2fa/disable",
            data={"current_password": PASSWORD, "code": "111111"},
            follow_redirects=False,
        )
        assert bad_disable.status_code == 303 and "error=2fa_bad_code" in _loc(bad_disable)

        turned_off = client.post(
            "/auth/account/2fa/disable",
            data={"current_password": "", "code": _code(stored)},
            follow_redirects=False,
        )
        assert turned_off.status_code == 303 and "msg=2fa_disabled" in _loc(turned_off)

        _as(client, engine, plain_id)
        bad_pw = client.post(
            "/auth/account/password",
            data={"current_password": "WrongPass1ok", "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
            follow_redirects=False,
        )
        assert bad_pw.status_code == 303 and "error=bad_password" in _loc(bad_pw)

        mismatch = client.post(
            "/auth/account/password",
            data={"current_password": PASSWORD, "new_password": NEW_PASSWORD, "confirm_password": "OtherPass1xx"},
            follow_redirects=False,
        )
        assert mismatch.status_code == 303 and "error=password_mismatch" in _loc(mismatch)

        policy = client.post(
            "/auth/account/password",
            data={"current_password": PASSWORD, "new_password": "short", "confirm_password": "short"},
            follow_redirects=False,
        )
        assert policy.status_code == 303 and "error=password_policy" in _loc(policy)

        changed = client.post(
            "/auth/account/password",
            data={"current_password": PASSWORD, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
            follow_redirects=False,
        )
        assert changed.status_code == 303 and "msg=password_changed" in _loc(changed)
    finally:
        _done()


def test_webauthn_account_branches(tmp_path, monkeypatch):
    import app.services.webauthn_svc as wa

    engine, client, _store = _boot(tmp_path, monkeypatch, "wa-account.db")
    uid, _secret = _user(engine, "user@example.com")
    gate = {"kind": "ok"}

    def reg_options(session, user):
        del session, user
        if gate["kind"] == "config":
            raise wa.WebAuthnConfigError("rp missing")
        if gate["kind"] == "boom":
            raise RuntimeError("options down")
        return ('{"challenge":"reg","timeout":60000}', "chal-example")

    def reg_verify(session, user, credential, chal, nickname=None):
        del session, user, credential, chal, nickname
        if gate["kind"] == "verify":
            raise wa.WebAuthnVerifyError("bad ceremony")
        if gate["kind"] == "config":
            raise wa.WebAuthnConfigError("rp missing")
        if gate["kind"] == "boom":
            raise RuntimeError("verify down")
        return SimpleNamespace(
            id=4,
            nickname="desk",
            created_at=datetime.utcnow(),
            last_used_at=None,
            aaguid=None,
            backup_eligible=False,
            backup_state=False,
        )

    def auth_options(session, user):
        del session, user
        if gate["kind"] == "config":
            raise wa.WebAuthnConfigError("no passkey rp")
        if gate["kind"] == "boom":
            raise RuntimeError("options down")
        return ('{"challenge":"auth","timeout":60000}', "chal-example")

    def auth_verify(session, user, credential, chal):
        del session, user, credential, chal
        if gate["kind"] == "verify":
            raise wa.WebAuthnVerifyError("bad ceremony")
        if gate["kind"] == "boom":
            raise RuntimeError("verify down")
        return SimpleNamespace(id=4)

    monkeypatch.setattr(wa, "registration_options_json", reg_options)
    monkeypatch.setattr(wa, "verify_registration", reg_verify)
    monkeypatch.setattr(wa, "authentication_options_json", auth_options)
    monkeypatch.setattr(wa, "verify_authentication", auth_verify)
    try:
        _as(client, engine, uid)
        gate["kind"] = "config"
        cfg = client.post("/auth/account/webauthn/register/options")
        assert cfg.status_code == 400 and "rp missing" in cfg.json()["error"]
        gate["kind"] = "boom"
        blown = client.post("/auth/account/webauthn/register/options")
        assert blown.status_code == 500 and blown.json()["error"] == "options_failed"
        gate["kind"] = "ok"
        ready = client.post("/auth/account/webauthn/register/options")
        assert ready.status_code == 200 and ready.json()["ok"] is True

        bad_json = client.post(
            "/auth/account/webauthn/register/verify",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
        assert bad_json.status_code == 400 and bad_json.json()["error"] == "bad_json"
        missing = client.post("/auth/account/webauthn/register/verify", json={"nickname": "desk"})
        assert missing.status_code == 400 and missing.json()["error"] == "missing_credential"
        not_dict = client.post("/auth/account/webauthn/register/verify", json=["nope"])
        assert not_dict.status_code == 400

        gate["kind"] = "verify"
        failed = client.post("/auth/account/webauthn/register/verify", json={"credential": {"id": "x"}})
        assert failed.status_code == 400 and "bad ceremony" in failed.json()["error"]
        gate["kind"] = "config"
        cfg_v = client.post("/auth/account/webauthn/register/verify", json={"credential": {"id": "x"}, "nickname": "desk"})
        assert cfg_v.status_code == 400 and "rp missing" in cfg_v.json()["error"]
        gate["kind"] = "boom"
        boom = client.post("/auth/account/webauthn/register/verify", json={"credential": {"id": "x"}})
        assert boom.status_code == 400 and boom.json()["error"] == "verify_failed"
        gate["kind"] = "ok"
        added = client.post(
            "/auth/account/webauthn/register/verify",
            json={"credential": {"id": "x"}, "nickname": "desk"},
        )
        assert added.status_code == 200 and added.json()["credential"]["nickname"] == "desk"

        empty = client.post("/auth/account/webauthn/stepup/options")
        assert empty.status_code == 400 and "No passkeys" in empty.json()["error"]

        cred = _passkey(engine, uid, "cred-step")
        gate["kind"] = "config"
        step_cfg = client.post("/auth/account/webauthn/stepup/options")
        assert step_cfg.status_code == 400 and "no passkey rp" in step_cfg.json()["error"]
        gate["kind"] = "boom"
        step_boom = client.post("/auth/account/webauthn/stepup/options")
        assert step_boom.status_code == 500 and step_boom.json()["error"] == "options_failed"
        gate["kind"] = "ok"
        step_ok = client.post("/auth/account/webauthn/stepup/options")
        assert step_ok.status_code == 200 and step_ok.json()["ok"] is True

        bad_step = client.post(
            "/auth/account/webauthn/stepup/verify",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
        assert bad_step.status_code == 400 and bad_step.json()["error"] == "bad_json"
        miss_step = client.post("/auth/account/webauthn/stepup/verify", json={})
        assert miss_step.status_code == 400 and miss_step.json()["error"] == "missing_credential"
        gate["kind"] = "verify"
        bad_cer = client.post("/auth/account/webauthn/stepup/verify", json={"credential": {"id": "x"}})
        assert bad_cer.status_code == 400 and "bad ceremony" in bad_cer.json()["error"]
        gate["kind"] = "boom"
        step_fail = client.post("/auth/account/webauthn/stepup/verify", json={"credential": {"id": "x"}})
        assert step_fail.status_code == 400 and step_fail.json()["error"] == "verify_failed"
        gate["kind"] = "ok"
        step_pass = client.post("/auth/account/webauthn/stepup/verify", json={"credential": {"id": "x"}})
        assert step_pass.status_code == 200 and step_pass.json()["ok"] is True

        client.cookies.clear()
        _as(client, engine, uid)
        denied = client.post(
            f"/auth/account/webauthn/{cred}/revoke",
            data={"current_password": "WrongPass1ok", "totp_code": ""},
            follow_redirects=False,
        )
        assert denied.status_code == 303 and "error=" in _loc(denied)

        client.cookies.set(ACCOUNT_STEPUP_COOKIE, create_account_stepup_token(uid))
        missing_row = client.post(
            "/auth/account/webauthn/999999/revoke",
            data={"current_password": "", "totp_code": ""},
            follow_redirects=False,
        )
        assert missing_row.status_code == 303 and "error=passkey_not_found" in _loc(missing_row)

        revoked = client.post(
            f"/auth/account/webauthn/{cred}/revoke",
            data={"current_password": "", "totp_code": ""},
            follow_redirects=False,
        )
        assert revoked.status_code == 303 and "msg=passkey_revoked" in _loc(revoked)
    finally:
        _done()


def test_oidc_callback_and_finish_login(tmp_path, monkeypatch):
    import app.services.oidc_svc as oidc

    engine, client, store = _boot(tmp_path, monkeypatch, "oidc.db")
    store.update(
        {
            "oidc_enabled": True,
            "oidc_issuer": ISSUER,
            "oidc_client_id": "client-example",
            "oidc_client_secret_encrypted": encrypt_str("token-example"),
            "oidc_auto_link_by_email": True,
            "oidc_require_email_verified": True,
            "oidc_sync_roles_on_login": True,
            "oidc_default_role": "viewer",
            "oidc_role_claim": "groups",
            "oidc_role_map": '{"ops": "operator"}',
            "oidc_allowed_email_domains": "",
            "oidc_require_sso": False,
            "oidc_idp_mfa_satisfies_login_2fa": False,
            "login_trusted_skip_2fa": True,
        }
    )
    admin_id, admin_secret = _user(engine, "admin@example.com", role="admin", totp=False)
    viewer_id, _ = _user(engine, "viewer@example.com", role="viewer")
    link_id, _ = _user(engine, "link@example.com", role="viewer")
    inactive_id, _ = _user(engine, "inactive@example.com", active=False)
    _identity(engine, admin_id, "sub-admin")
    real_claims = oidc.claims_from_tokens
    try:
        denied = _callback(client, error="access_denied")
        assert denied.status_code == 303 and "error=sso_denied" in _loc(denied)

        no_cookie = _callback(client, cookie=False)
        assert no_cookie.status_code == 303 and "error=sso_state" in _loc(no_cookie)

        mismatch = _callback(client, query_state="other-state")
        assert mismatch.status_code == 303 and "error=sso_state" in _loc(mismatch)

        empty = _callback(client, code="")
        assert empty.status_code == 303 and "error=sso_state" in _loc(empty)

        _OidcHttp.mode = "bad_discovery"
        oidc.clear_discovery_cache()
        cfg_login = _callback(client, mode="login", sp="cfg-login")
        assert cfg_login.status_code == 303 and "error=sso_config" in _loc(cfg_login)
        cfg_link = _callback(client, mode="link", sp="cfg-link", uid=admin_id)
        assert cfg_link.status_code == 303 and "/auth/account" in _loc(cfg_link) and "error=sso_config" in _loc(cfg_link)

        _OidcHttp.mode = "reject"
        oidc.clear_discovery_cache()
        flow_login = _callback(client, mode="login", sp="flow-login")
        assert flow_login.status_code == 303 and "error=sso_" in _loc(flow_login)
        flow_link = _callback(client, mode="link", sp="flow-link", uid=admin_id)
        assert flow_link.status_code == 303 and "/auth/account" in _loc(flow_link) and "error=" in _loc(flow_link)

        _OidcHttp.mode = "down"
        oidc.clear_discovery_cache()
        down = _callback(client, sp="down")
        assert down.status_code == 303 and "error=sso_" in _loc(down)

        _OidcHttp.mode = "userinfo_down"
        oidc.clear_discovery_cache()
        info = _callback(client, sp="userinfo")
        assert info.status_code == 303 and "error=sso_" in _loc(info)

        _OidcHttp.mode = "ok"
        oidc.clear_discovery_cache()

        def boom(*args, **kwargs):
            del args, kwargs
            raise RuntimeError("parse")

        monkeypatch.setattr(oidc, "claims_from_tokens", boom)
        unexpected = _callback(client, sp="boom")
        assert unexpected.status_code == 303 and "error=sso_denied" in _loc(unexpected)
        monkeypatch.setattr(oidc, "claims_from_tokens", real_claims)

        _OidcHttp.userinfo = {
            "sub": "sub-link",
            "email": "link@example.com",
            "email_verified": True,
        }
        no_uid = _callback(client, mode="link", sp="link-noid")
        assert no_uid.status_code == 303 and "error=sso_state" in _loc(no_uid)

        inactive = _callback(client, mode="link", sp="link-off", uid=inactive_id)
        assert inactive.status_code == 303 and "error=sso_inactive" in _loc(inactive)

        linked = _callback(client, mode="link", sp="link-ok", uid=link_id)
        assert linked.status_code == 303 and "msg=sso_linked" in _loc(linked)

        _OidcHttp.userinfo = {
            "sub": "sub-email",
            "email": "viewer@example.com",
            "email_verified": True,
            "name": "Viewer",
        }
        matched = _callback(client, sp="email-match")
        assert matched.status_code == 303 and "error=" not in _loc(matched)
        assert "2FA pending" not in _latest_sso(engine)

        _OidcHttp.userinfo = {
            "sub": "sub-jit",
            "email": "jit@example.com",
            "email_verified": True,
            "name": "JIT User",
        }
        provisioned = _callback(client, sp="jit")
        assert provisioned.status_code == 303 and _loc(provisioned).endswith("/")

        _OidcHttp.userinfo = {
            "sub": "sub-viewer-role",
            "email": "nobody-role@example.com",
            "email_verified": True,
            "groups": ["ops"],
        }
        # Create the viewer row via email match first, then a second login syncs the role.
        _identity_email = "role@example.com"
        role_uid, _ = _user(engine, _identity_email, role="viewer")
        _identity(engine, role_uid, "sub-viewer-role")
        _OidcHttp.userinfo = {
            "sub": "sub-viewer-role",
            "email": _identity_email,
            "email_verified": True,
            "groups": ["ops"],
        }
        changed = _callback(client, sp="role-change")
        assert changed.status_code == 303 and "error=" not in _loc(changed)
        with Session(engine) as session:
            assert session.get(User, role_uid).role == "operator"
        unchanged = _callback(client, sp="role-same")
        assert unchanged.status_code == 303

        _OidcHttp.userinfo = {
            "sub": "sub-admin",
            "email": "admin@example.com",
            "email_verified": True,
            "groups": [],
        }
        skipped = _callback(client, sp="sole-admin")
        assert skipped.status_code == 303 and _loc(skipped).endswith("/")
        with Session(engine) as session:
            assert session.get(User, admin_id).role == "admin"
        assert "IdP MFA" not in _latest_sso(engine)

        with Session(engine) as session:
            row = session.get(User, admin_id)
            row.totp_enabled = True
            row.totp_secret_encrypted = encrypt_totp_secret(admin_secret or pyotp.random_base32())
            session.add(row)
            session.commit()
        pending = _callback(client, sp="pending-2fa")
        assert pending.status_code == 303 and _loc(pending).endswith("/auth/2fa")
        assert "2FA pending" in _latest_sso(engine)

        with Session(engine) as session:
            raw, _dev = create_trusted_device(session, admin_id, label="Browser", ip="192.0.2.10")
        client.cookies.set(trusted_cookie_name(admin_id), raw)
        trusted = _callback(client, sp="trusted")
        assert trusted.status_code == 303 and _loc(trusted).endswith("/")
        assert "trusted device" in _latest_sso(engine)
        client.cookies.delete(trusted_cookie_name(admin_id))

        store["oidc_idp_mfa_satisfies_login_2fa"] = True
        _OidcHttp.userinfo = {
            "sub": "sub-admin",
            "email": "admin@example.com",
            "email_verified": True,
            "amr": ["mfa"],
        }
        idp = _callback(client, sp="idp-mfa")
        assert idp.status_code == 303 and _loc(idp).endswith("/")
        assert "IdP MFA" in _latest_sso(engine)
        store["oidc_idp_mfa_satisfies_login_2fa"] = False

        store["oidc_allowed_email_domains"] = "example.com"
        _OidcHttp.userinfo = {
            "sub": "sub-domain",
            "email": "user@other.test",
            "email_verified": True,
        }
        domain = _callback(client, sp="domain")
        assert domain.status_code == 303 and "error=sso_domain" in _loc(domain)
        store["oidc_allowed_email_domains"] = ""

        _OidcHttp.userinfo = {
            "sub": "sub-unverified",
            "email": "fresh@example.com",
            "email_verified": False,
        }
        unverified = _callback(client, sp="unverified")
        assert unverified.status_code == 303 and "error=sso_email" in _loc(unverified)

        store["oidc_auto_link_by_email"] = False
        _OidcHttp.userinfo = {
            "sub": "sub-taken-email",
            "email": "admin@example.com",
            "email_verified": True,
        }
        conflict = _callback(client, sp="email-exists")
        assert conflict.status_code == 303 and "error=sso_email_conflict" in _loc(conflict)
        store["oidc_auto_link_by_email"] = True

        _OidcHttp.userinfo = {
            "sub": "sub-different",
            "email": "viewer@example.com",
            "email_verified": True,
        }
        # viewer was linked by email_match above, so a new sub conflicts.
        different = _callback(client, sp="different-sub")
        assert different.status_code == 303 and "error=" in _loc(different)

        _OidcHttp.userinfo = {
            "sub": "sub-link-viewer",
            "email": "viewer@example.com",
            "email_verified": True,
        }
        link_conflict = _callback(client, mode="link", sp="link-conflict", uid=viewer_id)
        assert link_conflict.status_code == 303 and "/auth/account" in _loc(link_conflict) and "error=" in _loc(link_conflict)
    finally:
        _done()


def test_oidc_link_unlink_and_password(tmp_path, monkeypatch):
    import app.services.oidc_svc as oidc

    engine, client, store = _boot(tmp_path, monkeypatch, "oidc-account.db")
    store.update(
        {
            "oidc_enabled": False,
            "oidc_issuer": ISSUER,
            "oidc_client_id": "client-example",
            "oidc_client_secret_encrypted": encrypt_str("token-example"),
        }
    )
    uid, secret = _user(engine, "user@example.com", totp=True)
    other_id, _ = _user(engine, "other@example.com", totp=False)
    plain_id, _ = _user(engine, "plain@example.com", totp=False)
    sso_id, _ = _user(engine, "sso@example.com", totp=False, password_login=False)
    sso_2fa, sso_secret = _user(engine, "sso2@example.com", totp=True, password_login=False)
    try:
        _as(client, engine, uid)
        disabled = client.get("/auth/oidc/link", follow_redirects=False)
        assert disabled.status_code == 303 and "error=sso_disabled" in _loc(disabled)
        post_off = client.post(
            "/auth/oidc/link",
            data={"current_password": PASSWORD, "totp_code": ""},
            follow_redirects=False,
        )
        assert post_off.status_code == 303 and "error=sso_disabled" in _loc(post_off)

        store["oidc_enabled"] = True
        oidc.clear_discovery_cache()
        needs_cookie = client.get("/auth/oidc/link", follow_redirects=False)
        assert needs_cookie.status_code == 303 and "error=sso_2fa_link" in _loc(needs_cookie)

        client.cookies.set("oidc_link_ok", "not-a-token")
        junk = client.get("/auth/oidc/link", follow_redirects=False)
        assert junk.status_code == 303 and "error=sso_2fa_link" in _loc(junk)

        with Session(engine) as session:
            row = session.get(User, uid)
            client.cookies.set("oidc_link_ok", create_user_access_token(row))
        no_flag = client.get("/auth/oidc/link", follow_redirects=False)
        assert no_flag.status_code == 303 and "error=sso_2fa_link" in _loc(no_flag)

        client.cookies.set(
            "oidc_link_ok",
            create_access_token({"sub": "abc", "oidc_link": True}, expires_delta=timedelta(minutes=2)),
        )
        bad_sub = client.get("/auth/oidc/link", follow_redirects=False)
        assert bad_sub.status_code == 303 and "error=sso_2fa_link" in _loc(bad_sub)

        client.cookies.set(
            "oidc_link_ok",
            create_access_token({"sub": str(other_id), "oidc_link": True}, expires_delta=timedelta(minutes=2)),
        )
        other = client.get("/auth/oidc/link", follow_redirects=False)
        assert other.status_code == 303 and "error=sso_2fa_link" in _loc(other)

        client.cookies.set(
            "oidc_link_ok",
            create_access_token({"sub": str(uid), "oidc_link": True}, expires_delta=timedelta(minutes=2)),
        )
        store["oidc_client_id"] = ""
        cfg = client.get("/auth/oidc/link", follow_redirects=False)
        assert cfg.status_code == 303 and "error=sso_config" in _loc(cfg)

        store["oidc_client_id"] = "client-example"
        _OidcHttp.mode = "ok"
        oidc.clear_discovery_cache()
        client.cookies.set(
            "oidc_link_ok",
            create_access_token({"sub": str(uid), "oidc_link": True}, expires_delta=timedelta(minutes=2)),
        )
        started = client.get("/auth/oidc/link", follow_redirects=False)
        assert started.status_code == 303 and ISSUER in _loc(started)

        bad_step = client.post(
            "/auth/oidc/link",
            data={"current_password": PASSWORD, "totp_code": "111111"},
            follow_redirects=False,
        )
        assert bad_step.status_code == 303 and "error=" in _loc(bad_step)

        good_step = client.post(
            "/auth/oidc/link",
            data={"current_password": "", "totp_code": _code(secret)},
            follow_redirects=False,
        )
        assert good_step.status_code == 303 and _loc(good_step).endswith("/auth/oidc/link")

        _as(client, engine, plain_id)
        no_link = client.post(
            "/auth/account/password/remove",
            data={"current_password": PASSWORD, "totp_code": ""},
            follow_redirects=False,
        )
        assert no_link.status_code == 303 and "error=sso_required" in _loc(no_link)

        ident = _identity(engine, plain_id, "sub-plain")
        bad_remove = client.post(
            "/auth/account/password/remove",
            data={"current_password": "WrongPass1ok", "totp_code": ""},
            follow_redirects=False,
        )
        assert bad_remove.status_code == 303 and "error=password_required" in _loc(bad_remove)
        removed = client.post(
            "/auth/account/password/remove",
            data={"current_password": PASSWORD, "totp_code": ""},
            follow_redirects=False,
        )
        assert removed.status_code == 303 and "msg=password_removed" in _loc(removed)
        again = client.post(
            "/auth/account/password/remove",
            data={"current_password": PASSWORD, "totp_code": ""},
            follow_redirects=False,
        )
        assert again.status_code == 303 and "msg=password_already_removed" in _loc(again)

        _as(client, engine, uid)
        totp_ident = _identity(engine, uid, "sub-totp")
        bad_totp_remove = client.post(
            "/auth/account/password/remove",
            data={"current_password": PASSWORD, "totp_code": "111111"},
            follow_redirects=False,
        )
        assert bad_totp_remove.status_code == 303 and "error=" in _loc(bad_totp_remove)
        totp_removed = client.post(
            "/auth/account/password/remove",
            data={"current_password": "", "totp_code": _code(secret)},
            follow_redirects=False,
        )
        assert totp_removed.status_code == 303 and "msg=password_removed" in _loc(totp_removed)

        _as(client, engine, sso_id)
        mismatch = client.post(
            "/auth/account/password/set",
            data={"new_password": NEW_PASSWORD, "confirm_password": "OtherPass1xx"},
            follow_redirects=False,
        )
        assert mismatch.status_code in (303, 401, 403)
        weak = client.post(
            "/auth/account/password/set",
            data={"new_password": "short", "confirm_password": "short"},
            follow_redirects=False,
        )
        assert weak.status_code == 303 and "error=password_policy" in _loc(weak)
        set_ok = client.post(
            "/auth/account/password/set",
            data={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
            follow_redirects=False,
        )
        assert set_ok.status_code == 303 and "msg=password_set" in _loc(set_ok)
        _as(client, engine, sso_id)
        use_change = client.post(
            "/auth/account/password/set",
            data={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
            follow_redirects=False,
        )
        assert use_change.status_code == 303 and "error=use_change_password" in _loc(use_change)

        _as(client, engine, sso_2fa)
        need_code = client.post(
            "/auth/account/password/set",
            data={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD, "totp_code": "111111"},
            follow_redirects=False,
        )
        assert need_code.status_code == 303 and "error=" in _loc(need_code)
        set_2fa = client.post(
            "/auth/account/password/set",
            data={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD, "totp_code": _code(sso_secret)},
            follow_redirects=False,
        )
        assert set_2fa.status_code == 303 and "msg=password_set" in _loc(set_2fa)

        _as(client, engine, plain_id)
        missing = client.post(
            "/auth/oidc/unlink",
            data={"identity_id": "999999", "current_password": PASSWORD},
            follow_redirects=False,
        )
        assert missing.status_code == 303 and "error=sso_not_found" in _loc(missing)
        foreign = client.post(
            "/auth/oidc/unlink",
            data={"identity_id": str(totp_ident), "current_password": PASSWORD},
            follow_redirects=False,
        )
        assert foreign.status_code == 303 and "error=sso_not_found" in _loc(foreign)

        bad_pw = client.post(
            "/auth/oidc/unlink",
            data={"identity_id": str(ident), "current_password": "WrongPass1ok"},
            follow_redirects=False,
        )
        # password was removed, so stepup without 2FA succeeds and then a new password is required
        assert bad_pw.status_code == 303 and "error=password_mismatch" in _loc(bad_pw)
        weak_unlink = client.post(
            "/auth/oidc/unlink",
            data={
                "identity_id": str(ident),
                "new_password": "short",
                "confirm_password": "short",
            },
            follow_redirects=False,
        )
        assert weak_unlink.status_code == 303 and "error=password_policy" in _loc(weak_unlink)
        unlinked = client.post(
            "/auth/oidc/unlink",
            data={
                "identity_id": str(ident),
                "new_password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
            },
            follow_redirects=False,
        )
        assert unlinked.status_code == 303 and "msg=sso_unlinked" in _loc(unlinked)

        kept = _identity(engine, other_id, "sub-kept")
        _as(client, engine, other_id)
        step_fail = client.post(
            "/auth/oidc/unlink",
            data={"identity_id": str(kept), "current_password": "WrongPass1ok"},
            follow_redirects=False,
        )
        assert step_fail.status_code == 303 and "error=password_required" in _loc(step_fail)
        dropped = client.post(
            "/auth/oidc/unlink",
            data={"identity_id": str(kept), "current_password": PASSWORD},
            follow_redirects=False,
        )
        assert dropped.status_code == 303 and "msg=sso_unlinked" in _loc(dropped)
    finally:
        _done()


def test_settings_page_and_admin_routes(tmp_path, monkeypatch):
    import app.routers.settings as settings_mod
    import app.security.auth as sec
    import app.services.alert_channels as alert_ch
    import app.services.backup_replicate as copies
    import app.services.oidc_svc as oidc

    engine, client, store = _boot(tmp_path, monkeypatch, "settings.db")
    admin_id, _ = _user(engine, "admin@example.com", role="admin")
    op_id, _ = _user(engine, "operator@example.com", role="operator")
    monkeypatch.setattr(settings_mod.hb, "list_backups", lambda: [])
    now = datetime.utcnow()
    with Session(engine) as session:
        for provider, cfg in (
            ("drive", {"remote_dir": "PiHerder", "copy_herder_backup": True, "oauth_client_id": "client-example"}),
            ("smb", {"host": "host-a.example.com", "share": "backups", "remote_dir": "pi", "copy_herder_backup": True, "domain": "example"}),
            ("onedrive", {"remote_dir": "PiHerder", "copy_herder_backup": True, "oauth_client_id": "od-example"}),
        ):
            session.add(
                BackupDestination(
                    name=provider,
                    provider=provider,
                    enabled=True,
                    schedule="0 3 * * *",
                    after_host_backup=True,
                    credentials_encrypted="token-example",
                    config_json=json.dumps(cfg),
                    created_at=now,
                    updated_at=now,
                )
            )
        session.add(
            ApiToken(
                name="ci",
                token_prefix="token-examp",
                token_hash="hash-example",
                scopes="read,jobs",
                created_by_user_id=admin_id,
                last_used_at=now,
                expires_at=now + timedelta(days=7),
            )
        )
        session.add(
            ApiToken(
                name="old",
                token_prefix="token-oldex",
                token_hash="hash-revoked",
                scopes="read",
                created_by_user_id=admin_id,
                revoked_at=now,
            )
        )
        session.commit()

    class _Job:
        next_run_time = datetime.utcnow()

    class _Sched:
        def get_job(self, job_id):
            del job_id
            return _Job()

    monkeypatch.setattr(settings_mod, "_scheduler", lambda: (_Sched(), True))
    store["schedule_enabled"] = True
    store["data_cleanup_enabled"] = True
    store["oidc_role_map"] = '{"Ops": "nope", "": "admin", "admins": "admin"}'

    from app.routers.backup_copies import COPY_DRAFT_COOKIE
    from app.routers.settings import TOKEN_SECRET_COOKIE

    draft = create_access_token(
        {
            "cd": "1",
            "provider": "smb",
            "schedule_cron": "15 4 * * *",
            "after_host_backup": "1",
            "copy_herder_backup": "1",
            "host": "host-a.example.com",
            "share": "backups",
            "smb_path": "pi",
            "username": "user",
            "password": "token-example",
            "domain": "example",
            "drive_folder": "PiHerder",
            "client_id": "client-example",
            "client_secret": "token-example",
        },
        expires_delta=timedelta(minutes=5),
    )
    try:
        _as(client, engine, admin_id)
        client.cookies.set(COPY_DRAFT_COOKIE, draft)
        page = client.get(
            "/herder-backups?tab=backup&copy_provider=smb&copy_error=host&folders=onedrive",
            follow_redirects=False,
        )
        assert page.status_code == 200, page.status_code

        drive_draft = create_access_token(
            {
                "cd": "1",
                "provider": "drive",
                "schedule_cron": "0 4 * * *",
                "after_host_backup": "1",
                "copy_herder_backup": "1",
                "drive_folder": "PiHerder",
                "client_id": "client-example",
                "client_secret": "token-example",
                "host": "",
                "share": "",
                "smb_path": "",
                "username": "",
                "password": "",
                "domain": "",
            },
            expires_delta=timedelta(minutes=5),
        )
        client.cookies.set(COPY_DRAFT_COOKIE, drive_draft)
        drive_page = client.get("/herder-backups?tab=backup&copy_provider=drive&folders=drive")
        assert drive_page.status_code == 200

        od_draft = create_access_token(
            {
                "cd": "1",
                "provider": "onedrive",
                "schedule_cron": "0 5 * * *",
                "after_host_backup": "",
                "copy_herder_backup": "1",
                "drive_folder": "PiHerder",
                "client_id": "od-example",
                "client_secret": "token-example",
                "host": "",
                "share": "",
                "smb_path": "",
                "username": "",
                "password": "",
                "domain": "",
            },
            expires_delta=timedelta(minutes=5),
        )
        client.cookies.set(COPY_DRAFT_COOKIE, od_draft)
        od_page = client.get("/herder-backups?tab=backup&copy_provider=onedrive&folders=smb")
        assert od_page.status_code == 200

        flash = create_access_token(
            {"sub": str(admin_id), "ats": True, "secret": "token-example"},
            expires_delta=timedelta(minutes=5),
        )
        client.cookies.set(TOKEN_SECRET_COOKIE, flash)
        api_page = client.get("/herder-backups?tab=api&token_created=1&token_name=ci&token_status=all")
        assert api_page.status_code == 200
        revoked_page = client.get("/herder-backups?tab=api&token_status=revoked&token_rotated=1")
        assert revoked_page.status_code == 200
        mismatch = create_access_token(
            {"sub": "999999", "ats": True, "secret": "token-example"},
            expires_delta=timedelta(minutes=5),
        )
        client.cookies.set(TOKEN_SECRET_COOKIE, mismatch)
        mismatch_page = client.get("/herder-backups?tab=api&token_created=1")
        assert mismatch_page.status_code == 200
        client.cookies.set(
            TOKEN_SECRET_COOKIE,
            create_access_token({"sub": "abc", "ats": True, "secret": "token-example"}, expires_delta=timedelta(minutes=5)),
        )
        bad_flash = client.get("/herder-backups?tab=api&token_created=1")
        assert bad_flash.status_code == 200
        client.cookies.set(TOKEN_SECRET_COOKIE, "not-a-token")
        junk_flash = client.get("/herder-backups?tab=alerts&webhook_saved=1")
        assert junk_flash.status_code == 200

        store["oidc_role_map"] = "not-json"
        bad_map = client.get("/herder-backups?tab=general&security_saved=1")
        assert bad_map.status_code == 200
        store["oidc_role_map"] = []
        list_map = client.get("/herder-backups?tab=fleet&update_checks_saved=1")
        assert list_map.status_code == 200
        store["oidc_role_map"] = "{}"

        monkeypatch.setattr(
            settings_mod.stack_svc,
            "load_last_report",
            lambda: {"overall": "fail"},
        )
        status_page = client.get("/herder-backups?tab=status&stack_checked=1")
        assert status_page.status_code == 200
        monkeypatch.setattr(settings_mod.stack_svc, "load_last_report", lambda: {"overall": "warn"})
        warn_page = client.get("/herder-backups?tab=status")
        assert warn_page.status_code == 200

        monkeypatch.setattr(
            settings_mod,
            "load_repo_markdown",
            lambda name: (_ for _ in ()).throw(RuntimeError("docs")),
        )
        monkeypatch.setattr(
            settings_mod.tok_svc,
            "api_meta_dict",
            lambda: (_ for _ in ()).throw(RuntimeError("meta")),
        )
        monkeypatch.setattr(
            settings_mod.tok_svc,
            "count_api_tokens_by_status",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tokens")),
        )
        monkeypatch.setattr(
            settings_mod.sdc,
            "preview_cleanup",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("preview")),
        )
        fallback_bits = client.get("/herder-backups?tab=api&test_ok=0")
        assert fallback_bits.status_code == 200

        def _copy_boom(*args, **kwargs):
            del args, kwargs
            raise RuntimeError("copy lookup failed")

        monkeypatch.setattr(copies, "find_destination", _copy_boom)
        copy_fail = client.get("/herder-backups?tab=backup&restored=1")
        assert copy_fail.status_code == 200

        client.cookies.clear()
        _as(client, engine, op_id)
        for path in (
            "/herder-backups?tab=api",
            "/herder-backups?tab=status",
            "/herder-backups?tab=alerts",
            "/herder-backups?tab=fleet",
            "/herder-backups?tab=backup",
            "/herder-backups?tab=demo",
            "/herder-backups?tab=nope",
            "/herder-backups?webhook_test=1",
            "/herder-backups?smtp_test=1",
            "/herder-backups?demo_error=1",
        ):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 200, path

        _as(client, engine, admin_id)
        diag = {"ok": False}

        def diagnose(session, plain, client_ip=None, touch_last_used=True):
            del session, touch_last_used
            if not (plain or "").strip():
                return {"ok": False, "error": "missing_token", "detail": "No token secret provided", "client_ip": client_ip}
            if diag["ok"] and plain == "token-example":
                return {
                    "ok": True,
                    "detail": "Token accepted",
                    "scopes": ["read", "jobs"],
                    "token": {"name": "ci"},
                    "name": "ci",
                    "client_ip": client_ip,
                }
            return {"ok": False, "error": "invalid_or_revoked", "detail": "rejected", "client_ip": client_ip}

        monkeypatch.setattr(settings_mod.tok_svc, "diagnose_plaintext_token", diagnose)
        rejected = client.post(
            "/herder-backups/api-tokens/test",
            json={"token": "token-example"},
        )
        assert rejected.status_code == 401
        empty_json = client.post(
            "/herder-backups/api-tokens/test",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
        assert empty_json.status_code == 400
        form_bad = client.post(
            "/herder-backups/api-tokens/test",
            data={"token": "token-example"},
            follow_redirects=False,
        )
        assert form_bad.status_code == 303 and "test_ok=0" in _loc(form_bad)
        diag["ok"] = True
        accepted = client.post(
            "/herder-backups/api-tokens/test",
            json={"token": "token-example"},
        )
        assert accepted.status_code == 200 and accepted.json()["ok"] is True
        form_ok = client.post(
            "/herder-backups/api-tokens/test",
            data={"token": "token-example"},
            follow_redirects=False,
        )
        assert form_ok.status_code == 303 and "test_ok=1" in _loc(form_ok)

        missing_tok = client.post("/herder-backups/api-tokens/999999/revoke", follow_redirects=False)
        assert missing_tok.status_code == 303 and "token_revoked=1" in _loc(missing_tok)
        with Session(engine) as session:
            token_id = session.exec(select(ApiToken).where(ApiToken.name == "ci")).first().id
        revoked = client.post(f"/herder-backups/api-tokens/{token_id}/revoke", follow_redirects=False)
        assert revoked.status_code == 303 and "token_status=revoked" in _loc(revoked)

        real_audit = settings_mod.make_audit_log

        def audit_boom(**kwargs):
            del kwargs
            raise RuntimeError("audit down")

        monkeypatch.setattr(settings_mod, "make_audit_log", audit_boom)
        with Session(engine) as session:
            other = session.exec(select(ApiToken).where(ApiToken.name == "old")).first().id
        rolled = client.post(f"/herder-backups/api-tokens/{other}/revoke", follow_redirects=False)
        assert rolled.status_code == 303 and "token_revoked=1" in _loc(rolled)
        monkeypatch.setattr(settings_mod, "make_audit_log", real_audit)

        real_norm = sec.normalize_role

        def fake_role(role):
            if str(role or "").strip().lower() == "bogus":
                return "bogus"
            return real_norm(role)

        bad_json = client.post(
            "/herder-backups/oidc",
            data={"oidc_role_map": "{not json", "oidc_issuer": ISSUER, "oidc_client_id": "client-example"},
            follow_redirects=False,
        )
        assert bad_json.status_code == 303 and "oidc_error=" in _loc(bad_json)
        not_obj = client.post(
            "/herder-backups/oidc",
            data={"oidc_role_map": "[]", "oidc_issuer": ISSUER},
            follow_redirects=False,
        )
        assert not_obj.status_code == 303 and "oidc_error=" in _loc(not_obj)
        monkeypatch.setattr(sec, "normalize_role", fake_role)
        bad_role = client.post(
            "/herder-backups/oidc",
            data={"oidc_role_map": '{"ops": "bogus"}', "oidc_issuer": ISSUER},
            follow_redirects=False,
        )
        assert bad_role.status_code == 303 and "oidc_error=" in _loc(bad_role)
        default_role = client.post(
            "/herder-backups/oidc",
            data={
                "oidc_role_map": '{"ops": "viewer"}',
                "oidc_default_role": "bogus",
                "oidc_issuer": ISSUER,
                "oidc_client_id": "client-example",
            },
            follow_redirects=False,
        )
        assert default_role.status_code == 303 and "oidc_saved=1" in _loc(default_role)
        monkeypatch.setattr(sec, "normalize_role", real_norm)

        need_secret = client.post(
            "/herder-backups/oidc",
            data={
                "oidc_enabled": "on",
                "oidc_issuer": ISSUER,
                "oidc_client_id": "client-example",
                "oidc_client_secret": "",
                "oidc_role_map": "{}",
            },
            follow_redirects=False,
        )
        assert need_secret.status_code == 303 and "oidc_error=" in _loc(need_secret)

        monkeypatch.setattr(
            oidc,
            "set_client_secret_encrypted",
            lambda plain: (_ for _ in ()).throw(RuntimeError("encrypt")),
        )
        enc_fail = client.post(
            "/herder-backups/oidc",
            data={
                "oidc_enabled": "on",
                "oidc_issuer": ISSUER,
                "oidc_client_id": "client-example",
                "oidc_client_secret": "token-example",
                "oidc_role_map": "{}",
            },
            follow_redirects=False,
        )
        assert enc_fail.status_code == 303 and "oidc_error=" in _loc(enc_fail)
        monkeypatch.setattr(oidc, "set_client_secret_encrypted", lambda plain: "enc-" + plain)

        real_save = settings_mod.app_cfg.save_settings

        def save_boom(partial):
            raise RuntimeError("db down")

        monkeypatch.setattr(settings_mod.app_cfg, "save_settings", save_boom)
        save_fail = client.post(
            "/herder-backups/oidc",
            data={
                "oidc_issuer": ISSUER,
                "oidc_client_id": "client-example",
                "oidc_role_map": "{}",
            },
            follow_redirects=False,
        )
        assert save_fail.status_code == 303 and "oidc_error=" in _loc(save_fail)
        monkeypatch.setattr(settings_mod.app_cfg, "save_settings", real_save)

        saved = client.post(
            "/herder-backups/oidc",
            data={
                "oidc_enabled": "on",
                "oidc_display_name": "Example SSO",
                "oidc_issuer": ISSUER,
                "oidc_client_id": "client-example",
                "oidc_client_secret": "token-example",
                "oidc_role_map": '{"ops": "operator"}',
                "oidc_default_role": "viewer",
                "oidc_allowed_email_domains": "example.com",
                "oidc_sync_roles_on_login": "on",
                "oidc_auto_link_by_email": "on",
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303 and "oidc_saved=1" in _loc(saved)

        hook = {"mode": "bad"}

        def send_webhook(*args, **kwargs):
            del args, kwargs
            if hook["mode"] == "boom":
                raise RuntimeError("webhook down")
            if hook["mode"] == "bad":
                return {"ok": False, "error": "delivery failed"}
            return {"ok": True}

        monkeypatch.setattr(alert_ch, "send_webhook", send_webhook)
        bad_url = client.post(
            "/herder-backups/alerts/webhook/test",
            data={"webhook_url": "not-a-url"},
            follow_redirects=False,
        )
        assert bad_url.status_code == 303 and "alerts_error=" in _loc(bad_url)
        not_ok = client.post(
            "/herder-backups/alerts/webhook/test",
            data={"webhook_url": "https://hooks.example.com/notify", "webhook_secret": "token-example"},
            follow_redirects=False,
        )
        assert not_ok.status_code == 303 and "alerts_error=" in _loc(not_ok)
        hook["mode"] = "boom"
        blown = client.post(
            "/herder-backups/alerts/webhook/test",
            data={"webhook_url": "https://hooks.example.com/notify"},
            follow_redirects=False,
        )
        assert blown.status_code == 303 and "alerts_error=" in _loc(blown)
        hook["mode"] = "ok"
        sent = client.post(
            "/herder-backups/alerts/webhook/test",
            data={
                "webhook_url": "https://hooks.example.com/notify",
                "webhook_enabled": "on",
                "webhook_min_severity": "info",
            },
            follow_redirects=False,
        )
        assert sent.status_code == 303 and "webhook_test=1" in _loc(sent)

        mail = {"ok": False}

        def send_test_email(to):
            del to
            if mail["ok"]:
                return {"ok": True}
            return {"ok": False, "error": "send failed"}

        monkeypatch.setattr(alert_ch, "send_test_email", send_test_email)
        smtp_bad = client.post(
            "/herder-backups/alerts/smtp/test",
            data={"to": "user@example.com"},
            follow_redirects=False,
        )
        assert smtp_bad.status_code == 303 and "alerts_error=" in _loc(smtp_bad)
        mail["ok"] = True
        smtp_ok = client.post(
            "/herder-backups/alerts/smtp/test",
            data={"to": "user@example.com"},
            follow_redirects=False,
        )
        assert smtp_ok.status_code == 303 and "smtp_test=1" in _loc(smtp_ok)

        jobs = {"rows": []}

        def resolve_archive(**kwargs):
            if kwargs.get("allow_tmp_upload"):
                return resolve_archive.upload
            name = kwargs.get("name") or ""
            if name == "piherder-example.tar.gz":
                return Path("/tmp/piherder-example.tar.gz")
            return None

        resolve_archive.upload = None

        def enqueue(session, filename):
            del session, filename
            return list(jobs["rows"])

        def restore_backup(*args, **kwargs):
            del args, kwargs
            if restore_backup.fail:
                raise RuntimeError("restore failed")
            return {
                "restored_servers": 1,
                "restored_audit": 0,
                "would_restore_servers": 2,
                "would_restore_audit": 1,
            }

        restore_backup.fail = True
        monkeypatch.setattr(settings_mod.hb, "resolve_archive_in_roots", resolve_archive)
        monkeypatch.setattr(settings_mod.hb, "restore_herder_backup", restore_backup)
        monkeypatch.setattr(copies, "enqueue_after_herder_backup", enqueue)

        empty = client.post("/herder-backups/restore", data={"archive": ""}, follow_redirects=False)
        assert empty.status_code == 303 and "error=" in _loc(empty)
        missing_archive = client.post(
            "/herder-backups/restore",
            data={"archive": "/tmp/piherder-missing.tar.gz"},
            follow_redirects=False,
        )
        assert missing_archive.status_code == 303 and "error=" in _loc(missing_archive)
        uploaded = client.post(
            "/herder-backups/restore",
            data={"dry_run": "on"},
            files={"restore_file": ("notes.txt", b"not-a-tar", "application/octet-stream")},
            follow_redirects=False,
        )
        assert uploaded.status_code == 303 and "error=" in _loc(uploaded)
        restore_backup.fail = False
        resolve_archive.upload = Path("/tmp/piherder-example.tar.gz")
        preview = client.post(
            "/herder-backups/restore",
            data={"archive": "/tmp/piherder-example.tar.gz", "dry_run": "on", "restore_audit": "on"},
            follow_redirects=False,
        )
        assert preview.status_code == 303 and "restored=1" in _loc(preview)

        missing_copy = client.post(
            "/herder-backups/copy",
            data={"name": "not-an-archive"},
            follow_redirects=False,
        )
        assert missing_copy.status_code == 303 and "herder_copy=missing" in _loc(missing_copy)
        none_copy = client.post(
            "/herder-backups/copy",
            data={"name": "piherder-example.tar.gz"},
            follow_redirects=False,
        )
        assert none_copy.status_code == 303 and "herder_copy=none" in _loc(none_copy)
        jobs["rows"] = [SimpleNamespace(id=3, status="failed"), SimpleNamespace(id=4, status="failed")]
        failed_copy = client.post(
            "/herder-backups/copy",
            data={"name": "piherder-example.tar.gz"},
            follow_redirects=False,
        )
        assert failed_copy.status_code == 303 and "herder_copy=failed" in _loc(failed_copy)
        jobs["rows"] = [SimpleNamespace(id=8, status="pending"), SimpleNamespace(id=9, status="failed")]
        queued = client.post(
            "/herder-backups/copy",
            data={"name": "piherder-example.tar.gz"},
            follow_redirects=False,
        )
        assert queued.status_code == 303 and "/jobs?highlight=8" in _loc(queued)
    finally:
        _done()

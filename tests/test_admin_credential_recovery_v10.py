"""v1.0 admin credential recovery — password reset, clear 2FA, session_version."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import TotpBackupCode, User
from app.security.auth import (
    create_access_token,
    create_user_access_token,
    get_password_hash,
    user_session_version,
    verify_password,
)
from sqlmodel import select

from app.services.user_admin import (
    admin_reset_password,
    admin_sign_out_sessions,
    bump_session_version,
    clear_user_2fa,
)


# --- pure helpers -----------------------------------------------------------


def test_user_session_version_defaults():
    u = SimpleNamespace(session_version=None)
    assert user_session_version(u) == 0
    u.session_version = 3
    assert user_session_version(u) == 3


def test_create_user_access_token_embeds_sv():
    u = SimpleNamespace(id=9, session_version=4)
    token = create_user_access_token(u)
    from app.security.auth import decode_token_payload

    payload = decode_token_payload(token)
    assert payload is not None
    assert payload["sub"] == "9"
    assert payload["sv"] == 4


def test_bump_session_version_increments():
    class _S:
        def add(self, _o):
            pass

    u = User(
        id=1,
        email="a@example.com",
        hashed_password="x",
        role="operator",
        session_version=2,
    )
    v = bump_session_version(_S(), u)
    assert v == 3
    assert u.session_version == 3


def test_clear_user_2fa_wipes_flags(monkeypatch):
    deleted = []

    class _Exec:
        def all(self):
            return [SimpleNamespace(id=1)]

    class _S:
        def exec(self, _q):
            return _Exec()

        def delete(self, row):
            deleted.append(row)

        def add(self, _o):
            pass

    u = User(
        id=5,
        email="t@example.com",
        hashed_password="x",
        role="viewer",
        totp_enabled=True,
        totp_secret_encrypted="enc",
        totp_confirmed_at=None,
    )
    clear_user_2fa(_S(), u)
    assert u.totp_enabled is False
    assert u.totp_secret_encrypted is None
    # TOTP backup codes + WebAuthn passkeys (one mocked row each)
    assert len(deleted) == 2


# --- sqlite integration -----------------------------------------------------


@pytest.fixture()
def sqlite_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'cred_recovery.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def client(sqlite_engine):
    def _session():
        with Session(sqlite_engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    c = TestClient(app, raise_server_exceptions=False)
    try:
        yield c, sqlite_engine
    finally:
        app.dependency_overrides.clear()


def _mk_user(session: Session, *, email: str, role: str = "operator", **kw) -> User:
    # Default totp_enabled=True so force-2FA policy (live DB cache) does not
    # intercept admin HTTP tests with OnboardingRedirect.
    u = User(
        email=email,
        hashed_password=get_password_hash(kw.pop("password", "TempPass1ok")),
        role=role,
        is_active=True,
        must_change_password=False,
        totp_enabled=kw.pop("totp_enabled", True),
        totp_secret_encrypted=kw.pop("totp_secret_encrypted", "test-enc"),
        session_version=kw.pop("session_version", 0),
        **kw,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _cookie(user: User) -> dict[str, str]:
    return {"access_token": create_user_access_token(user)}


def test_stale_session_version_rejected(client):
    c, engine = client
    with Session(engine) as session:
        admin = _mk_user(session, email="admin@cred.test", role="admin")
        token = create_user_access_token(admin)
        admin.session_version = 5
        session.add(admin)
        session.commit()
    r = c.get("/auth/users", cookies={"access_token": token})
    assert r.status_code == 401


def test_legacy_token_without_sv_accepted_when_version_zero(client):
    """Tokens minted before sv claim still work until version is bumped."""
    c, engine = client
    with Session(engine) as session:
        admin = _mk_user(session, email="admin2@cred.test", role="admin", session_version=0)
        uid = admin.id
    legacy = create_access_token({"sub": str(uid)})  # no sv claim → treated as 0
    r = c.get("/auth/users", cookies={"access_token": legacy})
    assert r.status_code == 200, r.headers.get("location") or r.text[:200]


def test_admin_reset_password_http(client):
    c, engine = client
    with Session(engine) as session:
        admin = _mk_user(session, email="adm@cred.test", role="admin")
        target = _mk_user(
            session,
            email="op@cred.test",
            role="operator",
            password="OldPass1ok",
            totp_enabled=True,
            totp_secret_encrypted="secret-blob",
        )
        admin_id, target_id = admin.id, target.id
        old_sv = target.session_version

    new_pw = "NewTemp2ok!!"
    r = c.post(
        f"/auth/users/{target_id}/reset-password",
        data={"password": new_pw},
        cookies=_cookie_from_ids(engine, admin_id),
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text[:500]
    assert "Password reset" in r.text or "copy credentials" in r.text.lower()

    with Session(engine) as session:
        t = session.get(User, target_id)
        assert t is not None
        assert t.must_change_password is True
        assert verify_password(new_pw, t.hashed_password)
        assert t.session_version == old_sv + 1
        # password-only reset does not clear 2FA
        assert t.totp_enabled is True


def _cookie_from_ids(engine, uid: int) -> dict[str, str]:
    with Session(engine) as session:
        u = session.get(User, uid)
        assert u is not None
        return _cookie(u)


def test_admin_clear_2fa_http(client):
    c, engine = client
    with Session(engine) as session:
        admin = _mk_user(session, email="adm3@cred.test", role="admin")
        target = _mk_user(
            session,
            email="op2@cred.test",
            totp_enabled=True,
            totp_secret_encrypted="enc-secret",
        )
        session.add(
            TotpBackupCode(user_id=target.id, code_hash="hash1")
        )
        session.commit()
        admin_id, target_id = admin.id, target.id
        old_sv = target.session_version

    r = c.post(
        f"/auth/users/{target_id}/clear-2fa",
        data={"confirm": "1"},
        cookies=_cookie_from_ids(engine, admin_id),
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert "2fa_cleared" in (r.headers.get("location") or "")

    with Session(engine) as session:
        t = session.get(User, target_id)
        assert t.totp_enabled is False
        assert t.totp_secret_encrypted is None
        assert t.session_version == old_sv + 1
        codes = list(
            session.exec(
                select(TotpBackupCode).where(TotpBackupCode.user_id == target_id)
            ).all()
        )
        assert codes == []


def test_admin_reset_access_clears_2fa_and_password(client):
    c, engine = client
    with Session(engine) as session:
        admin = _mk_user(session, email="adm4@cred.test", role="admin")
        target = _mk_user(
            session,
            email="locked@cred.test",
            totp_enabled=True,
            totp_secret_encrypted="enc",
        )
        admin_id, target_id = admin.id, target.id

    pw = "RecoverMe9x"
    r = c.post(
        f"/auth/users/{target_id}/reset-access",
        data={"password": pw, "confirm": "1"},
        cookies=_cookie_from_ids(engine, admin_id),
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "Access reset" in r.text or "copy credentials" in r.text.lower()

    with Session(engine) as session:
        t = session.get(User, target_id)
        assert t.must_change_password is True
        assert t.totp_enabled is False
        assert t.totp_secret_encrypted is None
        assert verify_password(pw, t.hashed_password)
        assert t.session_version >= 1


def test_reset_access_cannot_target_self(client):
    c, engine = client
    with Session(engine) as session:
        admin = _mk_user(session, email="self@cred.test", role="admin")
        admin_id = admin.id

    r = c.post(
        f"/auth/users/{admin_id}/reset-access",
        data={"password": "SelfReset1ok", "confirm": "1"},
        cookies=_cookie_from_ids(engine, admin_id),
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert "reset_self" in (r.headers.get("location") or "")


def test_operator_forbidden_on_recovery(client):
    c, engine = client
    with Session(engine) as session:
        op = _mk_user(session, email="oponly@cred.test", role="operator")
        target = _mk_user(session, email="t@cred.test", role="viewer")
        op_id, target_id = op.id, target.id

    r = c.post(
        f"/auth/users/{target_id}/clear-2fa",
        data={"confirm": "1"},
        cookies=_cookie_from_ids(engine, op_id),
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_admin_reset_password_service_unit():
    class _S:
        def __init__(self):
            self.added = []
            self.deleted = []

        def add(self, o):
            self.added.append(o)

        def delete(self, o):
            self.deleted.append(o)

        def exec(self, _q):
            return SimpleNamespace(all=lambda: [])

        def flush(self):
            pass

        def commit(self):
            pass

    session = _S()
    u = User(
        id=3,
        email="u@example.com",
        hashed_password=get_password_hash("OldPass1ok"),
        role="operator",
        totp_enabled=True,
        totp_secret_encrypted="x",
        session_version=0,
    )
    admin_reset_password(session, u, "BrandNew2ok", clear_2fa=True)
    assert u.must_change_password is True
    assert u.totp_enabled is False
    assert u.session_version == 1
    assert verify_password("BrandNew2ok", u.hashed_password)


def test_admin_sign_out_sessions_service():
    class _S:
        def add(self, _o):
            pass

        def exec(self, _q):
            return SimpleNamespace(all=lambda: [])

        def delete(self, _o):
            pass

        def flush(self):
            pass

        def commit(self):
            pass

    u = User(
        id=4,
        email="s@example.com",
        hashed_password="x",
        role="viewer",
        session_version=7,
    )
    v = admin_sign_out_sessions(_S(), u)
    assert v == 8

"""Host-side recover-admin CLI (sole-admin lockout)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.cli import recover_admin as ra
from app.models import AuditLog, TotpBackupCode, TrustedDevice, User
from app.security.auth import get_password_hash, verify_password
from app.services.user_admin import user_session_version


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'recover_cli.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(ra, "engine", eng)
    return eng


def _user(session: Session, **kw) -> User:
    u = User(
        email=kw.pop("email", "admin@example.com"),
        hashed_password=get_password_hash(kw.pop("password", "OldPass1ok")),
        role=kw.pop("role", "admin"),
        is_active=True,
        totp_enabled=kw.pop("totp_enabled", False),
        totp_secret_encrypted=kw.pop("totp_secret_encrypted", None),
        must_change_password=False,
        session_version=kw.pop("session_version", 0),
        **kw,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def test_list_empty(engine, capsys):
    assert ra.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "No users" in out


def test_list_and_find_case_insensitive(engine):
    with Session(engine) as s:
        _user(s, email="Admin@Example.com")
    assert ra.main(["list"]) == 0
    with Session(engine) as s:
        u = ra.find_user(s, "admin@example.com")
        assert u is not None
        assert u.email == "Admin@Example.com"


def test_reset_password_generate(engine, capsys):
    with Session(engine) as s:
        u = _user(s, totp_enabled=True, totp_secret_encrypted="enc", session_version=2)
        uid = int(u.id)
    assert (
        ra.main(
            [
                "reset-password",
                "--email",
                "admin@example.com",
                "--generate",
                "--yes",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "Temporary password:" in out
    with Session(engine) as s:
        u = s.get(User, uid)
        assert u.must_change_password is True
        assert u.totp_enabled is True  # keep 2FA
        assert user_session_version(u) == 3
        logs = list(s.exec(select(AuditLog).where(AuditLog.action == "host_password_reset")))
        assert len(logs) == 1


def test_reset_access_clears_2fa(engine, capsys):
    with Session(engine) as s:
        u = _user(
            s,
            password="OldPass1ok",
            totp_enabled=True,
            totp_secret_encrypted="secret",
            session_version=1,
        )
        uid = int(u.id)
        s.add(TotpBackupCode(user_id=uid, code_hash="h1"))
        s.add(
            TrustedDevice(
                user_id=uid,
                token_hash="t1",
                expires_at=datetime.utcnow() + timedelta(days=30),
            )
        )
        s.commit()
    assert (
        ra.main(
            [
                "reset-access",
                "--email",
                "admin@example.com",
                "--password",
                "TempPass9ok",
                "--yes",
            ]
        )
        == 0
    )
    with Session(engine) as s:
        u = s.get(User, uid)
        assert verify_password("TempPass9ok", u.hashed_password)
        assert u.must_change_password is True
        assert u.totp_enabled is False
        assert u.totp_secret_encrypted is None
        assert user_session_version(u) == 2
        assert s.exec(select(TotpBackupCode).where(TotpBackupCode.user_id == uid)).all() == []
        assert s.exec(select(TrustedDevice).where(TrustedDevice.user_id == uid)).all() == []
        assert s.exec(select(AuditLog).where(AuditLog.action == "host_access_reset")).first()


def test_clear_2fa_only(engine):
    with Session(engine) as s:
        u = _user(s, totp_enabled=True, totp_secret_encrypted="x", session_version=0)
        uid = int(u.id)
    assert ra.main(["clear-2fa", "--email", "admin@example.com", "--yes"]) == 0
    with Session(engine) as s:
        u = s.get(User, uid)
        assert u.totp_enabled is False
        assert verify_password("OldPass1ok", u.hashed_password)
        assert user_session_version(u) == 1


def test_sign_out(engine):
    with Session(engine) as s:
        u = _user(s, session_version=5)
        uid = int(u.id)
    assert ra.main(["sign-out", "--email", "admin@example.com", "--yes"]) == 0
    with Session(engine) as s:
        u = s.get(User, uid)
        assert user_session_version(u) == 6


def test_delete_last_user_reopens_register_path(engine, capsys):
    with Session(engine) as s:
        _user(s)
    assert ra.main(["delete-user", "--email", "admin@example.com", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "no users" in out.lower() or "Register" in out
    with Session(engine) as s:
        assert list(s.exec(select(User)).all()) == []


def test_weak_password_rejected(engine):
    with Session(engine) as s:
        _user(s)
    with pytest.raises(SystemExit):
        ra.main(
            [
                "reset-password",
                "--email",
                "admin@example.com",
                "--password",
                "short",
                "--yes",
            ]
        )


def test_missing_user(engine):
    with pytest.raises(SystemExit):
        ra.main(["clear-2fa", "--email", "nobody@example.com", "--yes"])


def test_requires_yes_noninteractive(engine, monkeypatch):
    with Session(engine) as s:
        _user(s)
    monkeypatch.setattr(ra.sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as ei:
        ra.main(["sign-out", "--email", "admin@example.com"])
    assert "--yes" in str(ei.value)


def test_resolve_password_generate():
    p = ra.resolve_password(password=None, generate=True, prompt=False)
    assert len(p) >= 10

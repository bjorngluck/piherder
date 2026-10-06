"""v1.11 — a passkey counts as 2FA when Force 2FA is on."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import User, WebAuthnCredential
from app.security.auth import create_user_access_token, get_password_hash


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'passkey-2fa.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    test_client = TestClient(app, raise_server_exceptions=False)
    try:
        yield test_client, engine
    finally:
        app.dependency_overrides.clear()


def _user(engine, *, email: str) -> User:
    with Session(engine) as session:
        user = User(
            email=email,
            hashed_password=get_password_hash("SmokeTest1ok!"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def _passkey(engine, user_id: int) -> None:
    with Session(engine) as session:
        session.add(
            WebAuthnCredential(
                user_id=user_id,
                credential_id=f"cred-{user_id}",
                public_key="cHVi",
                sign_count=0,
            )
        )
        session.commit()


def _login(client: TestClient, user: User) -> None:
    client.cookies.set("access_token", create_user_access_token(user))


def test_force_2fa_page_offers_a_passkey(client, monkeypatch):
    test_client, engine = client
    monkeypatch.setattr(
        "app.services.account_stepup.force_2fa_applies", lambda *a, **k: True
    )
    user = _user(engine, email="enroll@test.local")
    _login(test_client, user)
    response = test_client.get("/auth/force-2fa")
    assert response.status_code == 200
    assert 'data-testid="force-2fa-passkey"' in response.text
    assert 'href="/auth/account#account-passkeys"' in response.text
    assert 'href="/auth/account#account-2fa"' in response.text
    assert "You do not need both" in response.text


def test_passkey_lifts_the_enroll_wall(client, monkeypatch):
    test_client, engine = client
    monkeypatch.setattr(
        "app.services.account_stepup.force_2fa_applies", lambda *a, **k: True
    )
    user = _user(engine, email="has-key@test.local")
    _passkey(engine, int(user.id))
    _login(test_client, user)
    response = test_client.get("/auth/force-2fa", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    account = test_client.get("/auth/account")
    assert account.status_code == 200
    assert "a passkey is this account's 2FA" in account.text
    assert 'id="account-devices"' in account.text
    users = test_client.get("/auth/users")
    assert users.status_code == 200
    assert 'data-testid="user-2fa">2fa' in users.text
    assert 'data-testid="user-2fa">no 2fa' not in users.text
    assert "2FA already off" not in users.text

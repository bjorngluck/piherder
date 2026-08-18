"""Logout must revoke JWTs (session_version) and parked consoles."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from app.database import get_session
from app.main import app
from app.models import User
from app.security.auth import create_user_access_token, get_password_hash, user_session_version


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'logout.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    c = TestClient(app, raise_server_exceptions=False)
    try:
        yield c, engine
    finally:
        app.dependency_overrides.clear()


def test_logout_bumps_session_version_old_jwt_rejected(client, monkeypatch):
    c, engine = client
    discarded = []
    monkeypatch.setattr(
        "app.services.ssh_console.discard_all_parked_for_user",
        lambda uid: discarded.append(int(uid)) or 0,
    )
    with Session(engine) as session:
        u = User(
            email="out@example.com",
            hashed_password=get_password_hash("LogoutTest1ok"),
            role="operator",
            is_active=True,
            must_change_password=False,
        )
        session.add(u)
        session.commit()
        session.refresh(u)
        token = create_user_access_token(u)
        uid = int(u.id)
        assert user_session_version(u) == 0

    r = c.get("/auth/logout", cookies={"access_token": token}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert discarded == [uid]

    with Session(engine) as session:
        u2 = session.get(User, uid)
        assert user_session_version(u2) == 1

    r2 = c.get("/servers", cookies={"access_token": token}, follow_redirects=False)
    assert r2.status_code in (401, 303, 302)
    # Must not still be a valid fleet page
    if r2.status_code == 200:
        pytest.fail("old JWT still accepted after logout")

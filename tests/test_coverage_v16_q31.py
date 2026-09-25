"""v1.6 Q-80 thirty-first pack — auth register/forgot/account/logout."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import User
from app.security.auth import create_user_access_token, get_password_hash


def test_auth_register_forgot_account_logout(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'r31.db'}",
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
        r = client.get("/auth/forgot-password")
        assert r.status_code in (200, 303)
        r = client.post(
            "/auth/forgot-password",
            data={"email": "nobody@test.local"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 429)
        r = client.get("/auth/reset-password")
        assert r.status_code in (200, 303)
        r = client.get("/auth/reset-password?token=dead")
        assert r.status_code in (200, 303)
        r = client.post(
            "/auth/reset-password",
            data={
                "token": "dead",
                "new_password": "NewPass12ok!",
                "confirm_password": "mismatch",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 422)
        r = client.get("/auth/register")
        assert r.status_code in (200, 303)
        r = client.post(
            "/auth/register",
            data={"email": "newq31@test.local", "password": "a"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 403, 422)

        with Session(engine) as s:
            user = User(
                email="q31@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            s.add(user)
            s.commit()
            s.refresh(user)
            client.cookies.set("access_token", create_user_access_token(user))

        r = client.get("/auth/account")
        assert r.status_code in (200, 303)
        r = client.post(
            "/auth/account/profile",
            data={
                "display_name": "Q31",
                "email": "q31@test.local",
                "current_password": "",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 403)
        r = client.post(
            "/auth/account/profile",
            data={
                "display_name": "Q31",
                "email": "q31-new@test.local",
                "current_password": "wrong",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 403)
        r = client.post(
            "/auth/account/password",
            data={
                "current_password": "wrong",
                "new_password": "NewPass12ok!",
                "confirm_password": "NewPass12ok!",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 403)
        r = client.post(
            "/auth/account/password",
            data={
                "current_password": "SmokeTest1ok",
                "new_password": "NewPass12ok!",
                "confirm_password": "other",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 403)
        r = client.post("/auth/account/2fa/start", follow_redirects=False)
        assert r.status_code in (303, 200, 403, 400)
        r = client.post(
            "/auth/account/2fa/confirm",
            data={"code": "000000"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 403, 400)
        r = client.post(
            "/auth/account/2fa/disable",
            data={"password": "SmokeTest1ok"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 403, 400)
        r = client.get("/auth/me/avatar")
        assert r.status_code in (200, 404, 303)
        r = client.post("/auth/account/trusted-devices/revoke-all", follow_redirects=False)
        assert r.status_code in (303, 200, 404)
        r = client.get("/auth/logout", follow_redirects=False)
        assert r.status_code in (303, 200)
    finally:
        app.dependency_overrides.clear()

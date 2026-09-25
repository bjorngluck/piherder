"""v1.6 Q-80 thirty-fourth pack — certificates HTML + 404 POSTs."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import User
from app.security.auth import create_user_access_token, get_password_hash


def test_certificates_pages_and_missing_posts(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'r34.db'}",
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
                email="q34@test.local",
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

        r = client.get("/certificates")
        assert r.status_code == 200
        r = client.get("/certificates/setup")
        assert r.status_code in (200, 303)
        r = client.get("/certificates/upload")
        assert r.status_code in (200, 303)
        r = client.get("/certificates/99999")
        assert r.status_code in (404, 200, 303)
        r = client.post(
            "/certificates/99999/settings",
            data={"name": "x"},
            follow_redirects=False,
        )
        assert r.status_code in (404, 303, 403, 422)
        r = client.post("/certificates/99999/deploy", follow_redirects=False)
        assert r.status_code in (404, 303, 403)
        r = client.post("/certificates/99999/renew", follow_redirects=False)
        assert r.status_code in (404, 303, 403)
        r = client.post("/certificates/99999/delete", follow_redirects=False)
        assert r.status_code in (404, 303, 403)
        r = client.post(
            "/certificates/99999/targets/simulate",
            follow_redirects=False,
        )
        assert r.status_code in (404, 303, 403, 422)
        r = client.post(
            "/certificates/upload",
            data={"name": "q34"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 400, 403, 422, 200)
    finally:
        app.dependency_overrides.clear()

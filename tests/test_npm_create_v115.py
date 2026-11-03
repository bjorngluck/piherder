"""NPM integration create returns success after the row is saved."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import User
from app.security.auth import create_user_access_token, get_password_hash
from app.services.integrations.npm import NpmPollResult


def test_npm_create_returns_success(tmp_path, monkeypatch):
    import app.routers.integrations_npm as npm_routes

    engine = create_engine(
        f"sqlite:///{tmp_path / 'npm.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    calls = []

    def _poll(*args, **kwargs):
        del args, kwargs
        return NpmPollResult(ok=True, version="2.12.0")

    def _poll_integration(integration_id, notify=True):
        calls.append((integration_id, notify))
        return {"ok": True}

    monkeypatch.setattr(npm_routes.npm_mod, "poll", _poll)
    monkeypatch.setattr(npm_routes.poll_svc, "poll_integration", _poll_integration)
    try:
        with Session(engine) as session:
            user = User(
                email="user@example.com",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            token = create_user_access_token(user)
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("access_token", token)
        response = client.post(
            "/integrations/new/npm",
            data={
                "name": "Nginx Proxy Manager",
                "base_url": "https://npm.example.com",
                "identity": "user@example.com",
                "password": "SmokeTest1ok",
                "poll_interval_sec": "300",
                "tls_verify": "on",
                "enabled": "on",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers.get("location") or ""
        assert "msg=created" in location
        assert calls and calls[0][1] is False
        assert "/integrations/" in location
    finally:
        app.dependency_overrides.pop(get_session, None)

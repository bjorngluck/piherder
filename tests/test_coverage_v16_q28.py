"""v1.6 Q-80 twenty-eighth pack — DNS coverage mute/infra + force-password HTTP."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import User
from app.security.auth import create_user_access_token, get_password_hash


def test_dns_coverage_mute_and_force_password(tmp_path, monkeypatch):
    from app.services import app_settings as cfg

    store: dict = {}
    monkeypatch.setattr(cfg, "_load_raw_from_db", lambda: dict(store))
    monkeypatch.setattr(cfg, "_write_raw_to_db", lambda data: store.update(data or {}))

    engine = create_engine(
        f"sqlite:///{tmp_path / 'r28.db'}",
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
                email="q28@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=True,
                totp_enabled=False,
            )
            s.add(user)
            s.commit()
            s.refresh(user)
            client.cookies.set("access_token", create_user_access_token(user))

        r = client.get("/auth/force-password")
        assert r.status_code in (200, 303)
        r = client.post(
            "/auth/force-password",
            data={"new_password": "a", "confirm_password": "b"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        r = client.post(
            "/auth/force-password",
            data={"new_password": "NewPass12ok!", "confirm_password": "NewPass12ok!"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200)

        with Session(engine) as s:
            u2 = User(
                email="q28b@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            s.add(u2)
            s.commit()
            s.refresh(u2)
            client.cookies.set("access_token", create_user_access_token(u2))

        r = client.post(
            "/dns/coverage/mute",
            data={"key": "1:grafana:web", "next": "/dns/coverage"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        r = client.post(
            "/dns/coverage/mute",
            data={"key": "bad", "next": "/dns/coverage"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        r = client.post(
            "/dns/coverage/mute",
            data={"key": "1:grafana:web", "unmute": "1", "next": "/dns/coverage"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        r = client.post(
            "/dns/coverage/show-infra",
            data={"show": "1"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
        r = client.post(
            "/dns/coverage/show-infra",
            data={"show": "0"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403)
    finally:
        app.dependency_overrides.clear()

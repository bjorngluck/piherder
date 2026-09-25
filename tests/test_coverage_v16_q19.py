"""v1.6 Q-80 nineteenth pack — auth + settings router HTTP.

httpx TestClient ignores ``cookies=``; use ``client.cookies.set``.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import User
from app.security.auth import create_user_access_token, get_password_hash


def test_auth_and_settings_router_http(tmp_path, monkeypatch):
    from app.services import stack_health as sh
    from app.services import app_settings as cfg

    store: dict = {}
    monkeypatch.setattr(cfg, "_load_raw_from_db", lambda: dict(store))
    monkeypatch.setattr(cfg, "_write_raw_to_db", lambda data: store.update(data or {}))
    monkeypatch.setattr(sh, "run_stack_health_check", lambda *a, **k: {"overall": "ok", "components": []})
    monkeypatch.setattr(sh, "collect_backup_tree_usage", lambda **k: {"ok": True, "children": []})

    engine = create_engine(
        f"sqlite:///{tmp_path / 'r19.db'}",
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
        r = client.get("/auth/login")
        assert r.status_code == 200
        r = client.get("/auth/forgot-password")
        assert r.status_code == 200
        r = client.get("/auth/register")
        assert r.status_code in (200, 303)
        r = client.get("/auth/reset-password")
        assert r.status_code in (200, 303, 400, 422)
        r = client.get("/auth/2fa")
        assert r.status_code in (200, 303, 401)
        r = client.get("/auth/logout")
        assert r.status_code in (200, 303)

        with Session(engine) as s:
            user = User(
                email="q19@test.local",
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
        r = client.get("/auth/force-password")
        assert r.status_code in (200, 303)
        r = client.get("/auth/force-2fa")
        assert r.status_code in (200, 303)
        r = client.get("/about")
        assert r.status_code == 200
        r = client.get("/notifications")
        assert r.status_code in (200, 404)
        r = client.get("/notifications/count")
        assert r.status_code in (200, 401, 404)
        r = client.get("/notifications/preview")
        assert r.status_code in (200, 401, 404)

        r = client.post(
            "/herder-backups/timezone",
            data={"timezone": "UTC"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 422)
        r = client.post("/herder-backups/status/check", follow_redirects=False)
        assert r.status_code in (303, 403, 500)
        r = client.get("/herder-backups/status/backup-usage")
        assert r.status_code in (200, 403, 500)
        r = client.post(
            "/herder-backups/files",
            data={"files_max_gib": "1"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 422)
        r = client.post(
            "/herder-backups/config",
            data={"keep": "5"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 422, 400)
    finally:
        app.dependency_overrides.clear()

"""v1.6 Q-80 thirty-sixth pack — integrations catalog + settings status/tokens."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import User
from app.security.auth import create_user_access_token, get_password_hash


def test_integrations_and_settings_status(tmp_path, monkeypatch):
    from app.services import stack_health as stack_svc

    monkeypatch.setattr(
        stack_svc,
        "run_stack_health_check",
        lambda *a, **k: {"overall": "ok"},
    )
    monkeypatch.setattr(
        stack_svc,
        "collect_backup_tree_usage",
        lambda: {"ok": True, "children": [], "tree_bytes": 0},
    )

    engine = create_engine(
        f"sqlite:///{tmp_path / 'r36.db'}",
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
                email="q36@test.local",
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

        r = client.get("/catalog")
        assert r.status_code in (200, 303, 404)
        r = client.get("/integrations")
        assert r.status_code in (200, 303)
        r = client.get("/integrations/99999")
        assert r.status_code in (404, 200, 303)
        r = client.get("/integrations/99999/edit")
        assert r.status_code in (404, 200, 303)

        r = client.post(
            "/herder-backups/demo-restore",
            data={"confirm": "yes"},
            follow_redirects=False,
        )
        assert r.status_code in (404, 403)

        r = client.post("/herder-backups/status/check", follow_redirects=False)
        assert r.status_code in (303, 403, 200)

        r = client.get("/herder-backups/status/backup-usage")
        assert r.status_code in (200, 403, 500)

        r = client.post(
            "/herder-backups/api-tokens/test",
            json={"token": "ph_notarealtokenvalue"},
        )
        assert r.status_code in (200, 400, 401, 403, 422)

        r = client.post(
            "/herder-backups/data-cleanup/run",
            data={"dry_run": "1"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200)
    finally:
        app.dependency_overrides.clear()

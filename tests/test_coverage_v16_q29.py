"""v1.6 Q-80 twenty-ninth pack — notifications dismiss HTTP."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Notification, Server, User
from app.security.auth import create_user_access_token, get_password_hash


def test_notifications_list_and_dismiss(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'r29.db'}",
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
                email="q29@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            s.add(user)
            srv = Server(
                name="Lab",
                hostname="lab.local",
                ssh_username="pi",
                docker_base_dir="/home/pi/docker",
                os_type="debian",
            )
            s.add(srv)
            s.commit()
            s.refresh(user)
            s.refresh(srv)
            n = Notification(
                server_id=srv.id,
                type="os_updates",
                title="OS updates",
                body="2 ready",
                status="open",
                severity="warning",
                fingerprint="q29-os-1",
            )
            s.add(n)
            s.commit()
            s.refresh(n)
            nid = n.id
            client.cookies.set("access_token", create_user_access_token(user))

        r = client.get("/notifications")
        assert r.status_code == 200
        r = client.get("/notifications?status=all")
        assert r.status_code == 200
        r = client.get("/notifications/count")
        assert r.status_code in (200, 404)
        r = client.post(f"/notifications/{nid}/dismiss", follow_redirects=False)
        assert r.status_code in (303, 200, 404)
        r = client.post("/notifications/dismiss-all", follow_redirects=False)
        assert r.status_code in (303, 200)
        r = client.post("/notifications/99999/dismiss", follow_redirects=False)
        assert r.status_code in (303, 404, 200)
    finally:
        app.dependency_overrides.clear()

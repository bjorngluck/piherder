"""v1.6 Q-80 thirty-seventh pack — about, favourites, metrics."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Server, User
from app.security.auth import create_user_access_token, get_password_hash


def test_about_favourites_metrics(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'r37.db'}",
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
                email="q37@test.local",
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
            sid = srv.id
            client.cookies.set("access_token", create_user_access_token(user))

        r = client.get("/about")
        assert r.status_code in (200, 303)
        r = client.get("/about?check=1")
        assert r.status_code in (200, 303)
        r = client.post("/about/check-update", follow_redirects=False)
        assert r.status_code in (303, 200, 403)

        r = client.get("/account/favourites.json")
        assert r.status_code in (200, 403)
        r = client.post(
            "/account/favourites/toggle",
            data={"kind": "app_page", "page": "hosts_map", "next": "/dns/physical"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 400)
        r = client.post(
            "/account/favourites/toggle",
            data={"kind": "server_feature", "server_id": str(sid), "feature": "docker"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 400)
        r = client.post(
            "/account/favourites/toggle",
            data={"kind": "integration", "integration_id": "0"},
            follow_redirects=False,
        )
        assert r.status_code in (400, 303, 422)
        r = client.post(
            "/account/favourites/99999/remove",
            data={"next": "/"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 404, 200)

        r = client.get("/metrics")
        assert r.status_code in (200, 401, 403)
    finally:
        app.dependency_overrides.clear()

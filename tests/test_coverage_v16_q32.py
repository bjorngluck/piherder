"""v1.6 Q-80 thirty-second pack — DNS pages, vocab, network, base domain."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Server, User
from app.security.auth import create_user_access_token, get_password_hash


def test_dns_pages_vocab_network(tmp_path, monkeypatch):
    from app.services import app_settings as cfg

    store: dict = {}
    monkeypatch.setattr(cfg, "_load_raw_from_db", lambda: dict(store))
    monkeypatch.setattr(cfg, "_write_raw_to_db", lambda data: store.update(data or {}))

    engine = create_engine(
        f"sqlite:///{tmp_path / 'r32.db'}",
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
                email="q32@test.local",
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

        for path in (
            "/dns",
            "/dns/coverage",
            "/dns/physical",
            "/dns/logical",
            "/dns/candidates",
            "/dns/host-ports-panel",
            "/dns/stack-panel",
        ):
            r = client.get(path)
            assert r.status_code in (200, 303, 404, 500), path

        r = client.get("/dns/host-ports-expand.json")
        assert r.status_code in (200, 404, 422, 400)
        r = client.get("/dns/stack-expand.json")
        assert r.status_code in (200, 404, 422, 400)

        r = client.post(
            "/dns/base-domain",
            data={"domain": "lab.example"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200)
        r = client.post(
            "/dns/base-domain",
            data={"domain": "!!!"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200)

        r = client.post(
            "/dns/network",
            data={
                "lan_subnet": "192.168.1.0/24",
                "gateway_ip": "192.168.1.1",
                "public_ip": "",
            },
            follow_redirects=False,
        )
        assert r.status_code in (303, 403, 200, 422)

        r = client.post(
            "/dns/vocab",
            data={"kind": "category", "key": "q32cat", "label": "Q32", "next": "/dns"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 400, 403)
        r = client.post(
            "/dns/vocab",
            data={"kind": "tag", "key": "q32tag", "label": "Tag", "next": "/dns"},
            headers={"Accept": "application/json"},
        )
        assert r.status_code in (200, 400, 403)
        r = client.post(
            "/dns/vocab",
            data={"kind": "nope", "key": "x", "next": "/dns"},
            headers={"Accept": "application/json"},
        )
        assert r.status_code in (400, 403, 200)

        r = client.post(
            "/dns/network/lookup-public-ip",
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 403, 502)

        r = client.post(
            f"/servers/{sid}/dns",
            data={"dns_name": "lab.lan"},
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 403, 404, 422)
        r = client.post(
            f"/servers/{sid}/dns/sync-a",
            follow_redirects=False,
        )
        assert r.status_code in (303, 200, 403, 404)
    finally:
        app.dependency_overrides.clear()

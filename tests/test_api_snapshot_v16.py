"""v1.6 Slice 1b — Docker inventory and fleet service snapshots (no SSH)."""
from __future__ import annotations

import json
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Server, User
from app.security.auth import get_password_hash
from app.services import api_tokens as tok_svc
from app.services.docker_inventory import snapshot_containers


def test_snapshot_containers_skips_placeholders():
    server = Server(
        name="Lab",
        hostname="lab.local",
        ssh_username="pi",
        docker_inventory_json=json.dumps(
            {
                "v": 2,
                "projects": [
                    {
                        "name": "web",
                        "containers": [
                            {
                                "name": "web",
                                "running": True,
                                "state": "running",
                                "image": "nginx:1",
                                "status": "Up 2 hours",
                                "compose_service": "web",
                            },
                            {"name": "gone", "placeholder": True},
                        ],
                    }
                ],
                "orphan_containers": [
                    {"name": "orphan", "running": False, "state": "exited", "image": "redis:7", "status": "Exited"}
                ],
            }
        ),
    )
    rows = snapshot_containers(server)
    assert [r["name"] for r in rows] == ["web", "orphan"]
    assert rows[0]["running"] is True
    assert rows[0]["status"] == "Up 2 hours"
    assert rows[0]["project"] == "web"
    assert snapshot_containers(Server(name="x", hostname="h", ssh_username="pi")) == []


def test_inventory_and_services_http(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'snap.db'}",
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
                email="snap@test.local",
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
                os_type="debian",
                os_pretty="Debian GNU/Linux 12",
                hardware="Raspberry Pi 5",
                disk_total_bytes=1000,
                disk_used_bytes=250,
                docker_inventory_status="ok",
                docker_inventory_at=datetime(2026, 9, 21, 12, 0, 0),
                docker_inventory_json=json.dumps(
                    {
                        "v": 2,
                        "projects": [
                            {
                                "name": "web",
                                "containers": [
                                    {
                                        "name": "web",
                                        "running": True,
                                        "image": "nginx:1",
                                        "status": "Up 2 hours",
                                    }
                                ],
                            }
                        ],
                        "orphan_containers": [],
                    }
                ),
            )
            s.add(srv)
            s.commit()
            s.refresh(user)
            s.refresh(srv)
            sid = srv.id
            _row, plain = tok_svc.create_api_token(
                s, name="snap", created_by=user, scopes=["read"]
            )
        auth = {"Authorization": f"Bearer {plain}"}
        r = client.get("/api/v1/inventory", headers=auth)
        assert r.status_code == 200
        body = r.json()
        assert body["container_count"] == 1
        assert body["hosts"][0]["containers"][0]["name"] == "web"
        assert body["hosts"][0]["disk_used_bytes"] == 250
        assert body["hosts"][0]["os_pretty"] == "Debian GNU/Linux 12"
        r = client.get(f"/api/v1/servers/{sid}/inventory", headers=auth)
        assert r.status_code == 200
        assert r.json()["container_count"] == 1
        r = client.get("/api/v1/servers/99999/inventory", headers=auth)
        assert r.status_code == 404
        r = client.get("/api/v1/services", headers=auth)
        assert r.status_code == 200
        assert r.json()["count"] == 0
        assert r.json()["services"] == []
    finally:
        app.dependency_overrides.clear()

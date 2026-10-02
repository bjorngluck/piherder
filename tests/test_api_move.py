"""Token Move route. Not the jobs allowlist and not an MCP tool."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import ApiToken, Server, User
from app.security.auth import get_password_hash
from app.security.encryption import encrypt_str
from app.services import api_tokens as tok
from app.services.mcp_hosted import MCP_JOB_TYPES


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'move.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _client(engine):
    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return TestClient(app, raise_server_exceptions=False)


def _server(name: str, docker: bool = True) -> Server:
    return Server(
        name=name,
        hostname=f"{name}.local",
        ip_address="10.0.0.20",
        ssh_username="pi",
        ssh_port=22,
        ssh_password_encrypted=encrypt_str("x"),
        backup_enabled=True,
        os_patch_enabled=True,
        container_patch_enabled=docker,
    )


def _seed(engine, scopes: str):
    with Session(engine) as session:
        user = User(
            email="move@test.local",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        source = _server("source")
        dest = _server("dest")
        plain = "ph_movetokenvalue000000000000000000"
        token = ApiToken(
            name="ha",
            token_prefix=plain[:12],
            token_hash=tok.hash_token(plain),
            scopes=scopes,
            created_by_user_id=user.id,
        )
        session.add(source)
        session.add(dest)
        session.add(token)
        session.commit()
        session.refresh(source)
        session.refresh(dest)
        return {"plain": plain, "source": source.id, "dest": dest.id}


def _job():
    return SimpleNamespace(
        id=44,
        server_id=1,
        job_type="service_migrate",
        status="pending",
        details=None,
        created_at=None,
        started_at=None,
        finished_at=None,
        worker_hostname=None,
    )


def test_move_stays_off_the_job_and_mcp_allowlists():
    assert "service_migrate" not in tok.JOB_FEATURE_KEY
    assert "service_migrate" not in MCP_JOB_TYPES
    assert "service_migrate_undo" not in tok.JOB_FEATURE_KEY
    paths = [row["path"] for row in tok.api_meta_dict()["endpoints"]]
    assert "/api/v1/servers/{id}/moves" in paths


def test_move_requires_confirm_jobs_and_docker(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    client = _client(engine)
    ids = _seed(engine, "read,jobs,feature:docker")
    captured: dict = {}

    def fake(source_id, dest_id, project, **kwargs):
        captured["args"] = (source_id, dest_id, project)
        captured["kwargs"] = kwargs
        return _job()

    monkeypatch.setattr("app.routers.api_v1.job_service.enqueue_service_migrate", fake)
    headers = {"Authorization": f"Bearer {ids['plain']}"}
    url = f"/api/v1/servers/{ids['source']}/moves"
    body = {"dest_server_id": ids["dest"], "project": "web", "confirm": True}
    try:
        missing = client.post(url, headers=headers, json={**body, "confirm": False})
        assert missing.status_code == 400
        assert captured == {}

        path = client.post(url, headers=headers, json={**body, "project": "/opt/web"})
        assert path.status_code == 400

        ok = client.post(url, headers=headers, json={**body, "leftover": "remove"})
        assert ok.status_code == 202
        payload = ok.json()
        assert payload["job_type"] == "service_migrate"
        assert payload["leftover"] == "stopped"
        assert captured["args"] == (ids["source"], ids["dest"], "web")
        assert captured["kwargs"]["leftover"] == "stopped"
        assert captured["kwargs"]["api_token_name"] == "ha"
        assert "undo" not in captured["kwargs"]

        jobs = client.post(
            f"/api/v1/servers/{ids['source']}/jobs",
            headers=headers,
            json={"job_type": "service_migrate", "source_filter": "web"},
        )
        assert jobs.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_move_hides_when_the_flag_is_off(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    client = _client(engine)
    ids = _seed(engine, "read,jobs")
    monkeypatch.setattr(
        "app.services.service_migrate.host_lock.migrate_surface_allowed",
        lambda: False,
    )
    headers = {"Authorization": f"Bearer {ids['plain']}"}
    try:
        health = client.get("/api/v1/health", headers=headers)
        assert health.status_code == 200
        assert health.json()["service_migrate"] is False
        moved = client.post(
            f"/api/v1/servers/{ids['source']}/moves",
            headers=headers,
            json={"dest_server_id": ids["dest"], "project": "web", "confirm": True},
        )
        assert moved.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_move_rejects_a_token_without_docker(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    client = _client(engine)
    ids = _seed(engine, "read,jobs,feature:os")
    monkeypatch.setattr(
        "app.routers.api_v1.job_service.enqueue_service_migrate",
        lambda *a, **k: _job(),
    )
    headers = {"Authorization": f"Bearer {ids['plain']}"}
    try:
        moved = client.post(
            f"/api/v1/servers/{ids['source']}/moves",
            headers=headers,
            json={"dest_server_id": ids["dest"], "project": "web", "confirm": True},
        )
        assert moved.status_code == 403
    finally:
        app.dependency_overrides.clear()

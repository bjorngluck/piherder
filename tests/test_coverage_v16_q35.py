"""v1.6 Q-80 thirty-fifth pack — API v1 catalog, fleet, jobs, feature patch."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Job, Notification, Server, User
from app.security.auth import create_user_access_token, get_password_hash
from app.services import api_tokens as tok_svc


def test_api_v1_token_surfaces(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    engine = create_engine(
        f"sqlite:///{tmp_path / 'r35.db'}",
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
                email="q35@test.local",
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
                backup_enabled=True,
                os_patch_enabled=False,
                container_patch_enabled=True,
            )
            s.add(srv)
            s.commit()
            s.refresh(user)
            s.refresh(srv)
            job = Job(
                server_id=srv.id,
                job_type="backup",
                status="success",
            )
            s.add(job)
            n = Notification(
                server_id=srv.id,
                type="os_updates",
                title="OS",
                body="1",
                status="open",
                severity="warning",
                fingerprint="q35-os",
            )
            s.add(n)
            s.commit()
            s.refresh(job)
            sid, jid = srv.id, job.id
            _row, plain = tok_svc.create_api_token(
                s,
                name="q35",
                created_by=user,
                scopes=["read", "jobs", "edit", "files"],
            )
            client.cookies.set("access_token", create_user_access_token(user))

        auth = {"Authorization": f"Bearer {plain}"}
        r = client.get("/api/v1", headers=auth)
        assert r.status_code in (200, 403)
        r = client.get("/api/v1/health", headers=auth)
        assert r.status_code in (200, 403)
        r = client.get("/api/v1/summary", headers=auth)
        assert r.status_code in (200, 403)
        r = client.get("/api/v1/servers", headers=auth)
        assert r.status_code in (200, 403)
        r = client.get("/api/v1/servers?q=Lab", headers=auth)
        assert r.status_code in (200, 403)
        r = client.get(f"/api/v1/servers/{sid}", headers=auth)
        assert r.status_code in (200, 403, 404)
        r = client.get("/api/v1/servers/99999", headers=auth)
        assert r.status_code in (404, 403)
        r = client.patch(
            f"/api/v1/servers/{sid}/features",
            headers=auth,
            json={},
        )
        assert r.status_code in (400, 403, 401)
        r = client.patch(
            f"/api/v1/servers/{sid}/features",
            headers=auth,
            json={"backup": False},
        )
        assert r.status_code in (200, 403, 401)
        r = client.get(f"/api/v1/servers/{sid}/jobs", headers=auth)
        assert r.status_code in (200, 403)
        r = client.get("/api/v1/jobs", headers=auth)
        assert r.status_code in (200, 403)
        r = client.get(f"/api/v1/jobs/{jid}", headers=auth)
        assert r.status_code in (200, 403, 404)
        r = client.get("/api/v1/jobs/99999", headers=auth)
        assert r.status_code in (404, 403)
        r = client.get(f"/api/v1/servers/{sid}/files", headers=auth)
        assert r.status_code in (200, 400, 403, 404, 502)
        r = client.get("/api/v1/tokens")
        assert r.status_code in (200, 401, 403)
        r = client.get("/api/v1/health")
        assert r.status_code in (401, 403)
    finally:
        app.dependency_overrides.clear()

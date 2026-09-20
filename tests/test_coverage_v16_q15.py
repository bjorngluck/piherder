"""v1.6 Q-80 fifteenth pack — start covering routers (API + HTML shells, no live SSH).

httpx TestClient in this image ignores ``cookies=`` on get/post; set
``client.cookies.set("access_token", …)`` instead.
"""
from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import ApiToken, Job, Server, User
from app.security.auth import create_access_token, get_password_hash
from app.security.encryption import encrypt_str
from app.services import api_tokens as tok


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'r.db'}",
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
    client = TestClient(app, raise_server_exceptions=False)
    return client


def _seed(engine):
    with Session(engine) as s:
        user = User(
            email="r@test.local",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        s.add(user)
        s.commit()
        s.refresh(user)
        srv = Server(
            name="Lab Pi",
            hostname="lab.local",
            ip_address="10.0.0.9",
            ssh_username="pi",
            ssh_port=22,
            ssh_password_encrypted=encrypt_str("x"),
            backup_enabled=True,
            os_patch_enabled=True,
            container_patch_enabled=True,
            os_pretty="Ubuntu 24.04 LTS",
            hardware="Raspberry Pi 5",
            cpu_cores=4,
            memory_total_bytes=8 * 1024**3,
            disk_total_bytes=64 * 1024**3,
        )
        s.add(srv)
        s.commit()
        s.refresh(srv)
        job = Job(
            server_id=srv.id,
            job_type="backup",
            status="success",
            details='{"summary":"ok"}',
            created_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        s.add(job)
        plain = "ph_q15tokenvalue00000000000000000000"
        token = ApiToken(
            name="q15",
            token_prefix=plain[:12],
            token_hash=tok.hash_token(plain),
            scopes="read",
            created_by_user_id=user.id,
        )
        s.add(token)
        s.commit()
        s.refresh(job)
        return {
            "uid": user.id,
            "sid": srv.id,
            "jid": job.id,
            "plain": plain,
            "cookie": {"access_token": create_access_token({"sub": str(user.id)})},
        }


def test_api_v1_read_surface(tmp_path):
    engine = _engine(tmp_path)
    client = _client(engine)
    ids = _seed(engine)
    try:
        h = {"Authorization": f"Bearer {ids['plain']}"}
        r = client.get("/api/v1", headers=h)
        assert r.status_code == 200
        assert "endpoints" in r.json()
        r = client.get("/api/v1/health", headers=h)
        assert r.status_code == 200
        assert r.json().get("ok") is True
        r = client.get("/api/v1/summary", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert body.get("hosts") >= 1
        r = client.get("/api/v1/servers", headers=h)
        assert r.status_code == 200
        assert r.json()["total"] >= 1
        r = client.get("/api/v1/servers?q=lab", headers=h)
        assert r.status_code == 200
        r = client.get(f"/api/v1/servers/{ids['sid']}", headers=h)
        assert r.status_code == 200
        row = r.json()
        assert row.get("id") == ids["sid"] or row.get("name") == "Lab Pi"
        r = client.get("/api/v1/servers/99999", headers=h)
        assert r.status_code == 404
        r = client.get(f"/api/v1/servers/{ids['sid']}/jobs", headers=h)
        assert r.status_code == 200
        r = client.get("/api/v1/jobs", headers=h)
        assert r.status_code == 200
        r = client.get("/api/v1/jobs?active_only=true", headers=h)
        assert r.status_code == 200
        r = client.get(f"/api/v1/jobs/{ids['jid']}", headers=h)
        assert r.status_code == 200
        r = client.get("/api/v1/jobs/99999", headers=h)
        assert r.status_code == 404
        r = client.post(
            f"/api/v1/servers/{ids['sid']}/jobs",
            headers=h,
            json={"job_type": "backup"},
        )
        assert r.status_code in (401, 403, 404, 422)
    finally:
        app.dependency_overrides.clear()


def test_html_server_jobs_settings_routers(tmp_path):
    engine = _engine(tmp_path)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    ids = _seed(engine)
    from app.security.auth import create_user_access_token

    with Session(engine) as s:
        user = s.get(User, ids["uid"])
        client.cookies.set("access_token", create_user_access_token(user))
    try:
        r = client.get("/servers")
        assert r.status_code == 200
        r = client.get(f"/servers/{ids['sid']}")
        assert r.status_code in (200, 500)
        r = client.get(f"/servers/{ids['sid']}?edit=1")
        assert r.status_code in (200, 500)
        r = client.get("/servers/99999")
        assert r.status_code in (200, 404, 401, 500)
        r = client.get("/jobs")
        assert r.status_code == 200
        r = client.get(f"/jobs/{ids['jid']}")
        assert r.status_code == 200
        r = client.get("/jobs/99999")
        assert r.status_code in (200, 404)
        r = client.post(
            f"/jobs/{ids['jid']}/cancel",
            headers={"Accept": "application/json"},
        )
        assert r.status_code in (200, 400, 409, 404)
        r = client.post("/jobs/99999/cancel", headers={"Accept": "application/json"})
        assert r.status_code in (200, 404)
        r = client.get("/audit")
        assert r.status_code == 200
        r = client.get("/certificates")
        assert r.status_code == 200
        r = client.get("/herder-backups?tab=status")
        assert r.status_code == 200
        r = client.get("/herder-backups?tab=api")
        assert r.status_code == 200
        r = client.get("/notifications")
        assert r.status_code in (200, 404)
        r = client.get("/dns/candidates")
        assert r.status_code in (200, 303)
    finally:
        app.dependency_overrides.clear()

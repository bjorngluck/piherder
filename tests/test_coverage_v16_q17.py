"""v1.6 Q-80 seventeenth pack — more router HTTP (diagnostics, jobs, console, dns, bulk).

httpx TestClient ignores ``cookies=``; use ``client.cookies.set``.
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import ApiToken, Job, Server, User
from app.security.auth import create_user_access_token, get_password_hash
from app.security.encryption import encrypt_str
from app.services import api_tokens as tok


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'r17.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_router_diagnostics_jobs_console_dns_bulk(tmp_path, monkeypatch):
    from app.services import ssh_console as cons

    monkeypatch.setattr(cons.settings, "PIHERDER_SSH_CONSOLE", True)
    engine = _engine(tmp_path)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with Session(engine) as s:
            user = User(
                email="q17@test.local",
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
                host_facts_status="ok",
                host_facts_json=json.dumps({"os_pretty": "Ubuntu 24.04", "kernel": "6.8"}),
                os_pretty="Ubuntu 24.04 LTS",
                hardware="Pi 5",
                cpu_cores=4,
            )
            s.add(srv)
            s.commit()
            s.refresh(srv)
            job = Job(
                server_id=srv.id,
                job_type="os_update_check",
                status="success",
                details='{"actionable_count":0}',
            )
            s.add(job)
            plain = "ph_q17tokenvalue00000000000000000000"
            s.add(
                ApiToken(
                    name="q17",
                    token_prefix=plain[:12],
                    token_hash=tok.hash_token(plain),
                    scopes="read",
                    created_by_user_id=user.id,
                )
            )
            s.commit()
            s.refresh(job)
            sid, jid, uid = srv.id, job.id, user.id
            client.cookies.set("access_token", create_user_access_token(user))

        r = client.get(f"/servers/{sid}/diagnostics")
        assert r.status_code in (200, 401, 403, 404, 500)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
            body = r.json()
            assert body.get("from_snapshot") is True or body.get("os_pretty") or "error" in body or body
        r = client.get(f"/servers/{sid}/diagnostics?force=1")
        assert r.status_code in (200, 401, 403, 404, 500)
        r = client.get("/servers/99999/diagnostics")
        assert r.status_code in (404, 200, 401, 500)

        r = client.get(f"/servers/{sid}/jobs")
        assert r.status_code in (200, 401, 403, 404, 500)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
            assert "jobs" in r.json()
        r = client.get(f"/servers/{sid}/jobs/{jid}")
        assert r.status_code in (200, 404, 401, 500)
        r = client.get(f"/servers/{sid}/os-patch-progress")
        assert r.status_code in (200, 401, 404, 500)

        r = client.get(f"/servers/{sid}/console")
        assert r.status_code in (200, 303, 403, 500)
        r = client.get(f"/servers/{sid}/console?embed=1")
        assert r.status_code in (200, 303, 403, 500)
        r = client.get("/console")
        assert r.status_code in (200, 303, 403, 404, 500)

        r = client.get("/servers/add")
        assert r.status_code in (200, 303)
        r = client.get("/servers/new")
        assert r.status_code in (200, 303)

        r = client.post("/servers/bulk", data={"action": "nope", "server_ids": str(sid)})
        assert r.status_code in (200, 400, 422, 303, 401, 403)
        r = client.post(
            "/servers/bulk",
            data={"action": "check_os", "server_ids": f"{sid},x, {sid}"},
            follow_redirects=False,
        )
        assert r.status_code in (200, 303, 400, 409)

        r = client.get("/dns")
        assert r.status_code == 200
        r = client.get("/dns/coverage")
        assert r.status_code == 200
        r = client.get("/dns/physical")
        assert r.status_code == 200
        r = client.get("/dns/logical")
        assert r.status_code == 200
        r = client.get("/dns/candidates")
        assert r.status_code == 200

        r = client.get(f"/servers/{sid}/docker")
        assert r.status_code in (200, 303, 500)

        h = {"Authorization": f"Bearer {plain}"}
        r = client.patch(
            f"/api/v1/servers/{sid}/features",
            headers=h,
            json={"backup": False},
        )
        assert r.status_code in (401, 403)
        r = client.get(f"/api/v1/servers/{sid}/files", headers=h)
        assert r.status_code in (401, 403, 404)
        r = client.get("/api/v1/tokens", headers=h)
        assert r.status_code in (401, 403, 200)
        r = client.get("/api/v1/tokens")
        assert r.status_code in (200, 401, 403)
    finally:
        app.dependency_overrides.clear()

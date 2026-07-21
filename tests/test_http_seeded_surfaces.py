"""HTTP smoke with seeded nmap integration, server shell, jobs (SQLite)."""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Integration, Job, NmapDevice, NmapScanRun, Server, User
from app.security.auth import create_access_token, get_password_hash
from app.security.encryption import encrypt_str


@pytest.fixture()
def seeded_client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'seed.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)

    with Session(engine) as s:
        user = User(
            email="seed@test.local",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        s.add(user)
        s.commit()
        s.refresh(user)
        uid = user.id

        srv = Server(
            name="Lab Pi",
            hostname="lab.local",
            ip_address="192.168.1.10",
            ssh_username="pi",
            ssh_port=22,
            ssh_password_encrypted=encrypt_str("x"),
            backup_enabled=False,
            os_patch_enabled=True,
            container_patch_enabled=True,
        )
        s.add(srv)
        s.commit()
        s.refresh(srv)
        sid = srv.id

        integ = Integration(
            type="nmap",
            name="Home LAN",
            base_url="",
            enabled=True,
            config_json=json.dumps(
                {
                    "cidrs": ["192.168.1.0/24"],
                    "excludes": [],
                    "use_syn": False,
                    "vuln_enabled": True,
                }
            ),
        )
        s.add(integ)
        s.commit()
        s.refresh(integ)
        iid = integ.id

        s.add(
            NmapDevice(
                integration_id=iid,
                identity_key="ip:192.168.1.20",
                ip_address="192.168.1.20",
                hostname="cam.local",
                display_name="cctv1",
                state="new",
                ports_json=json.dumps(
                    [{"port": 80, "protocol": "tcp", "state": "open", "service": "http"}]
                ),
            )
        )
        s.add(
            NmapDevice(
                integration_id=iid,
                identity_key="ip:192.168.1.10",
                ip_address="192.168.1.10",
                state="linked",
                linked_server_id=sid,
                display_name="lab-pi",
            )
        )
        s.add(
            NmapScanRun(
                integration_id=iid,
                intensity="discovery",
                status="success",
                hosts_up=2,
                hosts_total=2,
                summary_json="{}",
            )
        )
        job = Job(
            server_id=sid,
            job_type="backup",
            status="success",
            details=json.dumps(
                {
                    "summary": "ok",
                    "log_lines": ["done"],
                    "current": "completed",
                }
            ),
            finished_at=datetime.utcnow(),
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        jid = job.id

    cookies = {"access_token": create_access_token({"sub": str(uid)})}
    try:
        yield client, cookies, {"server_id": sid, "integration_id": iid, "job_id": jid}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "path_tpl",
    [
        "/integrations/{integration_id}",
        "/integrations/{integration_id}?tab=devices",
        "/integrations/{integration_id}?tab=network",
        "/integrations/{integration_id}?tab=schedules",
        "/integrations/{integration_id}?tab=runs",
        "/integrations/{integration_id}/edit",
        "/servers/{server_id}",
        "/servers/{server_id}/jobs",
        "/servers/{server_id}/services",
        "/servers/{server_id}/docker",
        "/servers/{server_id}/diagnostics",
        "/jobs",
        "/jobs/{job_id}",
        "/notifications",
        "/notifications/count",
        "/notifications/preview",
        "/certificates/setup",
        "/certificates/upload",
        "/templates/new",
        "/templates/from-host",
        "/integrations/new/pihole",
        "/integrations/new/nmap",
        "/herder-backups",
        "/herder-backups?tab=general",
        "/herder-backups?tab=cleanup",
        "/dns/stack-panel",
        "/auth/users",
        "/api/push/status",
        "/api/push/vapid-public-key",
        "/metrics",
    ],
)
def test_seeded_surfaces_ok(seeded_client, path_tpl):
    client, cookies, ids = seeded_client
    path = path_tpl.format(**ids)
    r = client.get(path, cookies=cookies, follow_redirects=False)
    # 200 HTML/JSON, 303 redirect, 404 for optional panels without data is ok-ish;
    # 500 is not
    assert r.status_code < 500, f"{path} → {r.status_code}: {r.text[:300]}"
    assert r.status_code in (
        200,
        303,
        302,
        401,
        403,
        404,
        422,
    ) or r.status_code < 500
    # Prefer success for core surfaces
    if path in (
        "/jobs",
        f"/integrations/{ids['integration_id']}",
        f"/servers/{ids['server_id']}",
    ):
        assert r.status_code == 200, f"{path} → {r.status_code}: {r.text[:200]}"

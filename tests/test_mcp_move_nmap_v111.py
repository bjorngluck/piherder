"""Hosted MCP can start a Move and can start or read a LAN Discovery scan.

``service_migrate`` stays off ``trigger_job``. Undo stays off. A scan uses
the ranges saved on the integration.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import AuditLog, Integration, NmapDevice, NmapScanRun, Server, User
from app.security.auth import get_password_hash
from app.security.encryption import encrypt_str
from app.services import api_tokens as tok
from app.services.mcp_hosted import MCP_JOB_TYPES
from app.services.nmap.config import create_nmap


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'mcp-should.db'}",
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


def _rpc(client, token: str, name: str, arguments: dict):
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )


def _tool_payload(response):
    body = response.json()["result"]
    text = json.loads(body["content"][0]["text"])
    return body, text


def _user(session) -> User:
    user = User(
        email="mcp-should@test.local",
        hashed_password=get_password_hash("SmokeTest1ok"),
        role="admin",
        is_active=True,
        must_change_password=False,
        totp_enabled=False,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _token(session, user, scopes: str) -> str:
    plain = "ph_" + scopes.replace(",", "").replace(":", "") + "0" * 20
    plain = plain[:40]
    session.add(
        tok.ApiToken(
            name="agent",
            token_prefix=plain[:12],
            token_hash=tok.hash_token(plain),
            scopes=scopes,
            created_by_user_id=user.id,
        )
    )
    session.commit()
    return plain


def _server(name: str) -> Server:
    return Server(
        name=name,
        hostname=f"{name}.local",
        ip_address="10.1.0.8",
        ssh_username="pi",
        ssh_port=22,
        ssh_password_encrypted=encrypt_str("x"),
        backup_enabled=True,
        os_patch_enabled=True,
        container_patch_enabled=True,
    )


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


def _scan_job():
    return SimpleNamespace(
        id=70,
        server_id=None,
        job_type="nmap_discovery",
        status="pending",
        details=None,
        created_at=None,
        started_at=None,
        finished_at=None,
        worker_hostname=None,
    )


def _scan_run(integration_id: int):
    return SimpleNamespace(
        id=9,
        integration_id=integration_id,
        job_id=70,
        intensity="discovery",
        status="pending",
        hosts_up=0,
        hosts_total=0,
        ports_open=0,
        targets_json='["192.168.86.0/24"]',
        error=None,
        started_at=None,
        finished_at=None,
    )


def test_move_and_nmap_stay_off_trigger_job():
    assert "service_migrate" not in MCP_JOB_TYPES
    assert "service_migrate_undo" not in MCP_JOB_TYPES
    assert "nmap_discovery" not in MCP_JOB_TYPES
    paths = [row["path"] for row in tok.api_meta_dict()["endpoints"]]
    assert "/api/v1/discovery" in paths
    assert "/api/v1/discovery/{id}/scans" in paths


def test_hosted_start_move_confirms_and_leaves_undo_off(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    client = _client(engine)
    captured: dict = {}

    def fake(source_id, dest_id, project, **kwargs):
        captured["args"] = (source_id, dest_id, project)
        captured["kwargs"] = kwargs
        return _job()

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr("app.routers.api_v1.job_service.enqueue_service_migrate", fake)
    with Session(engine) as session:
        user = _user(session)
        plain = _token(session, user, "read,jobs,feature:docker")
        read_only = _token(session, user, "read")
        source = _server("source")
        dest = _server("dest")
        session.add(source)
        session.add(dest)
        session.commit()
        session.refresh(source)
        session.refresh(dest)
        source_id, dest_id = source.id, dest.id
    try:
        listed = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "Authorization": f"Bearer {read_only}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        names = [tool["name"] for tool in listed.json()["result"]["tools"]]
        assert "read_discovery" in names
        assert "start_move" not in names
        assert "start_discovery" not in names

        refused = _rpc(
            client,
            plain,
            "start_move",
            {
                "server_id": source_id,
                "dest_server_id": dest_id,
                "project": "web",
                "confirm": False,
            },
        )
        _body, text = _tool_payload(refused)
        assert text["ok"] is False
        assert text["detail"] == "confirm must be true"
        assert captured == {}

        path = _rpc(
            client,
            plain,
            "start_move",
            {
                "server_id": source_id,
                "dest_server_id": dest_id,
                "project": "/opt/web",
                "confirm": True,
            },
        )
        _body, text = _tool_payload(path)
        assert text["ok"] is False
        assert captured == {}

        ok = _rpc(
            client,
            plain,
            "start_move",
            {
                "server_id": source_id,
                "dest_server_id": dest_id,
                "project": "web",
                "confirm": True,
            },
        )
        body, text = _tool_payload(ok)
        assert body["isError"] is False
        assert text["http_status"] == 202
        assert text["job_type"] == "service_migrate"
        assert text["leftover"] == "stopped"
        assert captured["args"] == (source_id, dest_id, "web")
        assert captured["kwargs"]["leftover"] == "stopped"
        assert "undo" not in captured["kwargs"]

        jobs = _rpc(
            client,
            plain,
            "trigger_job",
            {"server_id": source_id, "job_type": "service_migrate"},
        )
        _body, text = _tool_payload(jobs)
        assert text["ok"] is False
        assert "must be one of" in text["detail"]
    finally:
        app.dependency_overrides.clear()


def test_discovery_read_hides_secrets_and_start_uses_saved_ranges(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    client = _client(engine)
    captured: dict = {}

    def fake(session, *, integration_id, intensity, targets, user_id, scan_options, **kwargs):
        captured["call"] = {
            "integration_id": integration_id,
            "intensity": intensity,
            "targets": list(targets),
            "user_id": user_id,
            "scan_options": scan_options,
            "extra": kwargs,
        }
        run = _scan_run(integration_id)
        run.intensity = intensity
        job = _scan_job()
        job.job_type = f"nmap_{intensity}"
        return job, run

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr("app.services.nmap.public_api.enqueue_nmap_scan", fake)
    with Session(engine) as session:
        user = _user(session)
        plain = _token(session, user, "read,jobs")
        read_only = _token(session, user, "read")
        row = create_nmap(session, name="LAN", cidrs=["192.168.86.0/24"])
        row.credentials_encrypted = "super-secret-token"
        row.config_json = json.dumps(
            {
                "cidrs": ["192.168.86.0/24"],
                "notes": "operator-private-note",
            }
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        run = NmapScanRun(
            integration_id=row.id,
            intensity="discovery",
            status="success",
            hosts_up=2,
            hosts_total=3,
            ports_open=1,
            targets_json='["192.168.86.0/24"]',
            artifact_path="/data/nmap/secret.xml",
            summary_json='{"script":"private-script-output"}',
            error="",
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        session.add(
            NmapDevice(
                integration_id=row.id,
                identity_key="ip:192.168.86.35",
                ip_address="192.168.86.35",
                hostname="rpi5-3",
                state="known",
                notes="private-device-note",
                mac_address="aa:bb:cc:dd:ee:ff",
            )
        )
        session.commit()
        integration_id = row.id
        run_id = run.id
    headers = {"Authorization": f"Bearer {plain}"}
    try:
        listed = client.get("/api/v1/discovery", headers=headers)
        assert listed.status_code == 200
        body = listed.json()
        assert body["integrations"][0]["cidrs"] == ["192.168.86.0/24"]
        assert body["integrations"][0]["latest_run"]["id"] == run_id
        raw = listed.text
        assert "super-secret-token" not in raw
        assert "operator-private-note" not in raw
        assert "secret.xml" not in raw
        assert "private-script-output" not in raw

        detail = client.get(f"/api/v1/discovery/{integration_id}", headers=headers)
        assert detail.status_code == 200
        device = detail.json()["devices"][0]
        assert device["ip"] == "192.168.86.35"
        assert device["hostname"] == "rpi5-3"
        assert "mac" not in device
        assert "private-device-note" not in detail.text

        one = client.get(
            f"/api/v1/discovery/{integration_id}/runs/{run_id}",
            headers={"Authorization": f"Bearer {read_only}"},
        )
        assert one.status_code == 200
        assert one.json()["run"]["hosts_up"] == 2
        assert "artifact_path" not in one.text

        missing = client.post(
            f"/api/v1/discovery/{integration_id}/scans",
            headers=headers,
            json={"confirm": False},
        )
        assert missing.status_code == 400
        assert captured == {}

        extra = client.post(
            f"/api/v1/discovery/{integration_id}/scans",
            headers=headers,
            json={"confirm": True, "targets": ["1.2.3.4"]},
        )
        assert extra.status_code == 422
        assert captured == {}

        started = _rpc(
            client,
            plain,
            "start_discovery",
            {
                "integration_id": integration_id,
                "confirm": True,
                "targets": "10.9.9.0/24",
                "intensity": "inventory",
            },
        )
        body, text = _tool_payload(started)
        assert body["isError"] is False
        assert text["http_status"] == 202
        assert text["intensity"] == "inventory"
        assert text["targets"] == ["192.168.86.0/24"]
        assert captured["call"]["targets"] == ["192.168.86.0/24"]
        assert captured["call"]["intensity"] == "inventory"
        assert captured["call"]["scan_options"]["script_preset"] == "none"
        assert captured["call"]["scan_options"]["vuln_scripts"] is False
        assert "10.9.9.0/24" not in json.dumps(captured)

        with Session(engine) as session:
            audit = session.exec(
                select(AuditLog).where(AuditLog.action == "nmap_scan_queued")
            ).first()
            assert audit is not None
            assert "super-secret-token" not in (audit.details or "")

        read_tool = _rpc(
            client,
            read_only,
            "read_discovery",
            {"integration_id": integration_id, "run_id": run_id},
        )
        body, text = _tool_payload(read_tool)
        assert body["isError"] is False
        assert text["run"]["hosts_up"] == 2
        assert "private-script-output" not in json.dumps(text)

        hidden = _rpc(
            client,
            read_only,
            "start_discovery",
            {"integration_id": integration_id, "confirm": True},
        )
        body, text = _tool_payload(hidden)
        assert body["isError"] is True
        assert "Unknown tool" in text["detail"]
    finally:
        app.dependency_overrides.clear()


def test_discovery_start_refuses_off_and_empty(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    client = _client(engine)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(
        "app.services.nmap.public_api.enqueue_nmap_scan",
        lambda *a, **k: (_scan_job(), _scan_run(1)),
    )
    with Session(engine) as session:
        user = _user(session)
        plain = _token(session, user, "read,jobs")
        off = Integration(
            type="nmap",
            name="Off",
            base_url="local://nmap",
            enabled=False,
            config_json=json.dumps({"cidrs": ["10.0.0.0/24"]}),
        )
        empty = Integration(
            type="nmap",
            name="Empty",
            base_url="local://nmap",
            enabled=True,
            config_json=json.dumps({"cidrs": []}),
        )
        bad = Integration(
            type="nmap",
            name="Bad",
            base_url="local://nmap",
            enabled=True,
            config_json=json.dumps({"cidrs": ["10.1.0.0/24"]}),
        )
        session.add(off)
        session.add(empty)
        session.add(bad)
        session.commit()
        session.refresh(off)
        session.refresh(empty)
        session.refresh(bad)
        off_id, empty_id, bad_id = off.id, empty.id, bad.id
    headers = {"Authorization": f"Bearer {plain}"}
    try:
        disabled = client.post(
            f"/api/v1/discovery/{off_id}/scans",
            headers=headers,
            json={"confirm": True},
        )
        assert disabled.status_code == 400
        assert disabled.json()["detail"] == "LAN Discovery is off"
        bare = client.post(
            f"/api/v1/discovery/{empty_id}/scans",
            headers=headers,
            json={"confirm": True},
        )
        assert bare.status_code == 400
        assert bare.json()["detail"] == "No scan ranges are saved"
        nope = client.post(
            f"/api/v1/discovery/{bad_id}/scans",
            headers=headers,
            json={"confirm": True, "intensity": "nope"},
        )
        assert nope.status_code == 400
        assert "intensity must be one of" in nope.json()["detail"]
    finally:
        app.dependency_overrides.clear()

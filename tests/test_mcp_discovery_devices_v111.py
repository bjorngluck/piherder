"""Hosted MCP can tidy LAN Discovery devices. Issue #34.

Rename, mark, link, and purge use the same rules as the web UI.
A linked device cannot be purged. A device scan stays inside the saved ranges.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import AuditLog, Integration, NmapDevice, Server, User
from app.security.auth import get_password_hash
from app.security.encryption import encrypt_str
from app.services import api_tokens as tok
from app.services.mcp_hosted import MCP_JOB_TYPES


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'mcp-devices.db'}",
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
        email="mcp-devices@test.local",
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
    plain = "ph_" + scopes.replace(",", "").replace(":", "") + "0" * 24
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


def test_device_tools_stay_off_the_jobs_post():
    assert "nmap_discovery" not in MCP_JOB_TYPES
    paths = [row["path"] for row in tok.api_meta_dict()["endpoints"]]
    assert "/api/v1/discovery/{id}/devices" in paths
    assert "/api/v1/discovery/{id}/devices/purge-stale" in paths
    assert "/api/v1/discovery/{id}/devices/{device_id}/scans" in paths


def test_agent_can_rename_link_and_purge(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    client = _client(engine)
    captured: dict = {}
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    def fake(session, **kwargs):
        captured["call"] = kwargs
        run = SimpleNamespace(
            id=9,
            integration_id=kwargs["integration_id"],
            job_id=70,
            intensity=kwargs["intensity"],
            status="pending",
            hosts_up=0,
            hosts_total=0,
            ports_open=0,
            targets_json=json.dumps(kwargs["targets"]),
            error=None,
            started_at=None,
            finished_at=None,
        )
        job = SimpleNamespace(
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
        return job, run

    monkeypatch.setattr("app.services.nmap.public_api.enqueue_nmap_scan", fake)
    old = datetime.utcnow() - timedelta(days=30)
    with Session(engine) as session:
        user = _user(session)
        plain = _token(session, user, "read,jobs,edit")
        read_only = _token(session, user, "read")
        server = Server(
            name="desk",
            hostname="desk.local",
            ip_address="192.168.86.20",
            ssh_username="pi",
            ssh_port=22,
            ssh_password_encrypted=encrypt_str("x"),
            backup_enabled=True,
            os_patch_enabled=True,
            container_patch_enabled=True,
        )
        session.add(server)
        row = Integration(
            type="nmap",
            name="LAN",
            base_url="local://nmap",
            enabled=True,
            config_json=json.dumps({"cidrs": ["192.168.86.0/24"]}),
        )
        session.add(row)
        session.commit()
        session.refresh(server)
        session.refresh(row)
        session.add(
            NmapDevice(
                integration_id=row.id,
                identity_key="ip:192.168.86.10",
                ip_address="192.168.86.10",
                hostname="cam",
                display_name="old",
                kind_override="printer",
                state="new",
            )
        )
        session.add(
            NmapDevice(
                integration_id=row.id,
                identity_key="ip:192.168.86.11",
                ip_address="192.168.86.11",
                state="linked",
                linked_server_id=server.id,
                last_seen_at=old,
            )
        )
        session.add(
            NmapDevice(
                integration_id=row.id,
                identity_key="ip:192.168.86.12",
                ip_address="192.168.86.12",
                state="known",
                last_seen_at=old,
            )
        )
        session.add(
            NmapDevice(
                integration_id=row.id,
                identity_key="ip:10.9.9.9",
                ip_address="10.9.9.9",
                state="known",
            )
        )
        session.commit()
        integration_id = row.id
        server_id = server.id
        fresh = session.exec(
            select(NmapDevice).where(NmapDevice.ip_address == "192.168.86.10")
        ).one()
        linked = session.exec(
            select(NmapDevice).where(NmapDevice.ip_address == "192.168.86.11")
        ).one()
        stale = session.exec(
            select(NmapDevice).where(NmapDevice.ip_address == "192.168.86.12")
        ).one()
        outside = session.exec(
            select(NmapDevice).where(NmapDevice.ip_address == "10.9.9.9")
        ).one()
        fresh_id, linked_id, stale_id, outside_id = fresh.id, linked.id, stale.id, outside.id
    headers = {"Authorization": f"Bearer {plain}"}
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
        assert "list_discovery_devices" in names
        assert "rename_discovery_device" not in names
        assert "purge_discovery_device" not in names
        assert "scan_discovery_device" not in names

        page = client.get(
            f"/api/v1/discovery/{integration_id}/devices",
            headers={"Authorization": f"Bearer {read_only}"},
            params={"state": "stale", "limit": 10, "offset": 0},
        )
        assert page.status_code == 200
        stale_ids = [row["id"] for row in page.json()["devices"]]
        assert stale_id in stale_ids
        assert linked_id not in stale_ids
        assert "aa:bb" not in page.text

        renamed = _rpc(
            client,
            plain,
            "rename_discovery_device",
            {
                "integration_id": integration_id,
                "device_id": fresh_id,
                "display_name": "Front camera",
            },
        )
        body, text = _tool_payload(renamed)
        assert body["isError"] is False
        assert text["device"]["display_name"] == "Front camera"
        assert text["device"]["state"] == "known"
        with Session(engine) as session:
            row = session.get(NmapDevice, fresh_id)
            assert row.kind_override == "printer"
            audit = session.exec(
                select(AuditLog).where(AuditLog.action == "nmap_device_mapped")
            ).first()
            assert audit is not None
            assert audit.api_token_id is not None

        refused_new = _rpc(
            client,
            plain,
            "set_discovery_device_state",
            {
                "integration_id": integration_id,
                "device_id": linked_id,
                "state": "new",
            },
        )
        body, text = _tool_payload(refused_new)
        assert body["isError"] is True
        assert text["detail"] == "unlink before marking new"

        linked_ok = _rpc(
            client,
            plain,
            "link_discovery_device",
            {
                "integration_id": integration_id,
                "device_id": fresh_id,
                "server_id": server_id,
            },
        )
        body, text = _tool_payload(linked_ok)
        assert body["isError"] is False
        assert text["device"]["state"] == "linked"
        assert text["device"]["linked_server_id"] == server_id

        no_purge = _rpc(
            client,
            plain,
            "purge_discovery_device",
            {
                "integration_id": integration_id,
                "device_id": fresh_id,
                "confirm": True,
            },
        )
        body, text = _tool_payload(no_purge)
        assert body["isError"] is True
        assert "Unlink" in text["detail"]
        with Session(engine) as session:
            assert session.get(NmapDevice, fresh_id) is not None

        unlinked = _rpc(
            client,
            plain,
            "unlink_discovery_device",
            {"integration_id": integration_id, "device_id": fresh_id},
        )
        body, text = _tool_payload(unlinked)
        assert text["device"]["state"] == "known"
        assert text["device"]["linked_server_id"] is None

        held = _rpc(
            client,
            plain,
            "purge_discovery_device",
            {
                "integration_id": integration_id,
                "device_id": fresh_id,
                "confirm": False,
            },
        )
        body, text = _tool_payload(held)
        assert text["ok"] is False
        assert text["detail"] == "confirm must be true"
        with Session(engine) as session:
            assert session.get(NmapDevice, fresh_id) is not None

        gone = _rpc(
            client,
            plain,
            "purge_discovery_device",
            {
                "integration_id": integration_id,
                "device_id": fresh_id,
                "confirm": True,
            },
        )
        body, text = _tool_payload(gone)
        assert text["device_ids"] == [fresh_id]
        with Session(engine) as session:
            assert session.get(NmapDevice, fresh_id) is None
            audit = session.exec(
                select(AuditLog).where(AuditLog.action == "nmap_device_purged")
            ).first()
            assert audit is not None
            assert audit.client_ip is not None

        bulk_held = _rpc(
            client,
            plain,
            "purge_stale_discovery_devices",
            {"integration_id": integration_id, "confirm": False},
        )
        body, text = _tool_payload(bulk_held)
        assert text["detail"] == "confirm must be true"

        bulk = _rpc(
            client,
            plain,
            "purge_stale_discovery_devices",
            {"integration_id": integration_id, "confirm": True},
        )
        body, text = _tool_payload(bulk)
        assert stale_id in text["device_ids"]
        assert linked_id not in text["device_ids"]
        with Session(engine) as session:
            assert session.get(NmapDevice, stale_id) is None
            assert session.get(NmapDevice, linked_id) is not None

        outside_scan = client.post(
            f"/api/v1/discovery/{integration_id}/devices/{outside_id}/scans",
            headers=headers,
            json={"confirm": True, "intensity": "deep"},
        )
        assert outside_scan.status_code == 400
        assert outside_scan.json()["detail"] == "This device is outside the saved ranges"
        assert captured == {}

        scanned = _rpc(
            client,
            plain,
            "scan_discovery_device",
            {
                "integration_id": integration_id,
                "device_id": linked_id,
                "confirm": True,
            },
        )
        body, text = _tool_payload(scanned)
        assert body["isError"] is False
        assert text["http_status"] == 202
        assert text["intensity"] == "deep"
        assert text["targets"] == ["192.168.86.11"]
        assert captured["call"]["scan_options"]["vuln_scripts"] is False
        assert captured["call"]["scan_options"]["script_preset"] == "none"
        assert captured["call"]["targets"] == ["192.168.86.11"]

        extra = client.patch(
            f"/api/v1/discovery/{integration_id}/devices/{linked_id}",
            headers=headers,
            json={"display_name": "x", "mac": "secret"},
        )
        assert extra.status_code == 422
    finally:
        app.dependency_overrides.clear()

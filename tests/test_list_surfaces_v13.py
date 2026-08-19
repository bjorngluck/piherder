"""v1.3 slice 3 Deep — Servers / Docker / discovery / API list chrome (HTTP)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from datetime import datetime

from app.models import ApiToken, Integration, NmapDevice, Server, User
from app.security.auth import create_access_token, get_password_hash
from app.services import api_tokens as tok
from app.services import list_query as lq
from app.services.nmap import config as nmap_cfg


@pytest.fixture()
def list_client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'list.db'}",
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
            email="list@test.local",
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

        hosts = []
        for i in range(12):
            srv = Server(
                name=f"host-{i:02d}",
                hostname=f"h{i}.local",
                ip_address=f"192.168.1.{10 + i}",
                ssh_username="piherder",
                os_updates_count=2 if i == 3 else 0,
                container_patch_enabled=True,
            )
            if i == 1:
                srv.name = "hass-core"
                srv.hostname = "homeassistant.local"
            s.add(srv)
            hosts.append(srv)
        s.commit()
        for h in hosts:
            s.refresh(h)
        sid = hosts[1].id

        inv = {
            "v": 2,
            "projects": [
                {
                    "name": "homeassistant",
                    "path": "/home/pi/docker/homeassistant",
                    "has_pending_update": False,
                    "services": ["homeassistant"],
                    "containers": [
                        {
                            "name": "homeassistant",
                            "compose_service": "homeassistant",
                            "image": "ghcr.io/home-assistant/home-assistant",
                            "running": True,
                        }
                    ],
                },
                {
                    "name": "edge",
                    "path": "/home/pi/docker/edge",
                    "has_pending_update": True,
                    "services": ["app"],
                    "containers": [
                        {
                            "name": "npm",
                            "compose_service": "app",
                            "image": "jc21/nginx-proxy-manager",
                            "running": False,
                            "has_pending_update": True,
                        }
                    ],
                },
            ]
            + [
                {
                    "name": f"stack-{n}",
                    "path": f"/home/pi/docker/stack-{n}",
                    "services": [f"c{n}"],
                    "containers": [{"name": f"c{n}", "running": True}],
                }
                for n in range(10)
            ],
            "orphan_containers": [],
            "meta": {"project_count": 10, "container_count": 10},
        }
        hosts[1].docker_inventory_json = json.dumps(inv)
        hosts[1].docker_inventory_status = "ok"
        hosts[1].docker_inventory_at = datetime.utcnow()
        s.add(hosts[1])

        integ = Integration(type="nmap", name="LAN", base_url="", enabled=True, config_json="{}")
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
            )
        )
        s.add(
            NmapDevice(
                integration_id=iid,
                identity_key="ip:192.168.1.21",
                ip_address="192.168.1.21",
                hostname="pihole.local",
                display_name="dns",
                state="known",
            )
        )
        s.add(
            NmapDevice(
                integration_id=iid,
                identity_key="ip:192.168.1.22",
                ip_address="192.168.1.22",
                hostname="rpi-lab.local",
                display_name="lab",
                state="new",
            )
        )
        for n in range(10):
            s.add(
                NmapDevice(
                    integration_id=iid,
                    identity_key=f"ip:192.168.1.{30 + n}",
                    ip_address=f"192.168.1.{30 + n}",
                    hostname=f"extra-{n}.local",
                    state="known",
                )
            )
        s.commit()

    cookies = {"access_token": create_access_token({"sub": str(uid)})}
    try:
        yield client, cookies, engine, {
            "uid": uid,
            "server_id": sid,
            "integration_id": iid,
        }
    finally:
        app.dependency_overrides.clear()


def test_servers_q_and_alias(list_client):
    client, cookies, _engine, _ids = list_client
    r = client.get("/servers?q=ha", cookies=cookies)
    assert r.status_code == 200
    assert "hass-core" in r.text
    assert "host-00" not in r.text
    assert 'data-testid="server-list-search"' in r.text


def test_servers_filter_and_q_compose(list_client):
    client, cookies, _engine, _ids = list_client
    r = client.get("/servers?filter=os&q=host-03", cookies=cookies)
    assert r.status_code == 200
    assert "host-03" in r.text
    r2 = client.get("/servers?filter=os&q=hass", cookies=cookies)
    assert r2.status_code == 200
    assert 'data-testid="server-list-empty-filter"' in r2.text


def test_servers_pager(list_client):
    client, cookies, _engine, _ids = list_client
    r = client.get("/servers?per_page=10", cookies=cookies)
    assert r.status_code == 200
    assert 'data-testid="list-pager"' in r.text
    assert "Page 1 of 2" in r.text
    r2 = client.get("/servers?per_page=10&page=2", cookies=cookies)
    assert r2.status_code == 200
    assert "Page 2 of 2" in r2.text
    assert r2.cookies.get(lq.COOKIE) == "10"


def test_servers_reorder_lists_all(list_client):
    client, cookies, _engine, _ids = list_client
    r = client.get("/servers?reorder=1&per_page=10", cookies=cookies)
    assert r.status_code == 200
    assert "hass-core" in r.text
    assert "host-11" in r.text
    assert 'data-testid="list-pager"' not in r.text


def test_docker_fragment_filters(list_client):
    client, cookies, _engine, ids = list_client
    sid = ids["server_id"]
    r = client.get(f"/servers/{sid}/docker/stack-fragment?q=ha", cookies=cookies)
    assert r.status_code == 200
    assert "homeassistant" in r.text
    assert "stack-0" not in r.text
    r2 = client.get(f"/servers/{sid}/docker/stack-fragment?status=stopped", cookies=cookies)
    assert r2.status_code == 200
    assert "edge" in r2.text
    assert "homeassistant" not in r2.text
    r3 = client.get(
        f"/servers/{sid}/docker/stack-fragment?per_page=10&page=2", cookies=cookies
    )
    assert r3.status_code == 200
    assert 'data-testid="list-pager"' in r3.text
    r4 = client.get(
        f"/servers/{sid}/docker/stack-fragment?q=nope&project=edge", cookies=cookies
    )
    assert r4.status_code == 200
    assert "edge" in r4.text


def test_nmap_list_q_state_and_page(list_client):
    client, cookies, engine, ids = list_client
    iid = ids["integration_id"]
    r = client.get(f"/integrations/{iid}?tab=devices&q=pi-hole", cookies=cookies)
    assert r.status_code == 200
    assert "pihole.local" in r.text
    assert "cam.local" not in r.text
    r2 = client.get(f"/integrations/{iid}?tab=devices&state=new", cookies=cookies)
    assert r2.status_code == 200
    assert "cctv1" in r2.text
    assert "pihole.local" not in r2.text
    r3 = client.get(
        f"/integrations/{iid}?tab=devices&per_page=10&page=2", cookies=cookies
    )
    assert r3.status_code == 200
    assert 'data-testid="list-pager"' in r3.text
    r4 = client.get(f"/integrations/{iid}?tab=devices&view=map", cookies=cookies)
    assert r4.status_code == 200
    assert 'data-testid="nmap-view-map"' in r4.text


def test_nmap_device_matches_alias():
    dev = NmapDevice(
        integration_id=1,
        identity_key="ip:1",
        ip_address="10.0.0.1",
        hostname="homeassistant.lan",
        state="new",
    )
    assert nmap_cfg.device_matches_q(dev, "ha")
    assert not nmap_cfg.device_matches_q(dev, "chase")


def test_api_servers_q_limit_offset(list_client):
    client, cookies, engine, ids = list_client
    with Session(engine) as s:
        plain = "ph_listtokenvalue00000000000000000000"
        row = ApiToken(
            name="list-api",
            token_prefix=plain[:10],
            token_hash=tok.hash_token(plain),
            scopes="read",
            created_by_user_id=ids["uid"],
        )
        s.add(row)
        s.commit()
    r = client.get("/api/v1/servers?q=ha", headers={"Authorization": f"Bearer {plain}"})
    assert r.status_code == 200
    body = r.json()
    names = [x["name"] for x in body["servers"]]
    assert names == ["hass-core"]
    assert body["total"] == 1
    r2 = client.get("/api/v1/servers?limit=5&offset=5", headers={"Authorization": f"Bearer {plain}"})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["total"] == 12
    assert body2["limit"] == 5
    assert len(body2["servers"]) == 5
    r3 = client.get("/api/v1/servers", headers={"Authorization": f"Bearer {plain}"})
    assert r3.status_code == 200
    assert r3.json()["limit"] == 100
    assert r3.json()["total"] == 12

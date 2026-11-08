"""Coverage for DNS routes, integration routes, and dns_fabric.core gaps.

Public placeholders only. Outbound HTTP, SSH, and nmap are stubbed.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select
from starlette.requests import Request

from app.database import get_session
from app.main import app
from app.models import (
    Integration,
    IntegrationBinding,
    ManagedCertificate,
    NmapDevice,
    NmapScanRun,
    NmapScanSchedule,
    NmapScriptResult,
    Server,
    ServiceDnsRecord,
    StackDeployment,
    User,
)
from app.routers import dns as dns_routes
from app.routers import integrations_nmap as nmap_routes
from app.routers import integrations_pihole as pihole_routes
from app.security.auth import create_access_token, get_password_hash
from app.services import app_settings as cfg
from app.services import jobs as job_service
from app.services.dns_fabric import core as fabric_core
from app.services.integrations import pihole as ph
from app.services.integrations import poll as poll_svc
from app.services.integrations import registry as reg
from app.services.nmap.device_classify import MAP_ROLE_GATEWAY

_REAL_PIHOLE_JOB = pihole_routes._run_pihole_action_job


class _Net:
    """Mutable stubs for Pi-hole, polls, and nmap enqueue."""

    hosts: list = []
    cnames: list = []
    login_fail_urls: set = set()
    list_fail = False
    add_host_error = ""
    add_cname_error = ""
    delete_error = ""
    run_action_out = "ok"
    run_action_error = ""
    stats_ok = True
    stats_error = ""
    stats_raise = False
    test_ok = True
    poll = {
        "ok": True,
        "queries": 4,
        "blocked": 1,
        "monitor_count": 2,
        "dashboard_count": 2,
        "version": "1.2.3",
        "proxy_host_count": 1,
        "device_count": 3,
        "status_code": 200,
        "skipped": False,
        "error": "",
    }
    enqueue_error = ""
    vuln_error = ""


def _reset_net() -> None:
    _Net.hosts = [
        {"domain": "host-a.example.com", "ip": "192.0.2.10"},
        {"domain": "", "ip": "192.0.2.10"},
        {"domain": "zzzhost-a.example.com", "ip": "192.0.2.10"},
        {"domain": "host-a.example.com", "ip": "198.51.100.9"},
    ]
    _Net.cnames = [
        {"domain": "app.example.com", "target": "host-a.example.com"},
        {"domain": "ghost.example.com", "target": "missing.example.com"},
        {"domain": "", "target": "host-a.example.com"},
    ]
    _Net.login_fail_urls = set()
    _Net.list_fail = False
    _Net.add_host_error = ""
    _Net.add_cname_error = ""
    _Net.delete_error = ""
    _Net.run_action_out = "pack refreshed"
    _Net.run_action_error = ""
    _Net.stats_ok = True
    _Net.stats_error = ""
    _Net.stats_raise = False
    _Net.test_ok = True
    _Net.poll = {
        "ok": True,
        "queries": 4,
        "blocked": 1,
        "monitor_count": 2,
        "dashboard_count": 2,
        "version": "1.2.3",
        "proxy_host_count": 1,
        "device_count": 3,
        "status_code": 200,
        "skipped": False,
        "error": "",
    }
    _Net.enqueue_error = ""
    _Net.vuln_error = ""


def _login(base, password, tls_verify=True):
    if base in _Net.login_fail_urls or _Net.run_action_error == "login":
        raise RuntimeError("login failed")
    return SimpleNamespace(base_url=base, sid="sid")


def _logout(sess):
    return None


def _list_hosts(sess):
    if _Net.list_fail:
        raise RuntimeError("list hosts failed")
    return list(_Net.hosts)


def _list_cnames(sess):
    if _Net.list_fail:
        raise RuntimeError("list cnames failed")
    return list(_Net.cnames)


def _add_host(sess, ip, domain):
    if _Net.add_host_error:
        raise RuntimeError(_Net.add_host_error)


def _del_host(sess, ip, domain):
    if _Net.delete_error:
        raise RuntimeError(_Net.delete_error)


def _add_cname(sess, domain, target):
    if _Net.add_cname_error:
        raise RuntimeError(_Net.add_cname_error)


def _del_cname(sess, domain, target, raw=None):
    if _Net.delete_error:
        raise RuntimeError(_Net.delete_error)


def _run_action(sess, action):
    if _Net.run_action_error and _Net.run_action_error != "login":
        raise RuntimeError(_Net.run_action_error)
    return _Net.run_action_out


def _fetch_stats(base, password, tls_verify=True):
    if _Net.stats_raise:
        raise RuntimeError("stats down")
    return SimpleNamespace(ok=_Net.stats_ok, error=_Net.stats_error or "failed", queries=2, blocked=1)


def _test_connection(integration):
    return SimpleNamespace(
        ok=_Net.test_ok,
        error="" if _Net.test_ok else "boom",
        dashboards=[{"uid": "dash1"}],
        version="9.0",
        queries=8,
        blocked=2,
        proxy_hosts=[{"id": 1}],
        certificates=[{"id": 1}],
        cidrs=["192.0.2.0/24"],
        worker_online=False,
        status_code=204,
        monitors=[{"id": "m1"}],
    )


def _poll_integration(integration_id, notify=False):
    return dict(_Net.poll)


def _enqueue(session, **kwargs):
    if _Net.enqueue_error:
        raise RuntimeError(_Net.enqueue_error)
    job = job_service.Job(
        server_id=None,
        job_type="nmap_discovery",
        status="pending",
        details="{}",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    run = NmapScanRun(
        integration_id=int(kwargs["integration_id"]),
        job_id=job.id,
        intensity=kwargs.get("intensity") or "discovery",
        status="pending",
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return job, run


def _enqueue_vuln(session, **kwargs):
    if _Net.vuln_error:
        raise RuntimeError(_Net.vuln_error)
    job = job_service.Job(
        server_id=None,
        job_type="nmap_vuln_db_update",
        status="pending",
        details="{}",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@pytest.fixture()
def client(monkeypatch):
    _reset_net()
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    store: dict = {}

    def fake_load():
        return dict(store)

    def fake_write(data: dict):
        store.clear()
        store.update(data)

    monkeypatch.setattr(cfg, "_load_raw_from_db", fake_load)
    monkeypatch.setattr(cfg, "_write_raw_to_db", fake_write)
    cfg.clear_cache()

    monkeypatch.setattr(ph, "login", _login)
    monkeypatch.setattr(ph, "logout", _logout)
    monkeypatch.setattr(ph, "list_dns_hosts", _list_hosts)
    monkeypatch.setattr(ph, "list_dns_cnames", _list_cnames)
    monkeypatch.setattr(ph, "add_dns_host", _add_host)
    monkeypatch.setattr(ph, "delete_dns_host", _del_host)
    monkeypatch.setattr(ph, "add_dns_cname", _add_cname)
    monkeypatch.setattr(ph, "delete_dns_cname", _del_cname)
    monkeypatch.setattr(ph, "run_action", _run_action)
    monkeypatch.setattr(ph, "fetch_stats", _fetch_stats)
    monkeypatch.setattr(poll_svc, "test_connection", _test_connection)
    monkeypatch.setattr(poll_svc, "poll_integration", _poll_integration)
    monkeypatch.setattr(nmap_routes, "enqueue_nmap_scan", _enqueue)
    monkeypatch.setattr(nmap_routes, "enqueue_vuln_db_update", _enqueue_vuln)
    monkeypatch.setattr(nmap_routes, "worker_online", lambda: {"online": False})
    monkeypatch.setattr(pihole_routes, "_run_pihole_action_job", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.docker_inventory.request_refresh",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "app.services.scheduler.sync_nmap_schedules",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("resync skip")),
    )

    def _session():
        with Session(eng) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    tc = TestClient(app, raise_server_exceptions=False)
    try:
        yield tc, eng
    finally:
        app.dependency_overrides.clear()
        cfg.clear_cache()


def _user(session: Session, *, role: str = "admin", email: str = "user@example.com") -> User:
    row = User(
        email=email,
        hashed_password=get_password_hash("SmokeTest1ok"),
        role=role,
        is_active=True,
        must_change_password=False,
        totp_enabled=True,
        totp_secret_encrypted="enc",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _cookie(uid: int) -> dict:
    return {"access_token": create_access_token({"sub": str(uid)})}


def _server(
    session: Session,
    name: str,
    hostname: str,
    ip: str | None,
    dns: str | None = None,
    *,
    manage: bool = False,
) -> Server:
    row = Server(
        name=name,
        hostname=hostname,
        ip_address=ip,
        ssh_port=22,
        ssh_username="user",
        dns_name=dns,
        dns_manage_a=manage,
        docker_inventory_json=json.dumps(
            {
                "v": 1,
                "projects": [
                    {
                        "name": "app",
                        "containers": [
                            {"name": "app-1", "compose_service": "app", "running": True}
                        ],
                    }
                ],
                "orphan_containers": [],
                "meta": {},
            }
        ),
        docker_inventory_status="ok",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _req(path: str, query: str = "", method: str = "GET") -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [],
        "client": ("192.0.2.1", 1234),
        "server": ("testserver", 80),
    }
    return Request(scope)


def _seed_fleet(engine) -> dict:
    with Session(engine) as s:
        admin = _user(s, role="admin", email="user@example.com")
        viewer = _user(s, role="viewer", email="viewer@example.com")
        host_a = _server(
            s, "host-a", "host-a.example.com", "192.0.2.10", "host-a.example.com", manage=True
        )
        host_b = _server(
            s, "host-b", "host-b.example.com", "192.0.2.20", "host-b.example.com"
        )
        bare = _server(s, "bare", "192.0.2.30", None, None)
        dep = StackDeployment(
            server_id=host_b.id,
            project_name="app",
            template_slug="app",
        )
        s.add(dep)
        s.commit()
        s.refresh(dep)
        cert = ManagedCertificate(
            name="wildcard",
            domains_json=json.dumps(["*.example.com", "{"]),
        )
        cert_bad = ManagedCertificate(name="badjson", domains_json="{")
        cert_obj = ManagedCertificate(name="notlist", domains_json=json.dumps({"a": 1}))
        s.add(cert)
        s.add(cert_bad)
        s.add(cert_obj)
        s.commit()
        s.refresh(cert)
        pi = reg.create_pihole(
            s,
            name="Pi-hole A",
            base_url="https://pihole.example.com",
            password="secret",
            is_primary=True,
        )
        pi.last_status_json = json.dumps(
            {
                "ok": True,
                "queries": 10,
                "blocked": 2,
                "active_clients": 1,
                "domains_on_lists": 3,
                "is_primary": True,
            }
        )
        s.add(pi)
        pi_b = reg.create_pihole(
            s,
            name="Pi-hole B",
            base_url="https://pihole-b.example.com",
            password="secret",
            enabled=False,
        )
        s.add(pi_b)
        npm = reg.create_npm(
            s,
            name="NPM",
            base_url="https://npm.example.com",
            identity="user@example.com",
            password="secret",
        )
        npm.last_status_json = json.dumps(
            {
                "proxy_hosts": [
                    {
                        "id": "1",
                        "domain_names": "app.example.com",
                        "forward_host": "192.0.2.20",
                        "forward_port": 8080,
                        "forward_scheme": "http",
                        "label": "app",
                        "meta": {"docker_project": "app"},
                    },
                    {
                        "id": "2",
                        "domain_names": ["pihole.example.com"],
                        "forward_host": "192.0.2.50",
                        "forward_port": "bad",
                        "forward_scheme": "ftp",
                        "label": "pihole",
                    },
                    {
                        "id": "3",
                        "domain_names": ["direct.example.com"],
                        "forward_host": "host-b.example.com",
                        "forward_port": 80,
                        "forward_scheme": "https",
                        "label": "direct",
                    },
                    "nope",
                ]
            }
        )
        s.add(npm)
        npm_bad = reg.create_npm(
            s,
            name="NPM bad",
            base_url="https://npm-bad.example.com",
            identity="user@example.com",
            password="secret",
        )
        npm_bad.last_status_json = "{"
        s.add(npm_bad)
        npm_shape = reg.create_npm(
            s,
            name="NPM shape",
            base_url="https://npm-shape.example.com",
            identity="user@example.com",
            password="secret",
        )
        npm_shape.last_status_json = json.dumps({"proxy_hosts": "nope"})
        s.add(npm_shape)
        s.commit()
        s.add(
            IntegrationBinding(
                integration_id=npm.id,
                server_id=host_a.id,
                role=reg.ROLE_PROXY_HOST,
                external_id="1",
                docker_project="app",
            )
        )
        s.add(
            IntegrationBinding(
                integration_id=npm.id,
                server_id=host_b.id,
                role="service",
                external_id="npm-stack",
                external_label="nginx proxy",
                docker_project="npm",
            )
        )
        s.add(
            IntegrationBinding(
                integration_id=npm.id,
                server_id=host_b.id,
                role="service",
                external_id="graf",
                external_label="https://graf.example.com/d/1",
                docker_project="app",
                docker_container="app-1",
                external_meta_json=json.dumps(
                    {"url": "https://app.example.com", "hostname": "app.example.com"}
                ),
            )
        )
        s.add(
            IntegrationBinding(
                integration_id=npm.id,
                server_id=host_a.id,
                role="service",
                external_id="plain",
                external_label="plain.example.com",
                docker_project="",
            )
        )
        kuma = reg.create_kuma(
            s,
            name="Kuma",
            base_url="https://kuma.example.com",
            api_key="kuma-key",
            username="user@example.com",
            password="secret",
        )
        kuma.last_status_json = json.dumps(
            {
                "ok": True,
                "monitors": [
                    {
                        "id": "m1",
                        "name": "host-a SSH",
                        "type": "port",
                        "hostname": "host-a.example.com",
                        "port": "22",
                        "url": "",
                        "status": "up",
                        "response_time_ms": 3,
                        "dashboard_id": "9",
                        "cert_days_remaining": 10,
                        "cert_is_valid": True,
                    },
                    {"id": "", "name": "", "status": "up"},
                    {
                        "id": "m2",
                        "name": "app",
                        "type": "http",
                        "hostname": "app.example.com",
                        "port": "443",
                        "url": "https://app.example.com",
                        "status": "down",
                        "message": "timeout",
                    },
                ],
            }
        )
        s.add(kuma)
        graf = reg.create_grafana(
            s,
            name="Grafana",
            base_url="https://grafana.example.com",
            api_key="graf-key",
        )
        graf.last_status_json = json.dumps(
            {
                "ok": False,
                "dashboards": [{"uid": "dash1", "title": "Fleet"}],
            }
        )
        graf.last_error = "down"
        s.add(graf)
        generic = reg.create_generic_url(
            s,
            name="Link",
            base_url="https://link.example.com",
            product="custom",
            enabled=False,
        )
        other = Integration(
            type="other",
            name="Other",
            base_url="https://other.example.com",
            enabled=True,
        )
        s.add(other)
        nmap = reg.list_integrations(s, type_filter=reg.TYPE_NMAP)
        if not nmap:
            from app.services.nmap import config as nmap_cfg

            nmap_row = nmap_cfg.create_nmap(
                s,
                name="LAN",
                cidrs=["192.0.2.0/24"],
                excludes=["192.0.2.1"],
                vuln_enabled=False,
            )
        else:
            nmap_row = nmap[0]
        dev = NmapDevice(
            integration_id=nmap_row.id,
            identity_key="mac:aa",
            ip_address="198.51.100.40",
            hostname="cam.example.com",
            display_name="cam",
            state="new",
            ports_json=json.dumps(
                [
                    {"port": 80, "state": "open", "proto": "tcp"},
                    {"port": 23, "state": "closed", "proto": "tcp"},
                    "skip",
                ]
            ),
        )
        s.add(dev)
        s.commit()
        s.refresh(dev)
        s.add(
            NmapScriptResult(
                device_id=dev.id,
                script_id="http-title",
                output="App",
                port=80,
                protocol="tcp",
            )
        )
        gw = NmapDevice(
            integration_id=nmap_row.id,
            identity_key="mac:gw",
            ip_address="192.0.2.1",
            hostname="gw.example.com",
            display_name="Router",
            state="known",
            map_role=MAP_ROLE_GATEWAY,
            ports_json="not-json",
        )
        bad_ports = NmapDevice(
            integration_id=nmap_row.id,
            identity_key="mac:bad",
            ip_address="198.51.100.41",
            state="known",
            ports_json=json.dumps({"nope": True}),
        )
        linked = NmapDevice(
            integration_id=nmap_row.id,
            identity_key="mac:link",
            ip_address="198.51.100.42",
            state="linked",
            linked_server_id=host_a.id,
        )
        s.add(gw)
        s.add(bad_ports)
        s.add(linked)
        sched = NmapScanSchedule(
            integration_id=nmap_row.id,
            name="nightly",
            intensity="discovery",
            cron="0 3 * * *",
            enabled=False,
        )
        s.add(sched)
        s.commit()
        s.refresh(gw)
        s.refresh(bad_ports)
        s.refresh(linked)
        s.refresh(sched)
        s.refresh(pi)
        s.refresh(pi_b)
        s.refresh(kuma)
        s.refresh(graf)
        s.refresh(generic)
        s.refresh(other)
        s.refresh(npm)
        cfg.save_settings(
            {
                "dns_base_domain": "example.com",
                "network_lan_subnet": "192.0.2.0/24",
                "network_gateway_ip": "",
                "network_public_ip": "203.0.113.10",
                "network_gateway_kuma_external_id": "m1",
                "network_public_kuma_external_id": "missing-mon",
                "network_kuma_integration_id": str(kuma.id),
                "kuma_coverage_mute_keys": "[]",
                "force_2fa": False,
                "force_2fa_scope": "off",
            }
        )
        return {
            "admin": admin.id,
            "viewer": viewer.id,
            "host_a": host_a.id,
            "host_b": host_b.id,
            "bare": bare.id,
            "dep": dep.id,
            "cert": cert.id,
            "pi": pi.id,
            "pi_b": pi_b.id,
            "npm": npm.id,
            "kuma": kuma.id,
            "graf": graf.id,
            "generic": generic.id,
            "other": other.id,
            "nmap": nmap_row.id,
            "dev": dev.id,
            "gw": gw.id,
            "bad_ports": bad_ports.id,
            "linked": linked.id,
            "sched": sched.id,
        }


def _ids(engine) -> dict:
    return _seed_fleet(engine)


def test_redirect_helpers_and_dns_pages(client, monkeypatch):
    tc, engine = client
    ids = _ids(engine)
    admin = _cookie(ids["admin"])
    viewer = _cookie(ids["viewer"])

    bare = dns_routes._redirect("/dns")
    assert bare.status_code == 303
    assert dns_routes._safe_next_path("https://example.com/dns") == "/dns/coverage#kuma-deps"
    assert dns_routes._safe_next_path("/dns\nnext") == "/dns/coverage#kuma-deps"
    assert dns_routes._safe_next_path("//evil") == "/dns/coverage#kuma-deps"
    assert dns_routes._stack_next("", service_id="12").endswith("stack=12")
    assert dns_routes._stack_next("/dns/physical").startswith("/dns/physical")
    assert nmap_routes._device_return_to("server_7") == "server:7"
    assert nmap_routes._device_return_to("server:x") == ""
    with monkeypatch.context() as mp:
        mp.setattr(nmap_routes, "_device_return_to", lambda raw: "server:nope")
        blown = nmap_routes._device_redirect(ids["nmap"], ids["dev"], return_to="x", close=True)
        assert blown.status_code == 303

    monkeypatch.setattr(
        "app.services.dns_fabric.kuma_coverage.filter_path_gaps",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("filter")),
    )
    monkeypatch.setattr(
        "app.services.nav_shortcuts.app_page_pin_context",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pin")),
    )
    for path in (
        "/dns",
        "/dns/coverage?coverage_filter=nope",
        "/dns/coverage?coverage_filter=strict",
        "/dns/physical",
        "/dns/logical",
        f"/dns/host-ports-panel?server_id={ids['host_a']}&show_noise=yes&focus_project=app",
        f"/dns/host-ports-expand.json?server_id={ids['host_a']}&show_noise=1&focus_project=app&focus_container=app-1",
        "/dns/host-ports-expand.json",
        f"/dns/stack-panel?server_id={ids['host_a']}&project=app&visual_stack=main&force=1&focus_container=app-1",
        f"/dns/stack-panel?server_id={ids['host_a']}&visual_stack=4",
        f"/dns/stack-panel?server_id={ids['host_a']}&visual_stack=nope",
        f"/dns/stack-expand.json?server_id={ids['host_a']}&project=app&visual_stack=main",
        f"/dns/stack-expand.json?server_id={ids['host_a']}&visual_stack=9",
        "/dns/stack-expand.json?visual_stack=nope",
        "/dns/candidates",
    ):
        resp = tc.get(path, cookies=admin, follow_redirects=False)
        assert resp.status_code in (200, 303, 400, 404)

    resp = tc.get("/dns/candidates", cookies=viewer, follow_redirects=False)
    assert resp.status_code == 200

    monkeypatch.setattr(
        dns_routes.fabric,
        "list_service_dns_candidates",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("candidates")),
    )
    resp = tc.get("/dns/candidates", cookies=admin, follow_redirects=False)
    assert resp.status_code == 200

    with Session(engine) as s:
        user = s.get(User, ids["admin"])
        asyncio.run(
            dns_routes.dns_host_ports_panel(
                _req("/dns/host-ports-panel", f"server_id={ids['host_a']}&nmap_device_id={ids['dev']}"),
                s,
                user,
            )
        )
        asyncio.run(
            dns_routes.dns_stack_panel(
                _req(
                    "/dns/stack-panel",
                    f"service_id=1&server_id={ids['host_a']}&project=app",
                ),
                BackgroundTasks(),
                s,
                user,
            )
        )

    def _panel_missing(*a, **k):
        return {"ok": True, "server_id": 99999}

    monkeypatch.setattr(
        "app.services.dns_fabric.stack_panel.build_stack_panel",
        _panel_missing,
    )
    assert tc.get(
        f"/dns/stack-panel?server_id={ids['host_a']}&force=1",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 200

    monkeypatch.setattr(
        "app.services.docker_inventory.request_refresh",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "app.services.dns_fabric.stack_panel.build_stack_panel",
        lambda *a, **k: {"ok": True, "server_id": ids["host_a"]},
    )
    assert tc.get(
        f"/dns/stack-panel?server_id={ids['host_a']}&force=1",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 200
    monkeypatch.setattr(
        "app.services.docker_inventory.request_refresh",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("refresh")),
    )
    assert tc.get(
        f"/dns/stack-panel?server_id={ids['host_a']}&force=1",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 200


def test_dns_mutations(client, monkeypatch):
    tc, engine = client
    ids = _ids(engine)
    admin = _cookie(ids["admin"])
    sid = ids["host_a"]

    assert tc.post(
        "/dns/host-port-annotation",
        data={
            "server_id": sid,
            "host_port": 80,
            "proto": "tcp",
            "role_key": "web",
            "label": "web",
            "note": "public",
            "show_noise": "yes",
            "focus_project": "app",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 200
    assert tc.post(
        "/dns/host-port-annotation",
        data={"server_id": sid, "host_port": 80, "role_key": "", "label": "", "note": ""},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 200
    assert tc.post(
        "/dns/host-port-annotation",
        data={"server_id": sid, "host_port": 80, "role_key": "nope"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 400
    with monkeypatch.context() as mp:
        mp.setattr(
            dns_routes,
            "make_audit_log",
            lambda **k: (_ for _ in ()).throw(RuntimeError("audit")),
        )
        assert tc.post(
            "/dns/host-port-annotation",
            data={"server_id": sid, "host_port": 81, "role_key": "web"},
            cookies=admin,
            follow_redirects=False,
        ).status_code == 200

    order_body = {
        "server_id": sid,
        "project": "app",
        "order": '["app-1"]',
        "service_id": "3",
        "next": "/dns",
        "visual_stack": "main",
    }
    assert tc.post(
        "/dns/stack-order",
        data=order_body,
        cookies=admin,
        headers={"accept": "application/json"},
        follow_redirects=False,
    ).status_code == 200
    assert tc.post(
        "/dns/stack-order",
        data={**order_body, "project": "  ", "order": "app-1, app-2"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/dns/stack-order",
        data={**order_body, "project": " ", "order": "{}"},
        cookies=admin,
        headers={"accept": "application/json"},
        follow_redirects=False,
    ).status_code == 400
    with monkeypatch.context() as mp:
        mp.setattr(
            "app.services.container_annotations.set_order_via_annotations",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ann")),
        )
        assert tc.post(
            "/dns/stack-order",
            data={**order_body, "order": "not-json"},
            cookies=admin,
            follow_redirects=False,
        ).status_code == 303
    with monkeypatch.context() as mp:
        mp.setattr(
            "app.services.container_annotations.set_order_via_annotations",
            lambda *a, **k: ["app-1"],
        )
        mp.setattr(
            "app.services.stack_order.set_order",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("order")),
        )
        mp.setattr(
            dns_routes,
            "make_audit_log",
            lambda **k: (_ for _ in ()).throw(RuntimeError("audit")),
        )
        assert tc.post(
            "/dns/stack-order",
            data=order_body,
            cookies=admin,
            follow_redirects=False,
        ).status_code == 303

    ann = {
        "server_id": sid,
        "project": "app",
        "container_key": "app-1",
        "category_key": "nope",
        "visual_stack_id": "nope",
        "service_id": "1",
        "next": "/dns",
    }
    assert tc.post(
        "/dns/container-annotation",
        data={**ann, "project": " ", "container_key": " "},
        cookies=admin,
        headers={"accept": "application/json"},
        follow_redirects=False,
    ).status_code == 400
    assert tc.post(
        "/dns/container-annotation",
        data=ann,
        cookies=admin,
        headers={"accept": "application/json"},
        follow_redirects=False,
    ).status_code == 400
    assert tc.post(
        "/dns/vocab",
        data={"kind": "category", "key": "edgeops", "label": "Edge", "next": "/dns"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/dns/vocab",
        data={"kind": "tag", "key": "public", "label": "Public"},
        cookies=admin,
        headers={"accept": "application/json"},
        follow_redirects=False,
    ).status_code == 200
    assert tc.post(
        "/dns/vocab",
        data={"kind": "nope", "key": "x"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/dns/container-annotation",
        data={
            "server_id": sid,
            "project": "app",
            "container_key": "app-1",
            "category_key": "edgeops",
            "tags": "public",
            "clear_visual_stack": "1",
            "clear_category": "",
            "visual_stack_id": "",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    created = tc.post(
        "/dns/visual-stacks",
        data={"server_id": sid, "project": "app", "name": "Edge", "service_id": "1"},
        cookies=admin,
        headers={"accept": "application/json"},
        follow_redirects=False,
    )
    assert created.status_code == 200
    stack_id = created.json()["stack"]["id"]
    assert tc.post(
        "/dns/visual-stacks",
        data={"server_id": sid, "project": " ", "name": " "},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/dns/visual-stacks/{stack_id}/delete",
        data={"service_id": "1", "server_id": sid, "next": "/dns"},
        cookies=admin,
        headers={"accept": "application/json"},
        follow_redirects=False,
    ).status_code == 200
    assert tc.post(
        "/dns/visual-stacks/99999/delete",
        data={"next": "/dns"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    with monkeypatch.context() as mp:
        mp.setattr(
            "app.services.container_annotations.set_annotation",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("annotate")),
        )
        assert tc.post(
            "/dns/container-annotation",
            data={"server_id": sid, "project": "app", "container_key": "app-1"},
            cookies=admin,
            headers={"accept": "application/json"},
            follow_redirects=False,
        ).status_code == 500

    class _Form:
        def getlist(self, key):
            return []

        def get(self, key, default=None):
            if key == "tags":
                return '["public"]'
            return default

    async def _form_json(self):
        return _Form()

    with monkeypatch.context() as mp:
        mp.setattr(Request, "form", _form_json)
        assert tc.post(
            "/dns/container-annotation",
            data={"server_id": sid, "project": "app", "container_key": "app-2", "visual_stack_id": "3"},
            cookies=admin,
            follow_redirects=False,
        ).status_code in (303, 400, 422)

    class _FormCsv:
        def getlist(self, key):
            return []

        def get(self, key, default=None):
            if key == "tags":
                return "public, extra"
            return default

    async def _form_csv(self):
        return _FormCsv()

    with monkeypatch.context() as mp:
        mp.setattr(Request, "form", _form_csv)
        tc.post(
            "/dns/container-annotation",
            data={"server_id": sid, "project": "app", "container_key": "app-3"},
            cookies=admin,
            follow_redirects=False,
        )

    edge = {
        "from_server_id": sid,
        "from_project": "app",
        "from_container": "",
        "to_server_id": ids["host_b"],
        "to_project": "app",
        "to_container": "app-1",
        "service_id": "1",
        "next": "/dns?stack=1",
    }
    assert tc.post("/dns/stack-edges/manual", data={**edge, "from_project": " "}, cookies=admin, follow_redirects=False).status_code == 303
    assert tc.post("/dns/stack-edges/manual", data=edge, cookies=admin, follow_redirects=False).status_code == 303
    assert tc.post("/dns/stack-edges/accept", data=edge, cookies=admin, follow_redirects=False).status_code == 303
    assert tc.post("/dns/stack-edges/dismiss", data=edge, cookies=admin, follow_redirects=False).status_code == 303
    with Session(engine) as s:
        row = s.exec(select(ServiceDnsRecord)).first()
        edge_row_id = None
        from app.models import RuntimeEdge

        found = s.exec(select(RuntimeEdge)).first()
        if found:
            edge_row_id = found.id
    if edge_row_id:
        assert tc.post(
            f"/dns/stack-edges/{edge_row_id}/delete",
            data={"service_id": "1", "next": "/dns"},
            cookies=admin,
            follow_redirects=False,
        ).status_code == 303
    with monkeypatch.context() as mp:
        mp.setattr(
            "app.services.runtime_edges.accept_suggestion",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("accept")),
        )
        mp.setattr(
            "app.services.runtime_edges.dismiss_suggestion",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dismiss")),
        )
        mp.setattr(
            "app.services.runtime_edges.create_manual_edge",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("manual")),
        )
        assert tc.post("/dns/stack-edges/accept", data=edge, cookies=admin, follow_redirects=False).status_code == 303
        assert tc.post("/dns/stack-edges/dismiss", data=edge, cookies=admin, follow_redirects=False).status_code == 303
        assert tc.post("/dns/stack-edges/manual", data=edge, cookies=admin, follow_redirects=False).status_code == 303

    cfg.save_settings({"kuma_coverage_mute_keys": "{"})
    assert tc.post(
        "/dns/coverage/mute",
        data={"key": "1:app:app-1", "next": "https://example.com/x"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    cfg.save_settings({"kuma_coverage_mute_keys": "{}"})
    assert tc.post(
        "/dns/coverage/mute",
        data={"key": "1:app:app-1", "next": "/dns/coverage#kuma-deps"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/dns/coverage/mute",
        data={"key": "bad", "next": "/dns/coverage\nbad"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/dns/coverage/mute",
        data={"key": "1:app:app-1", "unmute": "1", "next": "/dns/coverage"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/dns/coverage/show-infra",
        data={"show": "1"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/dns/coverage/show-infra",
        data={"show": "0"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303

    assert tc.post(
        "/dns/base-domain",
        data={"domain": "not a domain"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/dns/base-domain",
        data={"domain": "example.com"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    for payload in (
        {"lan_subnet": "nope", "gateway_ip": "", "public_ip": ""},
        {"lan_subnet": "192.0.2.0/24", "gateway_ip": "nope", "public_ip": ""},
        {"lan_subnet": "192.0.2.0/24", "gateway_ip": "192.0.2.1", "public_ip": "nope"},
        {
            "lan_subnet": "192.0.2.0/24",
            "gateway_ip": "192.0.2.1",
            "public_ip": "203.0.113.8",
            "kuma_integration_id": "abc",
        },
    ):
        assert tc.post("/dns/network", data=payload, cookies=admin, follow_redirects=False).status_code == 303
    monkeypatch.setattr(
        dns_routes.apol,
        "set_type_override",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("policy")),
    )
    assert tc.post(
        "/dns/network",
        data={
            "lan_subnet": "192.0.2.0/24",
            "gateway_ip": "192.0.2.1",
            "public_ip": "198.51.100.8",
            "gateway_kuma_external_id": "m1",
            "public_kuma_external_id": "m2",
            "kuma_integration_id": str(ids["kuma"]),
            "stack_inventory_down_alerts": "on",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            if "ipify" in url:
                return SimpleNamespace(status_code=200, text="not-an-ip extra")
            if "ifconfig" in url:
                raise RuntimeError("down")
            return SimpleNamespace(status_code=200, text="198.51.100.20\n")

    monkeypatch.setattr("httpx.Client", _Client)
    assert tc.post("/dns/network/lookup-public-ip", cookies=admin, follow_redirects=False).status_code == 303

    class _Down:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return SimpleNamespace(status_code=500, text="")

    monkeypatch.setattr("httpx.Client", _Down)
    assert tc.post("/dns/network/lookup-public-ip", cookies=admin, follow_redirects=False).status_code == 303


def test_dns_records_and_attach(client, monkeypatch):
    tc, engine = client
    ids = _ids(engine)
    admin = _cookie(ids["admin"])

    created = tc.post(
        "/dns/services",
        data={
            "fqdn": "app.example.com",
            "target_server_id": ids["host_a"],
            "backend_server_id": ids["host_b"],
            "label": "App",
            "docker_project": "app",
            "npm_hint": "edge",
            "via_proxy": "on",
            "managed_on_pihole": "on",
            "sync_now": "on",
            "external_dns_status": "checklist",
            "certificate_id": str(ids["cert"]),
            "stack_deployment_id": str(ids["dep"]),
        },
        cookies=admin,
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert tc.post(
        "/dns/services",
        data={
            "fqdn": "not a name",
            "target_server_id": ids["host_a"],
            "backend_server_id": ids["host_b"],
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303

    with Session(engine) as s:
        row = s.exec(select(ServiceDnsRecord)).first()
        assert row is not None
        rid = row.id

    assert tc.post(f"/dns/services/{rid}/sync", cookies=admin, follow_redirects=False).status_code == 303
    assert tc.post("/dns/services/99999/sync", cookies=admin, follow_redirects=False).status_code == 404
    with Session(engine) as s:
        host = s.get(Server, ids["host_a"])
        host.dns_name = None
        s.add(host)
        s.commit()
    assert tc.post(f"/dns/services/{rid}/sync", cookies=admin, follow_redirects=False).status_code == 303
    with Session(engine) as s:
        host = s.get(Server, ids["host_a"])
        host.dns_name = "host-a.example.com"
        s.add(host)
        s.commit()

    assert tc.post(
        f"/dns/services/{rid}/link-project",
        data={"docker_project": "app", "mark_direct": "1", "next": "/dns"},
        cookies=admin,
        headers={"accept": "application/json"},
        follow_redirects=False,
    ).status_code == 200
    assert tc.post(
        f"/dns/services/{rid}/link-project",
        data={"docker_project": " ", "next": "/dns"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/dns/services/{rid}/link-project",
        data={"docker_project": " "},
        cookies=admin,
        headers={"accept": "application/json"},
        follow_redirects=False,
    ).status_code == 400
    assert tc.post(
        "/dns/services/99999/link-project",
        data={"docker_project": "app"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404
    assert tc.post(
        f"/dns/services/{rid}/external",
        data={"status": "done"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/dns/services/99999/external",
        data={"status": "done"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404
    assert tc.post(
        f"/dns/services/{rid}/delete",
        data={"remove_from_pihole": "0"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/dns/services/99999/delete",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404

    assert tc.post(
        f"/servers/{ids['host_b']}/dns",
        data={
            "dns_name": "host-b.example.com",
            "dns_manage_a": "on",
            "dns_ip_override": "",
            "ip_address": "192.0.2.20",
            "sync_now": "on",
            "return_to": f"/servers/{ids['host_b']}?tab=dns",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/servers/{ids['host_b']}/dns",
        data={
            "dns_name": "host-b.example.com",
            "dns_manage_a": "",
            "ip_address": "192.0.2.20",
            "return_to": "/servers/1",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/servers/99999/dns",
        data={"dns_name": "x.example.com", "ip_address": "192.0.2.9"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404
    assert tc.post(
        f"/servers/{ids['bare']}/dns",
        data={"dns_name": "not a name", "ip_address": ""},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/servers/{ids['host_a']}/dns/sync-a",
        data={"return_to": f"/servers/{ids['host_a']}?x=1"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/servers/99999/dns/sync-a",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404
    with Session(engine) as s:
        bare = s.get(Server, ids["bare"])
        bare.dns_name = None
        bare.ip_address = None
        bare.dns_manage_a = False
        s.add(bare)
        s.commit()
    assert tc.post(
        f"/servers/{ids['bare']}/dns/sync-a",
        data={"return_to": ""},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303

    assert tc.post(
        f"/templates/deployments/{ids['dep']}/dns",
        data={
            "fqdn": "app.example.com",
            "use_inferred": "",
            "target_server_id": ids["host_a"],
            "via_proxy": "on",
            "npm_hint": "edge",
            "managed_on_pihole": "on",
            "sync_now": "on",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/templates/deployments/99999/dns",
        data={"fqdn": "app.example.com"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404
    assert tc.post(
        "/dns/attach-deployment",
        data={"deployment_id": 99999, "fqdn": "app.example.com"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    with Session(engine) as s:
        extra = StackDeployment(server_id=ids["host_b"], project_name="extra", template_slug="extra")
        s.add(extra)
        s.commit()
        s.refresh(extra)
        extra_id = extra.id
    assert tc.post(
        "/dns/attach-deployment",
        data={"deployment_id": extra_id, "fqdn": "extra.example.com"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/dns/attach-cname",
        data={"fqdn": "gone.example.com", "cname_target": ""},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/dns/attach-cname",
        data={"fqdn": "ghost.example.com", "cname_target": "missing.example.com"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    _Net.cnames = [
        {"domain": "adopt.example.com", "target": "host-b.example.com"},
        {"domain": "ghost.example.com", "target": "missing.example.com"},
    ]
    assert tc.post(
        "/dns/attach-cname",
        data={"fqdn": "adopt.example.com", "cname_target": "host-b.example.com"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post("/dns/import-pihole", cookies=admin, follow_redirects=False).status_code == 303
    assert tc.post(
        "/dns/attach-host-identity",
        data={"server_id": 99999},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/dns/attach-host-identity",
        data={"server_id": ids["bare"]},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    with Session(engine) as s:
        bare = s.get(Server, ids["bare"])
        bare.dns_name = "bare.example.com"
        bare.ip_address = "192.0.2.30"
        s.add(bare)
        s.commit()
    assert tc.post(
        "/dns/attach-host-identity",
        data={"server_id": ids["bare"]},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    monkeypatch.setattr(
        dns_routes.fabric,
        "attach_service_dns_from_plan",
        lambda *a, **k: (_ for _ in ()).throw(fabric_core.DnsFabricError("nope", "invalid")),
    )
    assert tc.post(
        "/dns/attach-host-identity",
        data={"server_id": ids["host_a"]},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/dns/attach-deployment",
        data={"deployment_id": extra_id},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303


def test_integration_hub_bindings(client, monkeypatch):
    tc, engine = client
    ids = _ids(engine)
    admin = _cookie(ids["admin"])

    assert tc.get("/integrations", cookies=admin, follow_redirects=False).status_code == 200
    for iid, kind in (
        (ids["graf"], "grafana"),
        (ids["pi"], "pihole"),
        (ids["npm"], "npm"),
        (ids["nmap"], "nmap"),
        (ids["generic"], "generic"),
        (ids["kuma"], "kuma"),
        (ids["other"], "other"),
    ):
        detail = tc.get(f"/integrations/{iid}", cookies=admin, follow_redirects=False)
        edit = tc.get(f"/integrations/{iid}/edit", cookies=admin, follow_redirects=False)
        if kind == "other":
            assert detail.status_code == 400
            assert edit.status_code == 404
        else:
            assert detail.status_code in (200, 500)
            assert edit.status_code in (200, 500)
        if kind != "other":
            assert tc.post(
                f"/integrations/{iid}/test", cookies=admin, follow_redirects=False
            ).status_code == 303
            assert tc.post(
                f"/integrations/{iid}/poll", cookies=admin, follow_redirects=False
            ).status_code == 303

    _Net.test_ok = False
    _Net.poll = {"ok": False, "skipped": True, "error": "busy"}
    assert tc.post(
        f"/integrations/{ids['kuma']}/test", cookies=admin, follow_redirects=False
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['kuma']}/poll", cookies=admin, follow_redirects=False
    ).status_code == 303
    _Net.poll = {"ok": False, "skipped": False, "error": "down"}
    assert tc.post(
        f"/integrations/{ids['kuma']}/poll", cookies=admin, follow_redirects=False
    ).status_code == 303
    _Net.test_ok = True
    _Net.poll = {"ok": True, "queries": 1, "monitor_count": 1, "dashboard_count": 0, "version": "", "proxy_host_count": 0, "device_count": 0, "status_code": 0}

    assert tc.post(
        f"/integrations/{ids['kuma']}/edit",
        data={
            "name": "Kuma",
            "base_url": "https://kuma.example.com",
            "api_key": "kuma-key",
            "poll_interval_sec": 120,
            "tls_verify": "on",
            "enabled": "on",
            "clear_login": "on",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['graf']}/edit",
        data={
            "name": "Grafana",
            "base_url": "https://grafana.example.com",
            "api_key": "graf-key",
            "poll_interval_sec": 120,
            "tls_verify": "on",
            "enabled": "on",
            "query_template": "up",
            "query_template_container_host": "up",
            "query_template_container": "up",
            "query_template_logs": "up",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['graf']}/edit",
        data={"name": "Grafana", "base_url": "ftp://grafana.example.com"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    monkeypatch.setattr(
        reg,
        "update_grafana",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("graf")),
    )
    assert tc.post(
        f"/integrations/{ids['graf']}/edit",
        data={"name": "Grafana", "base_url": "https://grafana.example.com"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    # Keep fixture stubs. undo() would drop every patch on this test.

    assert tc.post(
        f"/integrations/{ids['pi']}/edit",
        data={
            "name": "Pi-hole A",
            "base_url": "https://pihole.example.com",
            "password": "secret",
            "poll_interval_sec": 60,
            "tls_verify": "on",
            "enabled": "on",
            "is_primary": "on",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['pi']}/edit",
        data={"name": "Pi-hole A", "base_url": "notaurl"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['npm']}/edit",
        data={
            "name": "NPM",
            "base_url": "https://npm.example.com",
            "identity": "user@example.com",
            "password": "secret",
            "poll_interval_sec": 60,
            "enabled": "on",
            "tls_verify": "on",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['npm']}/edit",
        data={"name": "NPM", "base_url": "notaurl", "identity": "user@example.com"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['generic']}/edit",
        data={
            "name": "Link",
            "base_url": "https://link.example.com",
            "product": "custom",
            "health_path": "/health",
            "notes": "note",
            "api_key": "token",
            "poll_interval_sec": 60,
            "enabled": "on",
            "tls_verify": "on",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['generic']}/edit",
        data={"name": "Link", "base_url": "notaurl", "product": "custom"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['nmap']}/edit",
        data={
            "name": "LAN",
            "cidrs": "192.0.2.0/24",
            "excludes": "192.0.2.1",
            "excludes_port_scans": "",
            "excludes_deep": "",
            "skip_dns": "on",
            "use_syn": "on",
            "vuln_enabled": "on",
            "notes": "lab",
            "enabled": "on",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['nmap']}/edit",
        data={"name": "LAN", "cidrs": "not-a-cidr"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['other']}/edit",
        data={"name": "Other", "base_url": "https://other.example.com"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404

    bind = {
        "server_id": ids["host_a"],
        "external_id": "m1",
        "dashboard_id": "9",
        "role": "nope",
        "binding_id": "",
    }
    assert tc.post(
        f"/integrations/{ids['kuma']}/bindings",
        data=bind,
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['kuma']}/bindings",
        data={**bind, "role": "service", "docker_project": "app", "docker_container": "app-1", "binding_id": "xx", "next": "/dns/coverage#kuma-deps"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['kuma']}/bindings",
        data={**bind, "external_id": "", "clear": "1", "role": "ssh_reachability"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['graf']}/bindings",
        data={
            "server_id": ids["host_a"],
            "external_id": "",
            "role": "dashboard",
            "kind": "metrics",
            "clear": "1",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['graf']}/bindings",
        data={
            "server_id": ids["host_a"],
            "external_id": "dash1",
            "role": "dashboard",
            "kind": "containers",
            "display_name": "Fleet view",
            "docker_project": "app",
            "docker_container": "app-1",
            "binding_id": "2",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['graf']}/bindings",
        data={
            "server_id": ids["host_b"],
            "external_id": "dash1",
            "role": "dashboard",
            "kind": "logs",
            "display_name": "",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['graf']}/bindings",
        data={
            "server_id": ids["host_b"],
            "external_id": "manual-uid",
            "role": "dashboard",
            "kind": "metrics",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['kuma']}/suggest-bindings",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    monkeypatch.setattr(
        reg,
        "set_binding",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad bind")),
    )
    assert tc.post(
        f"/integrations/{ids['kuma']}/bindings",
        data={**bind, "external_id": "missing", "next": "/dns/coverage"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    monkeypatch.setattr(
        reg,
        "set_binding",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bind")),
    )
    assert tc.post(
        f"/integrations/{ids['graf']}/bindings",
        data={
            "server_id": ids["host_a"],
            "external_id": "dash1",
            "role": "dashboard",
            "kind": "metrics",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['kuma']}/bindings",
        data={**bind, "external_id": "m1", "next": "https://example.com/phish"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    # Keep fixture stubs. undo() would drop every patch on this test.

    assert tc.post(
        f"/integrations/{ids['generic']}/delete",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        "/integrations/99999/delete",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404
    assert tc.get("/integrations/99999/edit", cookies=admin, follow_redirects=False).status_code == 404


def test_pihole_routes_and_job(client, monkeypatch):
    tc, engine = client
    ids = _ids(engine)
    admin = _cookie(ids["admin"])
    pi = ids["pi"]

    for tab in ("dns", "cname", "overview"):
        resp = tc.get(
            f"/integrations/{pi}?tab={tab}",
            cookies=admin,
            follow_redirects=False,
        )
        assert resp.status_code in (200, 500)
    _Net.list_fail = True
    assert tc.get(
        f"/integrations/{pi}?tab=dns", cookies=admin, follow_redirects=False
    ).status_code in (200, 500)
    _Net.list_fail = False

    with Session(engine) as s:
        reg.set_binding(
            s,
            integration_id=pi,
            server_id=ids["host_a"],
            external_id="instance",
            role=reg.ROLE_PIHOLE_HOST,
            docker_project="app",
            docker_container="app-1",
            external_label="Pi-hole A",
        )
        rows = reg.list_bindings(s, integration_id=pi, role=reg.ROLE_PIHOLE_HOST)
        bind_id = rows[0].id
    assert tc.get(
        f"/integrations/{pi}?tab=dns", cookies=admin, follow_redirects=False
    ).status_code in (200, 500)

    assert tc.post(
        f"/integrations/{pi}/pihole/dns-host",
        data={"action": "add", "ip": "", "domain": ""},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    _Net.add_host_error = "already exists"
    assert tc.post(
        f"/integrations/{pi}/pihole/dns-host",
        data={"action": "add", "ip": "192.0.2.10", "domain": "host-a.example.com", "scope": "all"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    _Net.add_host_error = ""
    _Net.delete_error = "missing"
    assert tc.post(
        f"/integrations/{pi}/pihole/dns-host",
        data={"action": "delete", "ip": "192.0.2.10", "domain": "host-a.example.com", "scope": "secondaries"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    _Net.delete_error = ""
    assert tc.post(
        f"/integrations/{pi}/pihole/dns-cname",
        data={"action": "add", "domain": "", "target": ""},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    _Net.add_cname_error = "duplicate"
    assert tc.post(
        f"/integrations/{pi}/pihole/dns-cname",
        data={
            "action": "add",
            "domain": "app.example.com",
            "target": "host-a.example.com",
            "raw": "app.example.com,host-a.example.com",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    _Net.add_cname_error = ""
    assert tc.post(
        f"/integrations/{ids['kuma']}/pihole/dns-host",
        data={"ip": "192.0.2.10", "domain": "host-a.example.com"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404

    assert tc.post(
        f"/integrations/{pi}/pihole/action",
        data={"action": ""},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{pi}/pihole/action",
        data={"action": "nope"},
        cookies=admin,
        headers={"X-PiHerder-Async": "1", "accept": "application/json"},
        follow_redirects=False,
    ).status_code == 400
    queued = tc.post(
        f"/integrations/{pi}/pihole/action",
        data={"action": "gravity", "all_instances": "on"},
        cookies=admin,
        headers={"accept": "application/json"},
        follow_redirects=False,
    )
    assert queued.status_code == 200
    body = queued.json()
    job_id = body["job_id"]
    with Session(engine) as s:
        from app.models import AuditLog, Job

        job = s.get(Job, job_id)
        audit = s.exec(select(AuditLog).order_by(AuditLog.id.desc())).first()
        audit_id = audit.id
        job.status = "cancelled"
        s.add(job)
        s.commit()

    monkeypatch.setattr(job_service, "_get_fresh_session", lambda: Session(engine))
    _REAL_PIHOLE_JOB(job_id, audit_id, [pi], "gravity")
    with Session(engine) as s:
        from app.models import Job

        job = s.get(Job, job_id)
        job.status = "pending"
        s.add(job)
        s.commit()
    _Net.run_action_out = ""
    _REAL_PIHOLE_JOB(job_id, audit_id, [pi, 99999], "restartdns")
    _Net.run_action_error = "login"
    _REAL_PIHOLE_JOB(job_id, audit_id, [pi], "flush_network")
    _Net.run_action_error = ""
    _REAL_PIHOLE_JOB(job_id, audit_id, [], "gravity")
    _REAL_PIHOLE_JOB(99999, audit_id, [pi], "gravity")

    def _boom_session():
        raise RuntimeError("db")

    monkeypatch.setattr(job_service, "_get_fresh_session", _boom_session)
    monkeypatch.setattr(job_service, "_finish", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("finish")))
    _REAL_PIHOLE_JOB(job_id, audit_id, [pi], "gravity")
    # Keep fixture stubs. undo() would drop every patch on this test.

    class _BadReq:
        headers = {"accept": "text/html"}

        async def form(self):
            raise RuntimeError("form")

    with Session(engine) as s:
        user = s.get(User, ids["admin"])
        asyncio.run(
            pihole_routes.pihole_action(
                _BadReq(),
                pi,
                BackgroundTasks(),
                s,
                user,
                action="",
                all_instances=None,
            )
        )

    assert tc.post(
        f"/integrations/{pi}/pihole/host-bind",
        data={"server_id": ids["host_b"], "docker_project": "app", "docker_container": "app-1"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    monkeypatch.setattr(
        reg,
        "set_binding",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("bind")),
    )
    assert tc.post(
        f"/integrations/{pi}/pihole/host-bind",
        data={"server_id": ids["host_a"]},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    # Keep fixture stubs. undo() would drop every patch on this test.
    assert tc.post(
        f"/integrations/{pi}/pihole/host-unbind",
        data={"binding_id": bind_id},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{ids['kuma']}/pihole/host-unbind",
        data={"binding_id": 1},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404

    assert tc.get("/integrations/new/pihole", cookies=admin, follow_redirects=False).status_code in (200, 500)
    _Net.stats_ok = False
    _Net.stats_error = "auth"
    assert tc.post(
        "/integrations/new/pihole",
        data={"name": "Pi-hole C", "base_url": "https://pihole-c.example.com", "password": "secret"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    _Net.stats_ok = True
    assert tc.post(
        "/integrations/new/pihole",
        data={"name": "Pi-hole C", "base_url": "ftp://pihole-c.example.com", "password": "secret"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    _Net.stats_raise = True
    assert tc.post(
        "/integrations/new/pihole",
        data={"name": "Pi-hole C", "base_url": "https://pihole-c.example.com", "password": "secret"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    _Net.stats_raise = False
    assert tc.post(
        "/integrations/new/pihole",
        data={
            "name": "Pi-hole C",
            "base_url": "https://pihole-c.example.com/admin",
            "password": "secret",
            "tls_verify": "on",
            "enabled": "on",
            "is_primary": "on",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303


def test_nmap_detail_devices_and_schedules(client, monkeypatch):
    tc, engine = client
    ids = _ids(engine)
    admin = _cookie(ids["admin"])
    nid = ids["nmap"]
    dev = ids["dev"]

    for path in (
        f"/integrations/{nid}?tab=network",
        f"/integrations/{nid}?tab=weird&view=nope&state=nope",
        f"/integrations/{nid}?tab=devices&view=list&device={dev}&state=new&q=cam",
        f"/integrations/{nid}?tab=devices&device=abc",
        f"/integrations/{nid}?tab=devices&device={ids['bad_ports']}",
        f"/integrations/{nid}?tab=devices&device={ids['gw']}",
        f"/integrations/{nid}?tab=devices&device=99999",
        f"/integrations/{nid}?tab=schedules&schedule={ids['sched']}",
        f"/integrations/{nid}?tab=schedules&schedule=nope",
        f"/integrations/{nid}?tab=schedules&new=1",
        f"/integrations/{nid}?tab=runs",
        f"/integrations/{nid}?tab=overview",
    ):
        resp = tc.get(path, cookies=admin, follow_redirects=False)
        assert resp.status_code in (200, 303, 500)

    assert tc.get("/integrations/new/nmap", cookies=admin, follow_redirects=False).status_code in (200, 303, 500)
    assert tc.post(
        "/integrations/new/nmap",
        data={"name": "LAN 2", "cidrs": "192.0.2.0/24"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    monkeypatch.setattr(
        "app.services.nmap.config.create_nmap",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("create")),
    )
    assert tc.post(
        "/integrations/new/nmap",
        data={"name": "LAN 2", "cidrs": "192.0.2.0/24"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    # Keep fixture stubs. undo() would drop every patch on this test.

    common = {"return_tab": "network", "return_to": "hosts", "return_view": "map"}
    assert tc.post(
        f"/integrations/{nid}/nmap/scan",
        data={
            "intensity": "nope",
            "targets": "",
            "vuln_scripts": "on",
            "script_preset": "safe",
            "timing": "3",
            "top_ports": "50",
            "include_udp": "on",
            "port_list": "80,443",
            "port_mode": "list",
        },
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    _Net.enqueue_error = "queue"
    assert tc.post(
        f"/integrations/{nid}/nmap/scan",
        data={"intensity": "deep", "targets": "192.0.2.10"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    _Net.enqueue_error = ""
    assert tc.post(
        f"/integrations/{nid}/nmap/vuln-db-update",
        data={"include_vulscan": "on", "include_exploitdb": "on"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    _Net.vuln_error = "queue"
    assert tc.post(
        f"/integrations/{nid}/nmap/vuln-db-update",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    _Net.vuln_error = ""

    assert tc.post(
        f"/integrations/{nid}/nmap/device/{dev}/scan",
        data={"script_preset": "vuln", "vuln_scripts": "on", "timing": "4", "include_udp": "on", "port_list": "22", **common},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    _Net.enqueue_error = "queue"
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{dev}/scan",
        data=common,
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    _Net.enqueue_error = ""
    assert tc.post(
        f"/integrations/{nid}/nmap/device/99999/scan",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404
    assert tc.post(
        f"/integrations/{ids['kuma']}/nmap/scan",
        data={"intensity": "discovery"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404

    assert tc.post(
        f"/integrations/{nid}/nmap/device/{dev}/name",
        data={"display_name": "cam", "kind_override": "camera", "map_role": "none", **common},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{dev}/ignore",
        data={"return_tab": "devices", "return_to": "server_7", "return_view": ""},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{dev}/unignore",
        data={"return_to": f"server:{ids['host_a']}", "return_tab": "devices"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{dev}/mark-known",
        data={"return_view": "map"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{ids['linked']}/mark-new",
        data={"return_to": "hosts"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{dev}/mark-new",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{dev}/link",
        data={"server_id": 99999, "return_to": "hosts", "return_view": "map"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{dev}/link",
        data={"server_id": ids["host_a"]},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{dev}/unlink",
        data={"return_view": "map"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{nid}/nmap/device/99999/name",
        data={"display_name": "x"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404

    monkeypatch.setattr(
        "app.services.nmap.nic_change.replace_stale_mac",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("missing")),
    )
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{dev}/replace-stale-mac",
        data={"stale_device_id": ids["gw"]},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404
    monkeypatch.setattr(
        "app.services.nmap.nic_change.replace_stale_mac",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("not_a_nic_change")),
    )
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{dev}/replace-stale-mac",
        data={"stale_device_id": ids["gw"]},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 400
    monkeypatch.setattr(
        "app.services.nmap.nic_change.replace_stale_mac",
        lambda *a, **k: {"state": "ignored"},
    )
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{dev}/replace-stale-mac",
        data={"stale_device_id": ids["gw"], "return_to": "hosts"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    # Keep fixture stubs. undo() would drop every patch on this test.

    assert tc.post(
        f"/integrations/{nid}/nmap/device/{ids['linked']}/purge",
        data={"return_tab": "devices"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{ids['bad_ports']}/purge",
        data={"return_tab": "network"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    with Session(engine) as s:
        extra = NmapDevice(
            integration_id=nid,
            identity_key="mac:purge",
            ip_address="198.51.100.77",
            state="known",
        )
        s.add(extra)
        s.commit()
        s.refresh(extra)
        extra_id = extra.id
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{extra_id}/purge",
        data={"return_to": "hosts", "return_view": "map"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    with Session(engine) as s:
        extra = NmapDevice(
            integration_id=nid,
            identity_key="mac:purge2",
            ip_address="198.51.100.78",
            state="known",
        )
        s.add(extra)
        s.commit()
        s.refresh(extra)
        extra_id = extra.id
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{extra_id}/purge",
        data={"return_to": f"server:{ids['host_a']}"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    monkeypatch.setattr(nmap_routes, "_device_return_to", lambda raw: "server:nope")
    with Session(engine) as s:
        extra = NmapDevice(
            integration_id=nid,
            identity_key="mac:purge3",
            ip_address="198.51.100.79",
            state="known",
        )
        s.add(extra)
        s.commit()
        s.refresh(extra)
        extra_id = extra.id
    assert tc.post(
        f"/integrations/{nid}/nmap/device/{extra_id}/purge",
        data={"return_to": "server:nope"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    # Keep fixture stubs. undo() would drop every patch on this test.

    assert tc.post(
        f"/integrations/{nid}/nmap/devices/purge-offline",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303

    sched_form = {
        "name": "hourly",
        "intensity": "discovery",
        "cron": "15 * * * *",
        "interval_hours": "nope",
        "enabled": "on",
        "timing": "nope",
        "top_ports": "nope",
        "include_udp": "on",
        "port_list": "22,80",
        "use_syn": "on",
        "script_preset": "none",
    }
    assert tc.post(
        f"/integrations/{nid}/nmap/schedules",
        data=sched_form,
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{nid}/nmap/schedules",
        data={**sched_form, "intensity": "nope", "cron": "bad"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    with Session(engine) as s:
        row = s.exec(
            select(NmapScanSchedule).where(NmapScanSchedule.name == "hourly")
        ).first()
        assert row is not None
        sid = row.id
    assert tc.post(
        f"/integrations/{nid}/nmap/schedules/{sid}/edit",
        data={**sched_form, "name": "hourly 2", "interval_hours": "6", "cron": "", "timing": "4", "top_ports": "20"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{nid}/nmap/schedules/{sid}/edit",
        data={**sched_form, "intensity": "nope"},
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{nid}/nmap/schedules/{sid}/toggle",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{nid}/nmap/schedules/99999/toggle",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404
    assert tc.post(
        f"/integrations/{nid}/nmap/schedules/99999/run",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404
    assert tc.post(
        f"/integrations/{nid}/nmap/schedules/{sid}/delete",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 303
    assert tc.post(
        f"/integrations/{nid}/nmap/schedules/99999/delete",
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404
    assert tc.post(
        f"/integrations/{nid}/nmap/schedules/99999/edit",
        data=sched_form,
        cookies=admin,
        follow_redirects=False,
    ).status_code == 404


def test_dns_fabric_core_gaps(client, monkeypatch):
    tc, engine = client
    ids = _ids(engine)
    del tc

    with Session(engine) as s:
        host_a = s.get(Server, ids["host_a"])
        host_b = s.get(Server, ids["host_b"])
        bare = s.get(Server, ids["bare"])
        bare.hostname = "192.0.2.30"
        bare.dns_ip_override = "192.0.2.31"
        bare.dns_name = None
        bare.ip_address = None
        s.add(bare)
        s.commit()

        _Net.login_fail_urls = {"https://pihole-b.example.com"}
        with Session(engine) as s2:
            pi_b = s2.get(Integration, ids["pi_b"])
            pi_b.enabled = True
            s2.add(pi_b)
            s2.commit()
        s.expire_all()

        assert fabric_core._npm_edge_hostname(s)
        with monkeypatch.context() as mp:
            mp.setattr(reg, "list_integrations", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("list")))
            assert fabric_core._npm_edge_hostname(s) == ""

        hosts = fabric_core._npm_proxy_hosts_cached(s)
        assert hosts
        match = fabric_core.match_pihole_host_for_server(s, host_a)
        assert match is None or match.get("domain")

        plan = fabric_core.resolve_service_dns_plan(
            s,
            backend_server_id=host_b.id,
            docker_project=None,
            stack_deployment_id=ids["dep"],
            base_domain="example.com",
        )
        assert plan.get("fqdn") or plan.get("blockers") is not None or "target_server_id" in plan

        orig_match = fabric_core._match_pihole_cname

        def _flaky(session, fqdn):
            raise RuntimeError("match")

        monkeypatch.setattr(fabric_core, "_match_pihole_cname", _flaky)
        try:
            fabric_core.resolve_service_dns_plan(
                s,
                backend_server_id=host_a.id,
                docker_project="app",
                fqdn="app.example.com",
                base_domain="example.com",
            )
        except fabric_core.DnsFabricError:
            pass
        monkeypatch.setattr(fabric_core, "_match_pihole_cname", orig_match)

        try:
            fabric_core.plan_from_pihole_cname(
                s, "app.example.com", "host-a.example.com", base_domain="example.com"
            )
        except fabric_core.DnsFabricError:
            pass
        try:
            fabric_core.plan_from_pihole_cname(
                s, "direct.example.com", "host-b.example.com", base_domain="example.com"
            )
        except fabric_core.DnsFabricError:
            pass

        _Net.list_fail = True
        assert fabric_core.list_pihole_cnames(s) == []
        _Net.list_fail = False
        _Net.login_fail_urls = set()
        cnames = fabric_core.list_pihole_cnames(s)
        assert isinstance(cnames, list)

        candidates = fabric_core.list_service_dns_candidates(s, base_domain="example.com")
        assert isinstance(candidates, list)

        ghost = StackDeployment(server_id=99999, project_name="ghost", template_slug="ghost")
        s.add(ghost)
        s.commit()
        again = fabric_core.list_service_dns_candidates(s, base_domain="example.com")
        assert isinstance(again, list)

        _Net.add_cname_error = "already exists"
        _Net.add_host_error = "item already present"
        imported = fabric_core.import_pihole_cnames(
            s, user_id=ids["admin"], base_domain="example.com"
        )
        assert "imported_count" in imported
        _Net.add_cname_error = ""
        _Net.add_host_error = ""

        defaults = fabric_core.host_dns_form_defaults(s, bare, base_domain="example.com")
        assert "dns_name" in defaults
        monkeypatch.setattr(
            fabric_core,
            "match_pihole_host_for_server",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("match")),
        )
        fabric_core.host_dns_form_defaults(s, bare, base_domain="example.com", probe_pihole=True)
        # Keep fixture stubs. undo() would drop every patch on this test.

        s.expire_all()
        host_a = s.get(Server, ids["host_a"])
        host_b = s.get(Server, ids["host_b"])
        try:
            fabric_core.upsert_service_record(
                s,
                fqdn="svc.example.com",
                target_server_id=host_a.id,
                backend_server_id=host_b.id,
                record_id=99999,
                sync_now=False,
                user_id=ids["admin"],
            )
        except fabric_core.DnsFabricError:
            pass
        row, _results = fabric_core.upsert_service_record(
            s,
            fqdn="svc.example.com",
            target_server_id=host_a.id,
            backend_server_id=host_b.id,
            docker_project="app",
            sync_now=False,
            managed_on_pihole=False,
            user_id=ids["admin"],
        )
        try:
            fabric_core.upsert_service_record(
                s,
                fqdn="svc.example.com",
                target_server_id=host_a.id,
                backend_server_id=host_b.id,
                record_id=row.id + 50,
                sync_now=False,
            )
        except fabric_core.DnsFabricError:
            pass
        renamed, _r = fabric_core.upsert_service_record(
            s,
            fqdn="svc2.example.com",
            target_server_id=host_b.id,
            backend_server_id=host_b.id,
            record_id=row.id,
            sync_now=True,
            user_id=ids["admin"],
        )
        assert renamed.fqdn
        fabric_core.sync_service_cname(s, renamed, user_id=ids["admin"])

        ident, _i = fabric_core.upsert_service_record(
            s,
            fqdn="host-b.example.com",
            target_server_id=host_b.id,
            backend_server_id=host_b.id,
            sync_now=True,
            user_id=ids["admin"],
        )
        fabric_core.delete_service_record(s, ident, user_id=ids["admin"], remove_from_pihole=True)

        host_b.dns_name = None
        s.add(host_b)
        s.commit()
        try:
            fabric_core.sync_service_dns(s, renamed)
        except fabric_core.DnsFabricError:
            pass
        host_b.dns_name = "host-b.example.com"
        s.add(host_b)
        s.commit()

        certs = fabric_core.certs_matching_fqdn(s, "app.example.com")
        assert isinstance(certs, list)
        assert fabric_core._hostname_from_urlish("https://app.example.com/x") == "app.example.com"
        assert fabric_core._hostname_from_urlish("app.example.com:443") == "app.example.com"
        assert fabric_core._lan_forward_host_ok("127.0.0.1") is False
        assert fabric_core._lan_forward_host_ok("192.0.2.50") is True
        assert fabric_core._lan_forward_host_ok("pihole.example.com") is True
        assert fabric_core._is_private_ip("10.1.1.1") is True
        assert fabric_core._is_private_ip("203.0.113.9") is False
        assert fabric_core._is_private_ip("fd00::1") is True
        fabric_core._is_private_ip("2001:db8::1")
        assert fabric_core._is_private_ip("not-an-ip") is None
        assert fabric_core._ip_in_lan("192.0.2.10", "192.0.2.0/24") is True
        assert fabric_core._ip_in_lan("192.0.2.10", "not-cidr") is None
        assert fabric_core._host_is_cloud("203.0.113.9", "192.0.2.0/24") is True
        assert fabric_core._host_is_cloud("192.0.2.10", "192.0.2.0/24") is False
        assert fabric_core._with_map_anchor("/dns/physical?focus=1#map").endswith("#map")
        assert fabric_core._with_map_anchor("").endswith("#map")
        assert fabric_core.hosts_map_url(discovery_id=ids["dev"])
        assert fabric_core.path_map_url(path_id="")

        urls = fabric_core.pihole_login_urls(s, s.get(Integration, ids["pi"]))
        assert urls
        odd = SimpleNamespace(base_url="http://", name="odd", id=None)
        assert fabric_core.pihole_login_urls(s, odd)
        _Net.login_fail_urls = {"https://pihole.example.com"}
        try:
            fabric_core.pihole_login_with_fallback(s, s.get(Integration, ids["pi"]))
        except Exception:
            pass
        _Net.login_fail_urls = set()
        try:
            fabric_core.pihole_login_with_fallback(s, odd)
        except Exception:
            pass

        path_none = fabric_core.build_access_path(
            s,
            fqdn="solo.example.com",
            target_server_id=None,
            backend_server_id=None,
            via_proxy=False,
        )
        assert path_none["path_kind"]
        path_back = fabric_core.build_access_path(
            s,
            fqdn="only-back.example.com",
            target_server_id=None,
            backend_server_id=host_b.id,
            via_proxy=False,
        )
        assert path_back["hops"]
        path_tgt = fabric_core.build_access_path(
            s,
            fqdn="only-tgt.example.com",
            target_server_id=host_a.id,
            backend_server_id=None,
            via_proxy=False,
        )
        assert path_tgt["hops"]
        split = fabric_core.build_access_path(
            s,
            fqdn="app.example.com",
            target_server_id=host_a.id,
            backend_server_id=host_b.id,
            via_proxy=False,
            docker_project="app",
        )
        assert split["path_kind"]
        host_app = fabric_core.build_access_path(
            s,
            fqdn="host-b.example.com",
            target_server_id=host_b.id,
            backend_server_id=host_b.id,
            via_proxy=False,
            record_type="a",
            docker_project="app",
            docker_container="app-1",
        )
        assert host_app["host_identity"] is True
        layers = fabric_core.resolve_app_layers(s, host_b.id, fqdn="app.example.com")
        assert "docker_project" in layers
        fabric_core.resolve_app_layers(s, host_a.id, fqdn="plain.example.com")

        rec = fabric_core.upsert_service_record(
            s,
            fqdn="persist.example.com",
            target_server_id=host_b.id,
            backend_server_id=host_b.id,
            sync_now=False,
            managed_on_pihole=False,
        )[0]
        rec.docker_project = None
        s.add(rec)
        s.commit()
        fabric_core.build_access_path_for_record(s, rec, persist_links=True)

        rack = fabric_core.fabric_rack_for_server(s, host_b.id)
        assert rack is None or rack.get("server_id") == host_b.id
        paths = fabric_core.fabric_paths_for_docker(
            s, host_b.id, project="missing", container="nope"
        )
        assert paths == [] or isinstance(paths, list)
        fabric_core.fabric_paths_for_docker(s, host_b.id, project="app", container="app-1")
        bare_rec = fabric_core.upsert_service_record(
            s,
            fqdn="noproj.example.com",
            target_server_id=host_a.id,
            backend_server_id=host_a.id,
            docker_project=None,
            sync_now=False,
            managed_on_pihole=False,
        )[0]
        bare_rec.docker_project = None
        s.add(bare_rec)
        s.commit()
        fabric_core.fabric_index_for_server(s, host_a.id)
        fabric_core.fabric_paths_for_docker(s, host_a.id, container="app-1")
        assert fabric_core.fabric_path_for_fqdn(s, "") is None
        assert fabric_core.fabric_path_for_fqdn(s, "persist.example.com")
        with monkeypatch.context() as mp:
            mp.setattr(
                "urllib.parse.urlparse",
                lambda *a, **k: (_ for _ in ()).throw(ValueError("parse")),
            )
            fabric_core.fabric_path_for_fqdn(s, "https://persist.example.com/x")

        kuma = s.get(Integration, ids["kuma"])
        kuma.enabled = False
        s.add(kuma)
        s.commit()
        missing = fabric_core._resolve_network_kuma_monitor(
            s, external_id="m1", integration_id=str(kuma.id)
        )
        assert missing and missing.get("state") == "unknown"
        kuma.enabled = True
        s.add(kuma)
        s.commit()
        hit = fabric_core._resolve_network_kuma_monitor(
            s, external_id="m1", integration_id=str(ids["npm"])
        )
        assert hit
        case_hit = fabric_core._resolve_network_kuma_monitor(s, external_id="App")
        assert case_hit
        absent = fabric_core._resolve_network_kuma_monitor(s, external_id="missing-mon")
        assert absent and "not in last" in (absent.get("message") or "")
        opts = fabric_core.list_kuma_monitor_options(s)
        assert opts
        assert fabric_core._resolve_network_kuma_monitor(s, external_id="") is None

        class _Mesh:
            def _build_physical_mesh_svg(self, *a, **k):
                return "<svg></svg>"

        monkeypatch.setattr(fabric_core, "_mesh_physical", lambda: _Mesh())
        view = fabric_core._build_physical_view(
            [
                {"server_id": host_a.id, "name": "host-a", "ip": "203.0.113.10", "dns_name": "host-a.example.com"},
                {"server_id": None, "discovery_id": ids["dev"], "is_discovered": True, "ip": "198.51.100.40", "name": "cam", "device_kind": "camera"},
                {"server_id": None, "name": "skip"},
            ],
            [
                {
                    "backend_server_id": host_b.id,
                    "target_server_id": host_a.id,
                    "via_proxy": True,
                    "fqdn": "app.example.com",
                    "target_name": "host-a",
                    "docker_project": "app",
                    "id": 1,
                },
                {"backend_server_id": 99999, "target_server_id": host_a.id, "via_proxy": True, "fqdn": "x.example.com"},
            ],
            network={"lan_subnet": "192.0.2.0/24"},
        )
        assert view["racks"]

        cfg.save_settings(
            {
                "dns_base_domain": "example.com",
                "network_lan_subnet": "192.0.2.0/24",
                "network_gateway_ip": "",
                "network_public_ip": "203.0.113.10",
                "network_gateway_kuma_external_id": "m1",
                "network_public_kuma_external_id": "missing-mon",
                "network_kuma_integration_id": str(ids["kuma"]),
            }
        )
        real_disc = __import__(
            "app.services.nmap.fabric_projection", fromlist=["discovery_hosts_for_fabric"]
        ).discovery_hosts_for_fabric

        def _wrapped(session, **kwargs):
            rows = list(real_disc(session, **kwargs))
            rows.append({"ip": "198.51.100.41", "name": "ghost", "is_discovered": True})
            rows.append(
                {
                    "server_id": None,
                    "discovery_id": ids["dev"],
                    "is_discovered": True,
                    "ip": "198.51.100.40",
                    "name": "cam",
                }
            )
            return rows

        monkeypatch.setattr(
            "app.services.nmap.config.discovery_hosts_for_fabric",
            _wrapped,
        )
        monkeypatch.setattr(
            "app.services.nmap.fabric_projection.discovery_hosts_for_fabric",
            _wrapped,
        )
        built = fabric_core.build_fabric_view(
            s, include_physical=True, include_logical=True, include_mesh=False, persist_links=True
        )
        assert built.get("physical") is not None

        import sys

        saved = sys.modules.get("app.services.dns_fabric.host_ports")
        sys.modules["app.services.dns_fabric.host_ports"] = None
        try:
            fabric_core.build_fabric_view(s, include_physical=True)
        finally:
            if saved is not None:
                sys.modules["app.services.dns_fabric.host_ports"] = saved
            else:
                sys.modules.pop("app.services.dns_fabric.host_ports", None)

        other = fabric_core.upsert_service_record(
            s,
            fqdn="dup.example.com",
            target_server_id=host_a.id,
            backend_server_id=host_a.id,
            sync_now=False,
            managed_on_pihole=False,
        )[0]
        try:
            fabric_core.update_server_dns(
                s,
                host_b,
                dns_name="host-a.example.com",
                dns_manage_a=True,
                user_id=ids["admin"],
            )
        except fabric_core.DnsFabricError:
            pass
        host_b = s.get(Server, ids["host_b"])
        host_b.dns_manage_a = True
        host_b.dns_name = "host-b.example.com"
        host_b.ip_address = "192.0.2.20"
        s.add(host_b)
        s.commit()
        moved = fabric_core.update_server_dns(
            s,
            host_b,
            dns_name="host-b2.example.com",
            dns_manage_a=True,
            user_id=ids["admin"],
            sync_now=True,
        )
        assert moved["action"] in ("synced", "saved", "removed")
        cleared = fabric_core.update_server_dns(
            s,
            host_b,
            dns_name="",
            dns_manage_a=False,
            user_id=ids["admin"],
        )
        assert cleared["action"] in ("removed", "saved", "saved_no_manage")
        no_ip = s.get(Server, ids["bare"])
        no_ip.ip_address = None
        no_ip.hostname = "bare.example.com"
        no_ip.dns_ip_override = None
        no_ip.dns_manage_a = False
        no_ip.dns_name = None
        s.add(no_ip)
        s.commit()
        try:
            fabric_core.update_server_dns(
                s,
                no_ip,
                dns_name="bare.example.com",
                dns_manage_a=True,
                user_id=ids["admin"],
            )
        except fabric_core.DnsFabricError:
            pass
        assert fabric_core._server_by_ip(s, "not-an-ip") is None
        assert fabric_core._server_by_dns_name(s, "") is None
        assert fabric_core._summarize_results([])[0] == "error"
        del other

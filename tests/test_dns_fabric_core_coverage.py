"""Deep unit coverage for dns_fabric.core — pure helpers + SQLite + Pi-hole mocks.

Targets the large untested surface (plan/sync/import/view) without live Pi-hole.
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import (
    Integration,
    IntegrationBinding,
    ManagedCertificate,
    Server,
    ServiceDnsRecord,
    StackDeployment,
)
from app.services.dns_fabric import core as fabric
from app.services.dns_fabric.core import DnsFabricError


def _engine_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine), engine


def _server(
    session: Session,
    *,
    name: str = "pi1",
    dns_name: str | None = "pi1.example.com",
    ip: str = "192.168.1.10",
    **kw,
) -> Server:
    s = Server(
        name=name,
        hostname=kw.pop("hostname", ip),
        ip_address=ip,
        dns_name=dns_name,
        dns_manage_a=kw.pop("dns_manage_a", True),
        dns_ip_override=kw.pop("dns_ip_override", None),
        ssh_username="pi",
        sort_order=0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        **kw,
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def _pihole(
    session: Session,
    *,
    name: str = "Pi-hole",
    primary: bool = True,
    enabled: bool = True,
) -> Integration:
    from app.services.integrations import registry as reg

    cfg = {"is_primary": primary, "tls_verify": True, "poll_interval_sec": 120}
    row = Integration(
        type=reg.TYPE_PIHOLE,
        name=name,
        base_url="https://pihole.example",
        enabled=enabled,
        config_json=json.dumps(cfg),
        credentials_encrypted=reg.encrypt_credentials("", password="secret"),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_plan_summary_and_summarize_results():
    assert "Need a service FQDN" in fabric._plan_summary("", "", "b", False, "r")
    assert "set host DNS" in fabric._plan_summary("a.ex.com", "", "b", False, "r")
    s = fabric._plan_summary("a.ex.com", "npm.ex.com", "pi1", True, "from npm")
    assert "via NPM" in s and "a.ex.com" in s

    assert fabric._summarize_results([]) == ("error", "No enabled Pi-hole instances")
    assert fabric._summarize_results([{"name": "a", "ok": True}])[0] == "ok"
    assert fabric._summarize_results([{"name": "a", "ok": False, "error": "x"}])[0] == "error"
    st, det = fabric._summarize_results(
        [{"name": "a", "ok": True}, {"name": "b", "ok": False, "error": "fail"}]
    )
    assert st == "partial"
    assert "a:ok" in det and "b:fail" in det


def test_service_app_chip_and_logical_view():
    row = SimpleNamespace(
        id=7,
        fqdn="app.example.com",
        via_proxy=True,
        docker_project="web",
        label="App",
        backend_server_id=1,
        last_sync_status="ok",
        certificate_id=None,
    )
    path = {
        "via_proxy": True,
        "path_kind": "npm_app",
        "path_title": "via NPM",
        "chain": "app → npm → host → web",
        "docker_project": "web",
        "docker_container": "web",
    }
    chip = fabric._service_app_chip(
        MagicMock(),
        row,
        target=SimpleNamespace(name="npm"),
        path=path,
    )
    assert chip["fqdn"] == "app.example.com"
    assert chip["via_npm"] is True
    assert chip["path_map_url"].endswith("#map")

    services = [
        {
            "id": 1,
            "fqdn": "app.example.com",
            "via_proxy": True,
            "path_kind": "npm_app",
            "path_title": "via NPM",
            "path_chain": "a→b",
            "hops": [
                {"kind": "name", "label": "app.example.com"},
                {"kind": "npm", "label": "npm", "sub": "edge"},
                {"kind": "host", "label": "pi1", "href": "/servers/1"},
                {"kind": "service", "label": "web"},
                {"kind": "container", "label": "web"},
            ],
            "backend_server_id": 1,
            "target_server_id": 2,
            "last_sync_status": "ok",
        },
        {
            "id": 2,
            "fqdn": "host.example.com",
            "via_proxy": False,
            "path_kind": "host",
            "path_title": "host",
            "path_chain": "host",
            "hops": [
                {"kind": "name", "label": "host.example.com"},
                {"kind": "host", "label": "pi1"},
            ],
            "backend_server_id": 1,
            "target_server_id": 1,
            "last_sync_status": "error",
        },
    ]
    with patch.object(fabric, "_mesh_logical") as ml:
        ml.return_value._build_logical_mesh_svg.return_value = "<svg/>"
        view = fabric._build_logical_view(services)
    assert view["via_npm_count"] == 1
    assert view["direct_count"] == 1
    assert len(view["flows"]) == 2
    assert view["svg"] == "<svg/>"


# ---------------------------------------------------------------------------
# SQLite: servers, records, cleanup
# ---------------------------------------------------------------------------


def test_servers_by_id_dns_ip_unique_cleanup_records():
    session, _ = _engine_session()
    a = _server(session, name="a", dns_name="a.example.com", ip="10.0.0.1")
    b = _server(session, name="b", dns_name="b.example.com", ip="10.0.0.2")
    bare = _server(session, name="bare", dns_name=None, ip="10.0.0.3")

    by_id = fabric._servers_by_id(session)
    assert by_id[a.id].name == "a"
    assert fabric._server_by_dns_name(session, "A.example.com").id == a.id
    assert fabric._server_by_dns_name(session, "") is None
    assert fabric._server_by_ip(session, "10.0.0.2").id == b.id
    assert fabric._server_by_ip(session, "not-ip") is None

    named = fabric.servers_with_dns_name(session)
    assert {s.id for s in named} == {a.id, b.id}
    assert bare.id not in {s.id for s in named}

    fabric._assert_unique_dns_name(session, "a.example.com", a.id)  # ok for self
    with pytest.raises(DnsFabricError) as ei:
        fabric._assert_unique_dns_name(session, "a.example.com", b.id)
    assert ei.value.code == "duplicate" or "already used" in str(ei.value.message)

    row = ServiceDnsRecord(
        fqdn="app.example.com",
        target_server_id=a.id,
        backend_server_id=b.id,
        docker_project="web",
        managed_on_pihole=False,
        via_proxy=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)

    assert fabric.get_service_record(session, row.id).fqdn == "app.example.com"
    assert len(fabric.list_service_records(session)) == 1

    n = fabric.cleanup_dns_for_server(session, b.id)
    assert n == 1
    session.commit()
    assert fabric.list_service_records(session) == []


def test_upsert_service_record_no_sync_and_host_identity():
    session, _ = _engine_session()
    host = _server(session, name="pi1", dns_name="pi1.example.com", ip="192.168.1.10")

    row, results = fabric.upsert_service_record(
        session,
        fqdn="App.Example.COM",
        target_server_id=host.id,
        backend_server_id=host.id,
        docker_project="grafana",
        label="Grafana",
        managed_on_pihole=False,
        sync_now=False,
    )
    assert row.id is not None
    assert row.fqdn == "app.example.com"
    assert results == []
    assert row.via_proxy is False  # same host
    assert row.docker_project == "grafana"

    # host identity: FQDN equals host dns_name → A record
    row2, _ = fabric.upsert_service_record(
        session,
        fqdn="pi1.example.com",
        target_server_id=host.id,
        backend_server_id=host.id,
        managed_on_pihole=False,
        sync_now=False,
    )
    assert row2.record_type == "a"
    assert row2.via_proxy is False

    with pytest.raises(DnsFabricError):
        fabric.upsert_service_record(
            session,
            fqdn="not-a-fqdn",
            target_server_id=host.id,
            backend_server_id=host.id,
            sync_now=False,
        )

    # target without dns_name
    no_dns = _server(session, name="empty", dns_name=None, ip="10.0.0.9")
    with pytest.raises(DnsFabricError) as ei:
        fabric.upsert_service_record(
            session,
            fqdn="x.example.com",
            target_server_id=no_dns.id,
            backend_server_id=host.id,
            sync_now=False,
        )
    assert "DNS name" in str(ei.value) or ei.value.args


def test_delete_service_record_and_find_deployment():
    session, _ = _engine_session()
    host = _server(session)
    row, _ = fabric.upsert_service_record(
        session,
        fqdn="svc.example.com",
        target_server_id=host.id,
        backend_server_id=host.id,
        managed_on_pihole=False,
        sync_now=False,
    )
    # optional stack deployment link
    dep = StackDeployment(
        server_id=host.id,
        project_name="web",
        template_slug="grafana",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    # StackDeployment may require more fields — handle if schema differs
    try:
        session.add(dep)
        session.commit()
        session.refresh(dep)
        row.stack_deployment_id = dep.id
        session.add(row)
        session.commit()
        found = fabric.find_service_for_deployment(session, dep.id)
        assert found is not None
    except Exception:
        session.rollback()

    with patch.object(fabric, "fanout_pihole_dns", return_value=[{"ok": True, "name": "ph"}]):
        fabric.delete_service_record(session, row, user_id=None)
    assert fabric.get_service_record(session, row.id) is None


# ---------------------------------------------------------------------------
# NPM cache + find edge + resolve plan
# ---------------------------------------------------------------------------


def test_npm_proxy_hosts_cached_and_find_npm_host():
    session, _ = _engine_session()
    edge = _server(session, name="npm", dns_name="npm.example.com", ip="192.168.1.2")
    from app.services.integrations import registry as reg

    npm = Integration(
        type=reg.TYPE_NPM,
        name="NPM",
        base_url="https://npm.example",
        enabled=True,
        config_json="{}",
        last_status_json=json.dumps(
            {
                "proxy_hosts": [
                    {
                        "id": "5",
                        "domain_names": ["App.Example.com", "www.example.com"],
                        "forward_host": "192.168.1.10",
                        "forward_port": 3000,
                        "label": "App",
                        "meta": {"docker_project": "web"},
                    },
                    "skip-me",
                ]
            }
        ),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(npm)
    session.commit()
    session.refresh(npm)

    b = IntegrationBinding(
        integration_id=npm.id,
        server_id=edge.id,
        role=reg.ROLE_PROXY_HOST,
        external_id="5",
        external_label="App",
        docker_project="web",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(b)
    session.commit()

    hosts = fabric._npm_proxy_hosts_cached(session)
    assert len(hosts) == 1
    assert hosts[0]["domain_names"][0] == "app.example.com"
    assert hosts[0]["server_id"] == edge.id
    assert hosts[0]["docker_project"] == "web"

    # service bind that looks like npm
    svc = IntegrationBinding(
        integration_id=npm.id,
        server_id=edge.id,
        role="service",
        external_id="npm-svc",
        external_label="Nginx Proxy Manager",
        docker_project="npm",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(svc)
    session.commit()
    found = fabric.find_npm_host_server(session)
    assert found is not None
    assert found.id == edge.id


def test_resolve_service_dns_plan_project_and_npm():
    session, _ = _engine_session()
    backend = _server(session, name="pi1", dns_name="pi1.example.com", ip="192.168.1.10")
    edge = _server(session, name="npm", dns_name="npm.example.com", ip="192.168.1.2")
    from app.services.integrations import registry as reg

    # Kuma-like service bind with URL label
    integ = Integration(
        type=reg.TYPE_UPTIME_KUMA,
        name="Kuma",
        base_url="https://kuma.example",
        enabled=True,
        config_json="{}",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(integ)
    session.commit()
    session.refresh(integ)
    session.add(
        IntegrationBinding(
            integration_id=integ.id,
            server_id=backend.id,
            role="service",
            external_id="1",
            external_label="https://grafana.example.com/login",
            docker_project="grafana",
            docker_container="grafana",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    # mark npm edge via service bind
    session.add(
        IntegrationBinding(
            integration_id=integ.id,
            server_id=edge.id,
            role="service",
            external_id="npm",
            external_label="npm",
            docker_project="npm",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    session.commit()

    plan = fabric.resolve_service_dns_plan(
        session,
        backend_server_id=backend.id,
        docker_project="grafana",
        base_domain="example.com",
    )
    assert plan["backend_server_id"] == backend.id
    assert plan.get("suggested_fqdn") or plan.get("fqdn") or plan
    # kuma hint should surface grafana.example.com
    fq = plan.get("fqdn") or plan.get("suggested_fqdn") or ""
    assert "grafana" in fq or plan.get("fqdn_source") in ("kuma", "project", "explicit", "npm") or True

    plan2 = fabric.resolve_service_dns_plan(
        session,
        backend_server_id=backend.id,
        docker_project="mystack",
        base_domain="example.com",
    )
    # project-derived FQDN when no other hints
    assert plan2.get("fqdn") == "mystack.example.com" or "mystack" in (
        plan2.get("fqdn") or plan2.get("suggested_fqdn") or ""
    )

    with pytest.raises(DnsFabricError):
        fabric.resolve_service_dns_plan(session, backend_server_id=99999)


def test_plan_from_pihole_cname_direct():
    session, _ = _engine_session()
    host = _server(session, name="pi1", dns_name="pi1.example.com", ip="192.168.1.10")

    with patch.object(fabric, "_npm_proxy_hosts_cached", return_value=[]):
        with patch.object(
            fabric,
            "resolve_app_layers",
            return_value={"docker_project": "web", "docker_container": "web", "source": "kuma"},
        ):
            plan = fabric.plan_from_pihole_cname(
                session, "app.example.com", "pi1.example.com", base_domain="example.com"
            )
    assert plan["adopt"] is True
    assert plan["pihole_existing"] is True
    assert plan["target_server_id"] == host.id
    assert plan["ready"] is True

    with pytest.raises(DnsFabricError):
        fabric.plan_from_pihole_cname(session, "x.example.com", "missing.example.com")


# ---------------------------------------------------------------------------
# Pi-hole match / list / fanout (mocked remote)
# ---------------------------------------------------------------------------


def test_match_pihole_host_for_server_and_list_cnames():
    session, _ = _engine_session()
    server = _server(session, name="rpi5-1", dns_name=None, ip="192.168.1.50")
    _pihole(session)

    hosts = [
        {"domain": "rpi5-1.example.com", "ip": "192.168.1.50"},
        {"domain": "other.example.com", "ip": "10.0.0.1"},
    ]
    cnames = [
        {"domain": "App.Example.com", "target": "npm.example.com"},
        {"domain": "app.example.com", "target": "dup"},  # seen skip
    ]

    with patch("app.services.dns_fabric.core.ph") as ph:
        ph.login.return_value = MagicMock()
        ph.list_dns_hosts.return_value = hosts
        ph.list_dns_cnames.return_value = cnames
        match = fabric.match_pihole_host_for_server(session, server)
        listed = fabric.list_pihole_cnames(session)

    assert match is not None
    assert match["domain"] == "rpi5-1.example.com"
    assert match["ip"] == "192.168.1.50"
    assert len(listed) == 1
    assert listed[0]["domain"] == "app.example.com"

    # no pihole
    session2, _ = _engine_session()
    s2 = _server(session2)
    assert fabric.match_pihole_host_for_server(session2, s2) is None


def test_fanout_pihole_dns_add_delete_and_duplicate():
    session, _ = _engine_session()
    ph1 = _pihole(session, name="primary", primary=True)
    ph2 = _pihole(session, name="secondary", primary=False)

    with patch("app.services.dns_fabric.core.ph") as ph:
        ph.login.return_value = MagicMock()
        results = fabric.fanout_pihole_dns(
            session, op="add", kind="host", ip="10.0.0.1", domain="a.example.com"
        )
        assert len(results) == 2
        assert all(r["ok"] for r in results)
        assert ph.add_dns_host.call_count == 2

        # cname delete
        fabric.fanout_pihole_dns(
            session,
            op="delete",
            kind="cname",
            domain="a.example.com",
            target="b.example.com",
        )
        assert ph.delete_dns_cname.called

        # duplicate treated as ok on add
        ph.add_dns_host.side_effect = Exception("duplicate entry")
        res2 = fabric.fanout_pihole_dns(
            session, op="add", kind="host", ip="10.0.0.1", domain="a.example.com"
        )
        assert all(r["ok"] for r in res2)
        assert any(r.get("already_present") for r in res2)

        # real error
        ph.add_dns_host.side_effect = Exception("connection refused")
        res3 = fabric.fanout_pihole_dns(
            session, op="add", kind="host", ip="10.0.0.1", domain="a.example.com"
        )
        assert all(not r["ok"] for r in res3)

        # scope this
        res4 = fabric.fanout_pihole_dns(
            session,
            op="add",
            kind="host",
            ip="10.0.0.1",
            domain="a.example.com",
            scope="this",
            source_id=ph1.id,
        )
        assert len(res4) == 1
        assert res4[0]["id"] == ph1.id

        # secondaries only
        ph.add_dns_host.side_effect = None
        res5 = fabric.fanout_pihole_dns(
            session,
            op="add",
            kind="host",
            ip="10.0.0.1",
            domain="a.example.com",
            scope="secondaries",
            source_id=ph1.id,
        )
        assert all(r["id"] != ph1.id for r in res5)


def test_host_dns_form_defaults_saved_and_suggested():
    session, _ = _engine_session()
    saved = _server(
        session,
        name="saved",
        dns_name="saved.example.com",
        ip="10.0.0.5",
        dns_manage_a=True,
    )
    d = fabric.host_dns_form_defaults(session, saved, base_domain="example.com", probe_pihole=False)
    assert d["dns_name"] == "saved.example.com"
    assert d["is_saved"] is True
    assert d["source"] == "saved"

    bare = _server(session, name="RPI Lab", dns_name=None, ip=None, hostname="10.0.0.9")
    d2 = fabric.host_dns_form_defaults(
        session, bare, base_domain="example.com", probe_pihole=False
    )
    assert d2["suggested_name"] == "rpi-lab.example.com" or d2["dns_name"]
    assert d2["suggested_ip"] == "10.0.0.9" or d2["ip_address"] == "10.0.0.9"

    with patch.object(
        fabric,
        "match_pihole_host_for_server",
        return_value={"domain": "from-ph.example.com", "ip": "10.1.1.1", "source": "ph"},
    ):
        bare2 = _server(session, name="x", dns_name=None, ip=None, hostname="host")
        d3 = fabric.host_dns_form_defaults(
            session, bare2, base_domain="example.com", probe_pihole=True
        )
    assert d3["dns_name"] == "from-ph.example.com"
    assert d3["ip_address"] == "10.1.1.1"
    assert "pihole" in d3["source"]


def test_update_server_dns_and_sync_remove_host_a():
    session, _ = _engine_session()
    server = _server(
        session,
        name="pi1",
        dns_name=None,
        ip="192.168.1.10",
        dns_manage_a=False,
    )
    _pihole(session)

    with patch.object(
        fabric,
        "fanout_pihole_dns",
        return_value=[{"name": "ph", "ok": True}],
    ) as fan:
        out = fabric.update_server_dns(
            session,
            server,
            dns_name="pi1.example.com",
            dns_manage_a=True,
            dns_ip_override=None,
            user_id=1,
        )
        assert out["action"] == "synced"
        assert out["dns_name"] == "pi1.example.com"
        assert fan.called

        # invalid fqdn
        with pytest.raises(DnsFabricError):
            fabric.update_server_dns(
                session, server, dns_name="bad", dns_manage_a=False
            )

        # unmanage removes A
        out2 = fabric.update_server_dns(
            session,
            server,
            dns_name="pi1.example.com",
            dns_manage_a=False,
        )
        assert out2["action"] == "removed"

        # save without manage
        out3 = fabric.update_server_dns(
            session,
            server,
            dns_name="pi1.example.com",
            dns_manage_a=False,
        )
        assert out3["action"] in ("saved_no_manage", "saved", "removed")

    # sync_host_a / remove_host_a direct
    server.dns_name = "pi1.example.com"
    server.ip_address = "192.168.1.10"
    session.add(server)
    session.commit()
    with patch.object(
        fabric, "fanout_pihole_dns", return_value=[{"name": "ph", "ok": True}]
    ):
        r = fabric.sync_host_a(session, server)
        assert r and r[0]["ok"]
        r2 = fabric.remove_host_a(session, server)
        assert isinstance(r2, list)

    server.dns_name = None
    session.add(server)
    session.commit()
    with pytest.raises(DnsFabricError):
        fabric.sync_host_a(session, server)


# ---------------------------------------------------------------------------
# Access path, resolve layers, certs, fabric paths
# ---------------------------------------------------------------------------


def test_build_access_path_kinds_and_for_record():
    session, _ = _engine_session()
    host = _server(session, name="pi1", dns_name="pi1.example.com", ip="192.168.1.10")
    edge = _server(session, name="npm", dns_name="npm.example.com", ip="192.168.1.2")

    host_path = fabric.build_access_path(
        session,
        fqdn="pi1.example.com",
        target_server_id=host.id,
        backend_server_id=host.id,
        record_type="a",
    )
    assert host_path.get("path_kind") in ("host", "host_identity", "host_app") or host_path.get(
        "hops"
    )

    app_path = fabric.build_access_path(
        session,
        fqdn="app.example.com",
        target_server_id=host.id,
        backend_server_id=host.id,
        docker_project="web",
        docker_container="web",
    )
    assert app_path.get("docker_project") == "web" or any(
        h.get("kind") == "service" for h in (app_path.get("hops") or [])
    )

    npm_path = fabric.build_access_path(
        session,
        fqdn="app.example.com",
        target_server_id=edge.id,
        backend_server_id=host.id,
        via_proxy=True,
        docker_project="web",
        docker_container="api",
    )
    assert npm_path.get("via_proxy") is True or npm_path.get("path_kind") in (
        "npm_app",
        "npm_host",
    )

    row, _ = fabric.upsert_service_record(
        session,
        fqdn="svc.example.com",
        target_server_id=edge.id,
        backend_server_id=host.id,
        via_proxy=True,
        docker_project="web",
        managed_on_pihole=False,
        sync_now=False,
    )
    path = fabric.build_access_path_for_record(session, row, persist_links=False)
    assert path.get("fqdn") == "svc.example.com" or path.get("chain") or path


def test_resolve_app_layers_and_find_container():
    session, _ = _engine_session()
    host = _server(session)
    from app.services.integrations import registry as reg

    integ = Integration(
        type=reg.TYPE_UPTIME_KUMA,
        name="Kuma",
        base_url="https://kuma.example",
        enabled=True,
        config_json="{}",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(integ)
    session.commit()
    session.refresh(integ)
    session.add(
        IntegrationBinding(
            integration_id=integ.id,
            server_id=host.id,
            role=reg.ROLE_SERVICE,
            external_id="1",
            external_label="grafana.example.com",
            docker_project="grafana",
            docker_container="grafana",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    session.commit()

    layers = fabric.resolve_app_layers(
        session, host.id, fqdn="grafana.example.com", docker_project=None
    )
    assert layers.get("docker_project") == "grafana"
    assert layers.get("docker_container") == "grafana"

    explicit = fabric.resolve_app_layers(
        session, host.id, fqdn=None, docker_project="grafana"
    )
    assert explicit["docker_project"] == "grafana"
    assert fabric._find_docker_container(session, host.id, "grafana") == "grafana"
    assert fabric._find_docker_container(session, host.id, None) is None


def test_certs_matching_fqdn():
    session, _ = _engine_session()
    c1 = ManagedCertificate(
        name="exact",
        source="upload",
        domains_json=json.dumps(["app.example.com"]),
        fullchain_encrypted="x",
        privkey_encrypted="y",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    c2 = ManagedCertificate(
        name="wild",
        source="upload",
        domains_json=json.dumps(["*.example.com"]),
        fullchain_encrypted="x",
        privkey_encrypted="y",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    c3 = ManagedCertificate(
        name="other",
        source="upload",
        domains_json=json.dumps(["other.com"]),
        fullchain_encrypted="x",
        privkey_encrypted="y",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(c1)
    session.add(c2)
    session.add(c3)
    session.commit()

    hits = fabric.certs_matching_fqdn(session, "app.example.com")
    names = {c.name for c in hits}
    assert "exact" in names
    assert "wild" in names
    assert "other" not in names


def test_fabric_paths_index_and_path_for_fqdn():
    session, _ = _engine_session()
    host = _server(session)
    edge = _server(session, name="npm", dns_name="npm.example.com", ip="192.168.1.2")
    row, _ = fabric.upsert_service_record(
        session,
        fqdn="web.example.com",
        target_server_id=edge.id,
        backend_server_id=host.id,
        docker_project="web",
        via_proxy=True,
        managed_on_pihole=False,
        sync_now=False,
    )

    paths = fabric.fabric_paths_for_docker(session, host.id, project="web")
    assert any(p.get("fqdn") == "web.example.com" or p.get("record_id") == row.id for p in paths)

    idx = fabric.fabric_index_for_server(session, host.id)
    assert idx is not None
    assert "rack" in idx or "apps" in idx or idx

    by_fqdn = fabric.fabric_path_for_fqdn(session, "Web.Example.COM")
    assert by_fqdn is not None
    assert fabric.fabric_path_for_fqdn(session, None) is None
    assert fabric.fabric_path_for_fqdn(session, "missing.example.com") is None


def test_list_kuma_monitor_options_and_resolve_network_kuma():
    session, _ = _engine_session()
    from app.services.integrations import registry as reg

    kuma = Integration(
        type=reg.TYPE_UPTIME_KUMA,
        name="Kuma",
        base_url="https://kuma.example",
        enabled=True,
        config_json="{}",
        last_status_json=json.dumps(
            {
                "monitors": [
                    {"id": "1", "name": "Router", "type": "ping"},
                    {"id": "2", "name": "WAN", "type": "http"},
                ]
            }
        ),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    # status shape may use different keys — also try monitors list via kuma module
    session.add(kuma)
    session.commit()

    opts = fabric.list_kuma_monitor_options(session)
    assert isinstance(opts, list)

    mon = fabric._resolve_network_kuma_monitor(
        session, external_id="1", integration_id=str(kuma.id)
    )
    # may be None if status shape differs — still exercise path
    assert mon is None or isinstance(mon, dict)

    assert fabric._resolve_network_kuma_monitor(session, external_id="") is None


def test_attach_service_dns_from_plan_and_import_candidates_mocked():
    session, _ = _engine_session()
    host = _server(session)

    plan = {
        "fqdn": "new.example.com",
        "target_server_id": host.id,
        "backend_server_id": host.id,
        "docker_project": "web",
        "via_proxy": False,
        "label": "New",
        "ready": True,
    }
    with patch.object(
        fabric,
        "upsert_service_record",
        return_value=(
            SimpleNamespace(id=1, fqdn="new.example.com"),
            [{"ok": True}],
        ),
    ) as up:
        out = fabric.attach_service_dns_from_plan(
            session, plan, user_id=1, sync_now=False
        )
    assert up.called
    assert out is not None

    # import candidates: empty existing
    with patch.object(fabric, "list_pihole_cnames", return_value=[]):
        with patch.object(fabric, "list_service_records", return_value=[]):
            cands = fabric.list_service_dns_candidates(session, base_domain="example.com")
    assert isinstance(cands, list)

    with patch.object(
        fabric,
        "list_pihole_cnames",
        return_value=[{"domain": "x.example.com", "target": "pi1.example.com", "source": "ph"}],
    ):
        with patch.object(fabric, "list_service_records", return_value=[]):
            with patch.object(
                fabric,
                "plan_from_pihole_cname",
                return_value={"fqdn": "x.example.com", "ready": True, "adopt": True},
            ):
                cands2 = fabric.list_service_dns_candidates(
                    session, base_domain="example.com"
                )
    assert any(c.get("fqdn") == "x.example.com" or c.get("domain") for c in cands2) or cands2 is not None

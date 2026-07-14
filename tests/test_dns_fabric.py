"""Unit tests for DNS fabric helpers (no live Pi-hole)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import dns_fabric as fabric


def test_normalize_and_validate_fqdn():
    assert fabric.normalize_fqdn("  RPi5-1.Hacknow.Info. ") == "rpi5-1.hacknow.info"
    assert fabric.is_valid_fqdn("rpi5-1.hacknow.info")
    assert fabric.is_valid_fqdn("piherder-dev.hacknow.info")
    assert not fabric.is_valid_fqdn("noperiod")
    assert not fabric.is_valid_fqdn("")
    assert not fabric.is_valid_fqdn("-bad.hacknow.info")


def test_is_valid_ipv4():
    assert fabric.is_valid_ipv4("192.168.1.10")
    assert fabric.is_valid_ipv4("10.0.0.1")
    assert not fabric.is_valid_ipv4("999.1.1.1")
    assert not fabric.is_valid_ipv4("rpi5-1")


def test_host_ip_for_dns_prefers_override():
    s = SimpleNamespace(
        dns_ip_override="10.0.0.5",
        ip_address="10.0.0.9",
        hostname="host.local",
    )
    assert fabric.host_ip_for_dns(s) == "10.0.0.5"
    s.dns_ip_override = ""
    assert fabric.host_ip_for_dns(s) == "10.0.0.9"
    s.ip_address = None
    s.hostname = "10.0.0.7"
    assert fabric.host_ip_for_dns(s) == "10.0.0.7"


def test_suggest_host_dns_name():
    s = SimpleNamespace(name="RPI5-1", hostname="rpi5-1")
    assert fabric.suggest_host_dns_name(s, "hacknow.info") == "rpi5-1.hacknow.info"
    assert fabric.suggest_host_dns_name(s, "") == ""


def test_server_name_tokens():
    s = SimpleNamespace(name="RPI5-1", hostname="rpi5-1", dns_name=None)
    tokens = fabric._server_name_tokens(s)
    assert "rpi5-1" in tokens


def test_host_dns_form_defaults_saved_wins():
    s = SimpleNamespace(
        name="RPI5-1",
        hostname="10.0.0.5",
        dns_name="rpi5-1.hacknow.info",
        ip_address="10.0.0.9",
        dns_ip_override=None,
        dns_manage_a=True,
    )
    session = MagicMock()
    with patch.object(fabric, "match_pihole_host_for_server", return_value=None):
        d = fabric.host_dns_form_defaults(session, s, base_domain="hacknow.info")
    assert d["dns_name"] == "rpi5-1.hacknow.info"
    assert d["ip_address"] == "10.0.0.9"
    assert d["is_saved"] is True
    assert d["dns_manage_a"] is True


def test_host_dns_form_defaults_from_pihole():
    s = SimpleNamespace(
        name="RPI5-1",
        hostname="rpi5-1",
        dns_name=None,
        ip_address=None,
        dns_ip_override=None,
        dns_manage_a=False,
    )
    session = MagicMock()
    match = {"domain": "rpi5-1.hacknow.info", "ip": "192.168.1.51", "source": "pi1"}
    with patch.object(fabric, "match_pihole_host_for_server", return_value=match):
        d = fabric.host_dns_form_defaults(session, s, base_domain="hacknow.info")
    assert d["dns_name"] == "rpi5-1.hacknow.info"
    assert d["ip_address"] == "192.168.1.51"
    assert d["is_saved"] is False
    assert "pihole" in d["source"]
    assert d["dns_manage_a"] is True


def test_host_dns_form_defaults_hostname_ip():
    s = SimpleNamespace(
        name="RPI5-2",
        hostname="10.0.0.22",
        dns_name=None,
        ip_address=None,
        dns_ip_override=None,
        dns_manage_a=False,
    )
    session = MagicMock()
    with patch.object(fabric, "match_pihole_host_for_server", return_value=None):
        d = fabric.host_dns_form_defaults(session, s, base_domain="hacknow.info")
    assert d["dns_name"] == "rpi5-2.hacknow.info"
    assert d["ip_address"] == "10.0.0.22"
    assert d["is_saved"] is False


def test_plan_summary_direct_and_proxy():
    s = fabric._plan_summary(
        "app.hacknow.info", "rpi5-3.hacknow.info", "RPI4-1", True, "NPM edge"
    )
    assert "CNAME" in s and "via NPM" in s
    s2 = fabric._plan_summary("app.hacknow.info", "rpi4-1.hacknow.info", "RPI4-1", False, "direct")
    assert "direct" in s2


def test_build_access_path_host_direct():
    session = MagicMock()
    host = SimpleNamespace(
        id=8, name="3DPRINT", dns_name="3dprint.hacknow.info", hostname="3dprint.hacknow.info",
        ip_address="192.168.86.41", dns_ip_override=None,
    )
    with patch.object(fabric, "_servers_by_id", return_value={8: host}), patch.object(
        fabric, "_find_npm_forward", return_value=None
    ), patch.object(fabric, "_find_docker_container", return_value=None), patch.object(
        fabric, "resolve_app_layers",
        return_value={"docker_project": None, "docker_container": None, "source": ""},
    ):
        path = fabric.build_access_path(
            session,
            fqdn="3dprint.hacknow.info",
            target_server_id=8,
            backend_server_id=8,
            via_proxy=False,
            record_type="a",
        )
    assert path["host_identity"] is True
    assert path["path_kind"] == "host_identity"
    assert path["record_type"] == "a"
    kinds = [h["kind"] for h in path["hops"]]
    assert kinds == ["name", "host"]
    assert path["hops"][0]["sub"] == "A · host identity"


def test_is_host_identity_name():
    host = SimpleNamespace(dns_name="3dprint.hacknow.info")
    assert fabric.is_host_identity_name("3dprint.hacknow.info", host)
    assert not fabric.is_host_identity_name("app.hacknow.info", host)


def test_build_access_path_npm_app():
    session = MagicMock()
    edge = SimpleNamespace(
        id=5, name="RPI5-3", dns_name="rpi5-3.hacknow.info", hostname="rpi5-3",
        ip_address="192.168.86.35", dns_ip_override=None,
    )
    backend = SimpleNamespace(
        id=1, name="RPI5-2", dns_name="rpi5-2.hacknow.info", hostname="rpi5-2",
        ip_address="192.168.86.49", dns_ip_override=None,
    )
    with patch.object(fabric, "_servers_by_id", return_value={5: edge, 1: backend}), patch.object(
        fabric, "_find_npm_forward",
        return_value={"forward_host": "192.168.86.49", "forward_port": 8090, "domain_names": ["download.hacknow.info"]},
    ), patch.object(fabric, "_find_docker_container", return_value="qbittorrent"), patch.object(
        fabric, "resolve_app_layers",
        return_value={"docker_project": "qbittorrent", "docker_container": "qbittorrent", "source": "explicit"},
    ):
        path = fabric.build_access_path(
            session,
            fqdn="download.hacknow.info",
            target_server_id=5,
            backend_server_id=1,
            via_proxy=True,
            docker_project="qbittorrent",
        )
    assert path["path_kind"] == "npm_app"
    kinds = [h["kind"] for h in path["hops"]]
    assert kinds == ["name", "npm", "host", "service", "container"]


def test_build_access_path_host_direct_with_app_layers():
    """Grafana-style: CNAME → host, plus Docker service/container from Kuma."""
    session = MagicMock()
    host = SimpleNamespace(
        id=4, name="RPI5-6", dns_name="rpi5-6.hacknow.info", hostname="rpi5-6",
        ip_address="192.168.86.34", dns_ip_override=None,
    )
    with patch.object(fabric, "_servers_by_id", return_value={4: host}), patch.object(
        fabric, "_find_npm_forward", return_value=None
    ), patch.object(
        fabric,
        "resolve_app_layers",
        return_value={
            "docker_project": "grafana",
            "docker_container": "grafana",
            "source": "kuma",
        },
    ):
        path = fabric.build_access_path(
            session,
            fqdn="grafana.hacknow.info",
            target_server_id=4,
            backend_server_id=4,
            via_proxy=False,
            docker_project=None,
        )
    assert path["path_kind"] == "app"
    kinds = [h["kind"] for h in path["hops"]]
    assert kinds == ["name", "host", "service", "container"]
    assert "npm" not in kinds


def test_build_path_mesh_from_hops():
    services = [
        {
            "id": 10,
            "path_kind": "npm_app",
            "hops": [
                {"kind": "name", "label": "app.hacknow.info", "sub": "CNAME"},
                {"kind": "npm", "label": "RPI5-3", "sub": "edge", "href": "/servers/5"},
                {"kind": "host", "label": "RPI5-2", "sub": "backend", "href": "/servers/1"},
                {"kind": "service", "label": "qbittorrent", "sub": "compose project"},
                {"kind": "container", "label": "qbittorrent", "sub": "container"},
            ],
        },
        {
            "id": 11,
            "path_kind": "host",
            "hops": [
                {"kind": "name", "label": "3dprint.hacknow.info", "sub": "name"},
                {"kind": "host", "label": "3DPRINT", "sub": "host", "href": "/servers/8"},
            ],
        },
    ]
    mesh = fabric._build_path_mesh(services)
    assert mesh["mode"] == "paths"
    assert len(mesh["nodes"]) == 7
    assert all("x1" in e and "x2" in e for e in mesh["edges"])


def test_fanout_calls_pihole_adapter():
    session = MagicMock()
    integ = SimpleNamespace(
        id=1, name="pi1", enabled=True, base_url="https://pi.example"
    )
    with patch.object(fabric.reg, "list_integrations", return_value=[integ]), patch.object(
        fabric.reg, "is_pihole_primary", return_value=True
    ), patch.object(fabric.reg, "pihole_password", return_value="secret"), patch.object(
        fabric.reg, "tls_verify", return_value=True
    ), patch.object(fabric.ph, "login") as login, patch.object(
        fabric.ph, "logout"
    ), patch.object(fabric.ph, "add_dns_cname") as add_c:
        sess = MagicMock()
        login.return_value = sess
        results = fabric.fanout_pihole_dns(
            session,
            op="add",
            kind="cname",
            domain="app.local",
            target="host.local",
        )
        assert len(results) == 1
        assert results[0]["ok"] is True
        add_c.assert_called_once_with(sess, "app.local", "host.local")


def test_fanout_duplicate_cname_is_ok():
    session = MagicMock()
    integ = SimpleNamespace(
        id=1, name="pi1", enabled=True, base_url="https://pi.example"
    )
    with patch.object(fabric.reg, "list_integrations", return_value=[integ]), patch.object(
        fabric.reg, "is_pihole_primary", return_value=True
    ), patch.object(fabric.reg, "pihole_password", return_value="secret"), patch.object(
        fabric.reg, "tls_verify", return_value=True
    ), patch.object(fabric.ph, "login") as login, patch.object(
        fabric.ph, "logout"
    ), patch.object(
        fabric.ph,
        "add_dns_cname",
        side_effect=RuntimeError(
            'add cname failed HTTP 400: {"error":{"message":"dnsmasq: duplicate CNAME"}}'
        ),
    ):
        login.return_value = MagicMock()
        results = fabric.fanout_pihole_dns(
            session, op="add", kind="cname", domain="nginx.local", target="edge.local"
        )
        assert results[0]["ok"] is True
        assert results[0]["already_present"] is True


def test_is_already_present_error():
    assert fabric._is_already_present_error("dnsmasq: duplicate CNAME at line 109")
    assert fabric._is_already_present_error(
        'HTTP 400: {"message":"Item already present","hint":"Uniqueness of items is enforced"}'
    )
    assert not fabric._is_already_present_error("connection refused")


def test_summarize_results():
    assert fabric._summarize_results([])[0] == "error"
    assert fabric._summarize_results([{"ok": True}, {"ok": True}])[0] == "ok"
    assert fabric._summarize_results([{"ok": True}, {"ok": False, "error": "x"}])[0] == "partial"
    assert fabric._summarize_results([{"ok": False}])[0] == "error"


def test_physical_mesh_svg_path_ids():
    hosts = [
        {
            "server_id": 1,
            "name": "RPI5-1",
            "dns_name": "rpi5-1.example",
            "ip": "10.0.0.1",
            "href": "/servers/1",
        },
        {
            "server_id": 2,
            "name": "RPI5-2",
            "dns_name": "rpi5-2.example",
            "ip": "10.0.0.2",
            "href": "/servers/2",
        },
    ]
    services = [
        {
            "id": 10,
            "fqdn": "app.example",
            "backend_server_id": 1,
            "target_server_id": 2,
            "via_proxy": True,
            "docker_project": "app",
            "docker_container": "app",
            "path_kind": "npm_app",
            "path_chain": "app.example → RPI5-2 → RPI5-1",
            "dep_href": None,
            "backend_href": "/servers/1",
        },
        {
            "id": 11,
            "fqdn": "direct.example",
            "backend_server_id": 1,
            "target_server_id": 1,
            "via_proxy": False,
            "docker_project": None,
            "docker_container": None,
            "path_kind": "host",
            "path_chain": "direct.example → RPI5-1",
            "dep_href": None,
            "backend_href": "/servers/1",
        },
    ]
    svg = fabric._build_physical_mesh_svg(hosts, services)
    assert svg["host_count"] == 2
    assert svg["app_count"] == 2
    # Spine infra (internet/gateway/lan) + 2 hosts + 2 apps
    kinds = {n["kind"] for n in svg["nodes"]}
    assert {"internet", "gateway", "lan", "host", "app"} <= kinds
    assert len([n for n in svg["nodes"] if n["kind"] == "host"]) == 2
    apps = [n for n in svg["nodes"] if n["kind"] == "app"]
    assert all(n.get("path_id") is not None for n in apps)
    assert any(e.get("path_id") == 10 and e.get("dashed") for e in svg["edges"])
    hosts_n = [n for n in svg["nodes"] if n["kind"] == "host"]
    assert any(10 in (n.get("path_ids") or []) for n in hosts_n)


def test_logical_mesh_svg_path_ids():
    flows = [
        {
            "id": 7,
            "url": "app.example",
            "via_npm": True,
            "npm_edge": "RPI5-2",
            "dest_host": "RPI5-1",
            "dest_project": "app",
            "dest_container": "app",
            "dest_summary": "RPI5-1 / app / app",
            "path_kind": "npm_app",
            "href": None,
            "dest_host_href": "/servers/1",
        },
        {
            "id": 8,
            "url": "host.example",
            "via_npm": False,
            "npm_edge": None,
            "dest_host": "RPI5-3",
            "dest_project": None,
            "dest_container": None,
            "dest_summary": "RPI5-3",
            "path_kind": "host_identity",
            "href": None,
            "dest_host_href": "/servers/3",
        },
    ]
    svg = fabric._build_logical_mesh_svg(flows)
    assert len(svg["nodes"]) >= 5  # hub + 2 urls + 2 dests
    assert any(n.get("kind") == "hub" for n in svg["nodes"])
    urls = [n for n in svg["nodes"] if n["kind"] == "url"]
    assert {n.get("path_id") for n in urls} == {7, 8}
    assert all("path_id" in e for e in svg["edges"])
    hub = next(n for n in svg["nodes"] if n["kind"] == "hub")
    assert 7 in (hub.get("path_ids") or [])


def test_physical_and_logical_views_include_svg():
    hosts = [
        {
            "server_id": 1,
            "name": "H1",
            "dns_name": "h1.example",
            "ip": "10.0.0.1",
            "href": "/servers/1",
        }
    ]
    services = [
        {
            "id": 1,
            "fqdn": "a.example",
            "backend_server_id": 1,
            "target_server_id": 1,
            "via_proxy": False,
            "path_kind": "host",
            "path_title": "name → host",
            "path_chain": "a.example → H1",
            "hops": [
                {"kind": "name", "label": "a.example"},
                {"kind": "host", "label": "H1", "href": "/servers/1"},
            ],
            "docker_project": None,
            "docker_container": None,
            "last_sync_status": "ok",
            "dep_href": None,
            "backend_href": "/servers/1",
        }
    ]
    phys = fabric._build_physical_view(hosts, services)
    assert phys["racks"]
    assert phys["svg"]["nodes"]
    assert phys["racks"][0]["apps"][0].get("path_id") == 1

    logi = fabric._build_logical_view(services)
    assert logi["flows"]
    assert logi["svg"]["nodes"]
    assert logi["flows"][0].get("id") == 1


def test_build_fabric_view_lazy_and_no_persist():
    """Hub GET must not write DB links; topology payloads are opt-in."""
    session = MagicMock()
    srv = SimpleNamespace(
        id=1,
        name="H1",
        hostname="10.0.0.1",
        dns_name="h1.example",
        ip_address="10.0.0.1",
        dns_ip_override=None,
        dns_manage_a=True,
        sort_order=0,
    )
    rec = SimpleNamespace(
        id=9,
        fqdn="app.example",
        target_server_id=1,
        backend_server_id=1,
        via_proxy=False,
        docker_project=None,
        label=None,
        npm_hint=None,
        record_type="cname",
        certificate_id=None,
        stack_deployment_id=None,
        managed_on_pihole=True,
        external_dns_status="checklist",
        last_sync_status="ok",
        last_synced_at=None,
    )

    class _Exec:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    session.exec = MagicMock(return_value=_Exec([srv]))

    with patch.object(fabric, "list_service_records", return_value=[rec]), patch.object(
        fabric,
        "build_access_path_for_record",
        return_value={
            "path_kind": "host",
            "path_title": "name → host",
            "chain": "app.example → H1",
            "hops": [
                {"kind": "name", "label": "app.example"},
                {"kind": "host", "label": "H1"},
            ],
            "via_proxy": False,
            "docker_project": None,
            "docker_container": None,
            "npm_forward": None,
        },
    ) as bap, patch.object(fabric, "certs_matching_fqdn", return_value=[]), patch(
        "app.services.app_settings.load_settings",
        return_value={
            "network_lan_subnet": "",
            "network_gateway_ip": "",
            "network_public_ip": "",
            "network_public_ip_checked_at": "",
            "network_gateway_kuma_external_id": "",
            "network_public_kuma_external_id": "",
            "network_kuma_integration_id": "",
        },
    ):
        view = fabric.build_fabric_view(session)
        assert view["physical"] == {}
        assert view["logical"] == {}
        assert view["mesh"] == {}
        assert len(view["services"]) == 1
        # Default: no persist_links
        assert bap.call_args.kwargs.get("persist_links") is False

        view2 = fabric.build_fabric_view(
            session, include_physical=True, include_logical=True
        )
        assert view2["physical"].get("svg") is not None
        assert view2["logical"].get("svg") is not None
        assert bap.call_args.kwargs.get("persist_links") is False


def test_ip_in_lan():
    assert fabric._ip_in_lan("192.168.86.10", "192.168.86.0/24") is True
    assert fabric._ip_in_lan("10.0.0.5", "192.168.86.0/24") is False
    assert fabric._ip_in_lan(None, "192.168.86.0/24") is None
    assert fabric._ip_in_lan("192.168.86.10", "") is None


def test_host_is_cloud_rfc1918_fallback():
    """Without LAN CIDR, public IPs are cloud; private stay on LAN."""
    assert fabric._host_is_cloud("203.0.113.50", "") is True
    assert fabric._host_is_cloud("192.168.86.20", "") is False
    assert fabric._host_is_cloud("10.0.0.5", "") is False
    assert fabric._host_is_cloud(None, "") is False
    assert fabric._host_is_cloud("203.0.113.50", "192.168.86.0/24") is True
    assert fabric._host_is_cloud("192.168.86.20", "192.168.86.0/24") is False


def test_physical_mesh_places_cloud_and_infra():
    hosts = [
        {
            "server_id": 1,
            "name": "RPI",
            "dns_name": "rpi.example",
            "ip": "192.168.86.20",
            "href": "/servers/1",
        },
        {
            "server_id": 2,
            "name": "Nomad",
            "dns_name": "nomad.example",
            "ip": "203.0.113.50",
            "href": "/servers/2",
        },
    ]
    services = [
        {
            "id": 1,
            "fqdn": "app.example",
            "backend_server_id": 1,
            "target_server_id": 1,
            "via_proxy": False,
            "path_kind": "app",
            "path_chain": "app.example → RPI",
            "dep_href": None,
            "backend_href": "/servers/1",
        }
    ]
    net = {
        "lan_subnet": "192.168.86.0/24",
        "gateway_ip": "192.168.86.1",
        "public_ip": "198.51.100.9",
        "gateway_kuma": {
            "external_id": "router-ping",
            "label": "Router ping",
            "state": "up",
            "open_url": "https://kuma.example/dashboard/1",
        },
    }
    svg = fabric._build_physical_mesh_svg(hosts, services, network=net)
    kinds = {n["kind"] for n in svg["nodes"]}
    assert "internet" in kinds
    assert "gateway" in kinds
    assert "lan" in kinds
    hosts_n = [n for n in svg["nodes"] if n["kind"] == "host"]
    by_name = {n["label"]: n for n in hosts_n}
    assert by_name["Nomad"].get("is_cloud") is True
    assert by_name["RPI"].get("is_cloud") is False
    assert any(e.get("kind") == "wan" for e in svg["edges"])
    assert any(e.get("kind") == "lan" for e in svg["edges"])
    # Spine edges: Internet→gateway, gateway→LAN, LAN→RPI, Internet→Nomad
    edge_kinds = [e["kind"] for e in svg["edges"]]
    assert edge_kinds.count("wan") >= 2  # uplink + cloud host (and maybe public badge)
    assert edge_kinds.count("lan") >= 2  # gateway→lan + host→lan
    gw = next(n for n in svg["nodes"] if n["kind"] == "gateway")
    assert gw.get("kuma", {}).get("state") == "up"
    assert "up" in (gw.get("sub") or "")


def test_physical_mesh_links_without_network_settings():
    """RFC1918 fallback still builds Internet→router→LAN→hosts + cloud→internet."""
    hosts = [
        {
            "server_id": 1,
            "name": "RPI",
            "dns_name": "rpi.example",
            "ip": "192.168.86.20",
            "href": "/servers/1",
        },
        {
            "server_id": 2,
            "name": "Nomad",
            "dns_name": "nomad.example",
            "ip": "203.0.113.50",
            "href": "/servers/2",
        },
    ]
    svg = fabric._build_physical_mesh_svg(hosts, [], network={})
    kinds = {n["kind"] for n in svg["nodes"]}
    assert "internet" in kinds
    assert "gateway" in kinds
    assert "lan" in kinds
    by_name = {n["label"]: n for n in svg["nodes"] if n["kind"] == "host"}
    assert by_name["Nomad"]["is_cloud"] is True
    assert by_name["RPI"]["is_cloud"] is False
    assert svg["lan_count"] == 1
    assert svg["cloud_count"] == 1
    # Every host must have a topology edge (LAN or WAN)
    assert len([e for e in svg["edges"] if e["kind"] in ("lan", "wan")]) >= 4
    # Selectable without services
    assert by_name["Nomad"].get("node_id") == "host-2"
    assert by_name["Nomad"].get("path_chain")
    assert any(n.get("node_id") == "gateway" for n in svg["nodes"])
    assert any(n.get("node_id") == "lan" for n in svg["nodes"])


def test_physical_mesh_hosts_not_on_router_spine():
    """No LAN host sits on the vertical Router → LAN corridor (top of ring)."""
    hosts = [
        {
            "server_id": i,
            "name": f"H{i}",
            "dns_name": f"h{i}.example",
            "ip": f"192.168.86.{10 + i}",
            "href": f"/servers/{i}",
        }
        for i in range(1, 10)
    ]
    net = {
        "lan_subnet": "192.168.86.0/24",
        "gateway_ip": "192.168.86.1",
        "public_ip": "198.51.100.9",
    }
    svg = fabric._build_physical_mesh_svg(hosts, [], network=net)
    gw = next(n for n in svg["nodes"] if n["kind"] == "gateway")
    lan = next(n for n in svg["nodes"] if n["kind"] == "lan")
    # Corridor: same x as spine, y between gateway and LAN hub
    for h in svg["nodes"]:
        if h["kind"] != "host" or h.get("is_cloud"):
            continue
        # Host centers must stay outside a vertical strip near the spine between gw and lan
        on_spine_x = abs(h["x"] - gw["x"]) < 70
        between = min(gw["y"], lan["y"]) < h["y"] < max(gw["y"], lan["y"]) - 20
        assert not (on_spine_x and between), (
            f"host {h['label']} at ({h['x']},{h['y']}) overlaps router→LAN path"
        )


def test_physical_mesh_caps_satellites_per_host():
    """Dense hosts draw at most PHYSICAL_MESH_MAX_APPS_PER_HOST apps + a +N marker."""
    hosts = [
        {
            "server_id": 1,
            "name": "Busy",
            "dns_name": "busy.example",
            "ip": "10.0.0.1",
            "href": "/servers/1",
        }
    ]
    max_shown = fabric.PHYSICAL_MESH_MAX_APPS_PER_HOST
    total = max_shown + 4
    services = [
        {
            "id": i,
            "fqdn": f"app{i}.example",
            "backend_server_id": 1,
            "target_server_id": 1,
            "via_proxy": False,
            "docker_project": f"p{i}",
            "docker_container": f"c{i}",
            "path_kind": "app",
            "path_chain": f"app{i}.example → Busy",
            "dep_href": None,
            "backend_href": "/servers/1",
        }
        for i in range(1, total + 1)
    ]
    svg = fabric._build_physical_mesh_svg(hosts, services)
    apps = [n for n in svg["nodes"] if n["kind"] == "app"]
    more = [n for n in svg["nodes"] if n["kind"] == "more"]
    assert len(apps) == max_shown
    assert len(more) == 1
    assert more[0]["label"] == f"+{total - max_shown} more"
    assert svg["app_count"] == max_shown
    assert svg["overflow_count"] == total - max_shown
    assert svg["app_total"] == total
    # Hidden path ids live on the overflow marker for focus
    assert len(more[0].get("path_ids") or []) == total - max_shown


def test_physical_mesh_no_overflow_when_under_cap():
    hosts = [
        {
            "server_id": 1,
            "name": "H1",
            "dns_name": "h1.example",
            "ip": "10.0.0.1",
            "href": "/servers/1",
        }
    ]
    services = [
        {
            "id": 1,
            "fqdn": "a.example",
            "backend_server_id": 1,
            "target_server_id": 1,
            "via_proxy": False,
            "path_kind": "host",
            "path_chain": "a.example → H1",
            "dep_href": None,
            "backend_href": "/servers/1",
        }
    ]
    svg = fabric._build_physical_mesh_svg(hosts, services)
    assert not any(n["kind"] == "more" for n in svg["nodes"])
    assert svg.get("overflow_count", 0) == 0
    assert svg["app_count"] == 1


def test_physical_mesh_app_nodes_carry_sync_and_cert():
    hosts = [
        {
            "server_id": 1,
            "name": "H1",
            "dns_name": "h1.example",
            "ip": "10.0.0.1",
            "href": "/servers/1",
        }
    ]
    services = [
        {
            "id": 3,
            "fqdn": "app.example",
            "backend_server_id": 1,
            "target_server_id": 1,
            "via_proxy": False,
            "path_kind": "app",
            "path_chain": "app.example → H1",
            "last_sync_status": "ok",
            "certificate_id": 9,
            "cert_name": "app-cert",
            "dep_href": None,
            "backend_href": "/servers/1",
        }
    ]
    svg = fabric._build_physical_mesh_svg(hosts, services)
    apps = [n for n in svg["nodes"] if n["kind"] == "app"]
    assert apps[0]["sync_status"] == "ok"
    assert apps[0]["has_cert"] is True
    phys = fabric._build_physical_view(hosts, services)
    assert phys["racks"][0]["apps"][0]["has_cert"] is True
    logi = fabric._build_logical_view(services)
    assert logi["flows"][0]["sync_status"] == "ok"
    assert logi["flows"][0]["has_cert"] is True


def test_logical_mesh_dimensions_scale_with_flows():
    flows = [
        {
            "id": i,
            "url": f"u{i}.example",
            "via_npm": i % 2 == 0,
            "npm_edge": "Edge" if i % 2 == 0 else None,
            "dest_host": "H1",
            "dest_project": None,
            "dest_container": None,
            "dest_summary": "H1",
            "path_kind": "app",
            "href": None,
            "dest_host_href": "/servers/1",
        }
        for i in range(1, 16)
    ]
    svg = fabric._build_logical_mesh_svg(flows)
    # 15 urls + 15 dests + hub (even ids use npm)
    assert any(n.get("kind") == "hub" for n in svg["nodes"])
    assert len([n for n in svg["nodes"] if n["kind"] == "url"]) == 15
    assert svg["height"] >= 70 + 15 * 52

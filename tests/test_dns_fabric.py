"""Unit tests for DNS fabric helpers (no live Pi-hole)."""
from __future__ import annotations

import re

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import dns_fabric as fabric


def test_normalize_and_validate_fqdn():
    assert fabric.normalize_fqdn("  RPi5-1.example.com. ") == "rpi5-1.example.com"
    assert fabric.is_valid_fqdn("rpi5-1.example.com")
    assert fabric.is_valid_fqdn("piherder-dev.example.com")
    assert not fabric.is_valid_fqdn("noperiod")
    assert not fabric.is_valid_fqdn("")
    assert not fabric.is_valid_fqdn("-bad.example.com")


def test_host_focus_key_and_map_urls():
    assert fabric.host_focus_key(3) == "n:host-3"
    # Deep links land on the map panel (#map), not list-first chrome
    assert fabric.hosts_map_url() == "/dns/physical#map"
    assert fabric.hosts_map_url(server_id=3) == "/dns/physical?focus=n:host-3#map"
    assert fabric.hosts_map_url(path_id=12) == "/dns/physical?focus=12#map"
    assert fabric.path_map_url() == "/dns/logical#map"
    assert fabric.path_map_url(path_id=12) == "/dns/logical?focus=12#map"


def test_fabric_rack_for_server_backend_and_npm_edge():
    """Rack lists backend apps; target-only proxy edge marks is_npm_edge."""
    backend = SimpleNamespace(
        id=1, name="rpi5-1", dns_name="rpi5-1.example.com",
        hostname="10.0.0.1", ip_address="10.0.0.1", dns_ip_override=None,
    )
    edge = SimpleNamespace(
        id=2, name="npm", dns_name="npm.example.com",
        hostname="10.0.0.2", ip_address="10.0.0.2", dns_ip_override=None,
    )
    rec = SimpleNamespace(
        id=10,
        fqdn="app.example.com",
        backend_server_id=1,
        target_server_id=2,
        via_proxy=True,
        docker_project="web",
        label="App",
        record_type="cname",
        npm_hint=None,
        certificate_id=None,
        last_sync_status="ok",
    )

    session = MagicMock()

    def _get(model, pk):
        if pk == 1:
            return backend
        if pk == 2:
            return edge
        return None

    session.get.side_effect = _get

    with patch.object(fabric.core, "list_service_records", return_value=[rec]), patch.object(
        fabric.core,
        "build_access_path_for_record",
        return_value={
            "via_proxy": True,
            "path_kind": "npm_app",
            "path_title": "via NPM",
            "chain": "app.example.com → npm → rpi5-1 → web",
            "docker_project": "web",
            "docker_container": "web",
        },
    ):
        rack_b = fabric.fabric_rack_for_server(session, 1)
        rack_e = fabric.fabric_rack_for_server(session, 2)

    assert rack_b is not None
    assert rack_b["app_count"] == 1
    assert rack_b["apps"][0]["fqdn"] == "app.example.com"
    assert rack_b["apps"][0]["path_map_url"] == "/dns/logical?focus=10#map"
    assert rack_b["hosts_map_url"] == "/dns/physical?focus=n:host-1#map"
    assert rack_b["is_npm_edge"] is False

    assert rack_e is not None
    assert rack_e["app_count"] == 0
    assert rack_e["is_npm_edge"] is True
    assert rack_e["ingress_count"] == 1
    assert rack_e["hosts_map_url"] == "/dns/physical?focus=n:host-2#map"


def test_fabric_rack_for_server_missing():
    session = MagicMock()
    session.get.return_value = None
    assert fabric.fabric_rack_for_server(session, 99) is None


def test_fabric_path_for_fqdn_from_url():
    rec = SimpleNamespace(
        id=5,
        fqdn="grafana.example.com",
        backend_server_id=1,
        target_server_id=1,
    )
    session = MagicMock()
    with patch.object(fabric.core, "list_service_records", return_value=[rec]):
        hit = fabric.fabric_path_for_fqdn(session, "https://grafana.example.com/login")
        miss = fabric.fabric_path_for_fqdn(session, "https://other.example.com/")
    assert hit is not None
    assert hit["path_id"] == 5
    assert hit["path_map_url"] == "/dns/logical?focus=5#map"
    assert miss is None


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
    assert fabric.suggest_host_dns_name(s, "example.com") == "rpi5-1.example.com"
    assert fabric.suggest_host_dns_name(s, "") == ""


def test_server_name_tokens():
    s = SimpleNamespace(name="RPI5-1", hostname="rpi5-1", dns_name=None)
    tokens = fabric._server_name_tokens(s)
    assert "rpi5-1" in tokens


def test_host_dns_form_defaults_saved_wins():
    s = SimpleNamespace(
        name="RPI5-1",
        hostname="10.0.0.5",
        dns_name="rpi5-1.example.com",
        ip_address="10.0.0.9",
        dns_ip_override=None,
        dns_manage_a=True,
    )
    session = MagicMock()
    with patch.object(fabric.core, "match_pihole_host_for_server", return_value=None):
        d = fabric.host_dns_form_defaults(session, s, base_domain="example.com")
    assert d["dns_name"] == "rpi5-1.example.com"
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
    match = {"domain": "rpi5-1.example.com", "ip": "192.168.1.51", "source": "pi1"}
    with patch.object(fabric.core, "match_pihole_host_for_server", return_value=match):
        d = fabric.host_dns_form_defaults(session, s, base_domain="example.com")
    assert d["dns_name"] == "rpi5-1.example.com"
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
    with patch.object(fabric.core, "match_pihole_host_for_server", return_value=None):
        d = fabric.host_dns_form_defaults(session, s, base_domain="example.com")
    assert d["dns_name"] == "rpi5-2.example.com"
    assert d["ip_address"] == "10.0.0.22"
    assert d["is_saved"] is False


def test_plan_summary_direct_and_proxy():
    s = fabric._plan_summary(
        "app.example.com", "rpi5-3.example.com", "RPI4-1", True, "NPM edge"
    )
    assert "CNAME" in s and "via NPM" in s
    s2 = fabric._plan_summary("app.example.com", "rpi4-1.example.com", "RPI4-1", False, "direct")
    assert "direct" in s2


def test_build_access_path_host_direct():
    session = MagicMock()
    host = SimpleNamespace(
        id=8, name="3DPRINT", dns_name="3dprint.example.com", hostname="3dprint.example.com",
        ip_address="192.168.86.41", dns_ip_override=None,
    )
    with patch.object(fabric.core, "_servers_by_id", return_value={8: host}), patch.object(fabric.core, "_find_npm_forward", return_value=None
    ), patch.object(fabric.core, "_find_docker_container", return_value=None), patch.object(fabric.core, "resolve_app_layers",
        return_value={"docker_project": None, "docker_container": None, "source": ""},
    ):
        path = fabric.build_access_path(
            session,
            fqdn="3dprint.example.com",
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
    host = SimpleNamespace(dns_name="3dprint.example.com")
    assert fabric.is_host_identity_name("3dprint.example.com", host)
    assert not fabric.is_host_identity_name("app.example.com", host)


def test_build_access_path_npm_app():
    session = MagicMock()
    edge = SimpleNamespace(
        id=5, name="RPI5-3", dns_name="rpi5-3.example.com", hostname="rpi5-3",
        ip_address="192.168.86.35", dns_ip_override=None,
    )
    backend = SimpleNamespace(
        id=1, name="RPI5-2", dns_name="rpi5-2.example.com", hostname="rpi5-2",
        ip_address="192.168.86.49", dns_ip_override=None,
    )
    with patch.object(fabric.core, "_servers_by_id", return_value={5: edge, 1: backend}), patch.object(fabric.core, "_find_npm_forward",
        return_value={"forward_host": "192.168.86.49", "forward_port": 8090, "domain_names": ["download.example.com"]},
    ), patch.object(fabric.core, "_find_docker_container", return_value="qbittorrent"), patch.object(fabric.core, "resolve_app_layers",
        return_value={"docker_project": "qbittorrent", "docker_container": "qbittorrent", "source": "explicit"},
    ):
        path = fabric.build_access_path(
            session,
            fqdn="download.example.com",
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
        id=4, name="RPI5-6", dns_name="rpi5-6.example.com", hostname="rpi5-6",
        ip_address="192.168.86.34", dns_ip_override=None,
    )
    with patch.object(fabric.core, "_servers_by_id", return_value={4: host}), patch.object(fabric.core, "_find_npm_forward", return_value=None
    ), patch.object(fabric.core, "resolve_app_layers",
        return_value={
            "docker_project": "grafana",
            "docker_container": "grafana",
            "source": "kuma",
        },
    ):
        path = fabric.build_access_path(
            session,
            fqdn="grafana.example.com",
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
                {"kind": "name", "label": "app.example.com", "sub": "CNAME"},
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
                {"kind": "name", "label": "3dprint.example.com", "sub": "name"},
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
    with patch.object(fabric.core.reg, "list_integrations", return_value=[integ]), patch.object(
        fabric.core.reg, "is_pihole_primary", return_value=True
    ), patch.object(fabric.core.reg, "pihole_password", return_value="secret"), patch.object(
        fabric.core.reg, "tls_verify", return_value=True
    ), patch.object(fabric.core.ph, "login") as login, patch.object(
        fabric.core.ph, "logout"
    ), patch.object(fabric.core.ph, "add_dns_cname") as add_c:
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
    with patch.object(fabric.core.reg, "list_integrations", return_value=[integ]), patch.object(
        fabric.core.reg, "is_pihole_primary", return_value=True
    ), patch.object(fabric.core.reg, "pihole_password", return_value="secret"), patch.object(
        fabric.core.reg, "tls_verify", return_value=True
    ), patch.object(fabric.core.ph, "login") as login, patch.object(
        fabric.core.ph, "logout"
    ), patch.object(
        fabric.core.ph,
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


def test_physical_mesh_includes_discovered_hosts():
    """Unlinked nmap devices appear on Hosts map without linking to Server."""
    hosts = [
        {
            "server_id": 1,
            "name": "Fleet Pi",
            "dns_name": "pi.example",
            "ip": "10.0.0.1",
            "href": "/servers/1",
        },
        {
            "server_id": None,
            "discovery_id": 99,
            "is_discovered": True,
            "name": "Printer",
            "dns_name": None,
            "ip": "10.0.0.50",
            "device_kind": "printer",
            "device_kind_label": "Printer",
            "device_kind_short": "Print",
            "href": "/integrations/1?tab=devices&device=99",
        },
    ]
    svg = fabric._build_physical_mesh_svg(
        hosts, [], network={"lan_subnet": "10.0.0.0/24", "gateway_ip": "10.0.0.1"}
    )
    host_nodes = [n for n in svg["nodes"] if n["kind"] == "host"]
    assert len(host_nodes) == 2
    disc = next(n for n in host_nodes if n.get("is_discovered"))
    fleet = next(n for n in host_nodes if not n.get("is_discovered"))
    assert disc.get("discovery_id") == 99
    assert disc.get("node_id") == "host-d-99"
    assert disc.get("device_kind") == "printer"
    # Compact discovered chip vs full fleet card
    assert disc.get("hw", 99) < fleet.get("hw", 0)
    assert disc.get("hh", 99) < fleet.get("hh", 0)
    # Discovered sits further from LAN centre than fleet (outer ring)
    lan = next(n for n in svg["nodes"] if n["kind"] == "lan")
    import math

    def _dist(n):
        return math.hypot(n["x"] - lan["x"], n["y"] - lan["y"])

    assert _dist(disc) > _dist(fleet)
    assert svg.get("discovered_count") == 1
    assert svg.get("fleet_count") == 1
    phys = fabric._build_physical_view(
        hosts, [], network={"lan_subnet": "10.0.0.0/24"}
    )
    assert phys["discovered_count"] == 1
    assert any(r.get("is_discovered") for r in phys["racks"])


def test_physical_mesh_fleet_ring_not_inflated_by_discovery():
    """Many discovered devices must not shrink fleet/app layout into a corner."""
    fleet = [
        {
            "server_id": 1,
            "name": "RPI",
            "dns_name": "rpi.example",
            "ip": "10.0.0.10",
            "href": "/servers/1",
        }
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
    net = {"lan_subnet": "10.0.0.0/24", "gateway_ip": "10.0.0.1"}
    svg_fleet_only = fabric._build_physical_mesh_svg(fleet, services, network=net)
    disc = [
        {
            "server_id": None,
            "discovery_id": i,
            "is_discovered": True,
            "name": f"d{i}",
            "ip": f"10.0.0.{50 + i}",
            "href": f"/integrations/1?tab=devices&device={i}",
        }
        for i in range(1, 25)
    ]
    svg_both = fabric._build_physical_mesh_svg(fleet + disc, services, network=net)
    h_only = next(n for n in svg_fleet_only["nodes"] if n["kind"] == "host")
    h_both = next(
        n for n in svg_both["nodes"] if n["kind"] == "host" and not n.get("is_discovered")
    )
    # Fleet host position (and full card size) unchanged by discovery density
    assert abs(h_only["x"] - h_both["x"]) < 1.5
    assert abs(h_only["y"] - h_both["y"]) < 1.5
    assert h_both.get("hw") == h_only.get("hw") or h_both.get("hw", 62) >= 60
    app_only = next(n for n in svg_fleet_only["nodes"] if n["kind"] == "app")
    app_both = next(n for n in svg_both["nodes"] if n["kind"] == "app")
    assert abs(app_only["x"] - app_both["x"]) < 2.0
    assert abs(app_only["y"] - app_both["y"]) < 2.0


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

    with patch.object(fabric.core, "list_service_records", return_value=[rec]), patch.object(
        fabric.core,
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
    ) as bap, patch.object(fabric.core, "certs_matching_fqdn", return_value=[]), patch(
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
    assert gw.get("split") is True
    assert "up" in (gw.get("path_chain") or "")
    assert "198.51.100.9" in (gw.get("sub_top") or "")
    assert "192.168.86.1" in (gw.get("sub_bottom") or gw.get("lan_ip") or "")
    # No separate Public IP card — WAN IP is on the Router
    assert not any(n.get("kind") == "public_ip" for n in svg["nodes"])


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


def test_physical_mesh_lan_hosts_inside_zone_apps_outside():
    """LAN server cards sit inside the zone ellipse; apps outside it."""
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
            "name": "Pi2",
            "dns_name": "pi2.example",
            "ip": "192.168.86.21",
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
    }
    svg = fabric._build_physical_mesh_svg(hosts, services, network=net)
    lan = next(n for n in svg["nodes"] if n["kind"] == "lan")
    assert lan.get("zone_rx") and lan.get("zone_ry")
    assert lan.get("sub") == "192.168.86.0/24"
    # Circular centre badge
    assert lan.get("rx") == lan.get("ry")
    zrx, zry = float(lan["zone_rx"]), float(lan["zone_ry"])
    lcx, lcy = float(lan["x"]), float(lan["y"])

    def _inside(x: float, y: float, pad: float = 0.92) -> bool:
        return ((x - lcx) / zrx) ** 2 + ((y - lcy) / zry) ** 2 <= pad**2

    for h in svg["nodes"]:
        if h["kind"] != "host" or h.get("is_cloud"):
            continue
        assert _inside(float(h["x"]), float(h["y"])), (
            f"LAN host {h['label']} at ({h['x']},{h['y']}) should be inside zone"
        )
    apps = [n for n in svg["nodes"] if n["kind"] == "app"]
    assert apps
    for a in apps:
        assert not _inside(float(a["x"]), float(a["y"]), pad=1.02), (
            f"app {a['label']} at ({a['x']},{a['y']}) should be outside LAN zone"
        )
    # Router sits on the top rim of the LAN zone; Internet above, no public-IP card
    inet = next(n for n in svg["nodes"] if n["kind"] == "internet")
    gw = next(n for n in svg["nodes"] if n["kind"] == "gateway")
    assert not any(n.get("kind") == "public_ip" for n in svg["nodes"])
    assert (inet.get("sub") or "") == ""
    assert gw.get("split") is True
    assert gw.get("sub_top") == "198.51.100.9"
    assert gw.get("sub_bottom") == "192.168.86.1"
    zone_top = lcy - zry
    assert abs(gw["y"] - zone_top) < 2.0  # on the rim
    assert inet["y"] < gw["y"]
    assert abs(inet["x"] - gw["x"]) < 1.0  # centred spine


def test_physical_mesh_cloud_hooks_internet_side():
    """Cloud host (e.g. Nomad) attaches to the side of the Internet ellipse."""
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
    net = {
        "lan_subnet": "192.168.86.0/24",
        "gateway_ip": "192.168.86.1",
        "public_ip": "198.51.100.9",
    }
    svg = fabric._build_physical_mesh_svg(hosts, [], network=net)
    inet = next(n for n in svg["nodes"] if n["kind"] == "internet")
    nomad = next(n for n in svg["nodes"] if n.get("label") == "Nomad")
    assert nomad.get("is_cloud") is True
    # Side of cloud: similar y to Internet, offset in x beyond ellipse
    assert abs(nomad["y"] - inet["y"]) < 30
    assert abs(nomad["x"] - inet["x"]) > float(inet.get("rx") or 52)
    assert not any(n.get("kind") == "public_ip" for n in svg["nodes"])


def test_physical_mesh_edges_meet_node_borders():
    """Connectors clip to card/ellipse borders, not centres."""
    import math

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
    }
    svg = fabric._build_physical_mesh_svg(hosts, services, network=net)
    rpi = next(n for n in svg["nodes"] if n["label"] == "RPI")
    lan = next(n for n in svg["nodes"] if n["kind"] == "lan")
    app = next(n for n in svg["nodes"] if n["kind"] == "app")
    host_hw, host_hh = 62.0, 30.0
    lan_edges = [e for e in svg["edges"] if e.get("kind") == "lan"]
    assert lan_edges

    def near_host_border(x: float, y: float, hx: float, hy: float) -> bool:
        dx, dy = abs(x - hx), abs(y - hy)
        on_side = abs(dx - host_hw) < 2.5 and dy <= host_hh + 2.5
        on_topbot = abs(dy - host_hh) < 2.5 and dx <= host_hw + 2.5
        return on_side or on_topbot

    def near_circle(x: float, y: float, cx: float, cy: float, r: float) -> bool:
        return abs(math.hypot(x - cx, y - cy) - r) < 2.5

    found_host_lan = False
    for e in lan_edges:
        for xa, ya, xb, yb in (
            (e["x1"], e["y1"], e["x2"], e["y2"]),
            (e["x2"], e["y2"], e["x1"], e["y1"]),
        ):
            if near_host_border(xa, ya, rpi["x"], rpi["y"]) and near_circle(
                xb, yb, lan["x"], lan["y"], float(lan["rx"])
            ):
                found_host_lan = True
    assert found_host_lan, "host↔LAN edge should hit host card edge and LAN badge rim"

    land = [e for e in svg["edges"] if e.get("kind") == "land" and e.get("path_id") == 1]
    assert land
    e = land[0]
    app_hw, app_hh = 70.0, 22.0
    ends = [(e["x1"], e["y1"]), (e["x2"], e["y2"])]

    def near_app(x: float, y: float) -> bool:
        dx, dy = abs(x - app["x"]), abs(y - app["y"])
        return (abs(dx - app_hw) < 2.5 and dy <= app_hh + 2.5) or (
            abs(dy - app_hh) < 2.5 and dx <= app_hw + 2.5
        )

    assert any(near_app(x, y) for x, y in ends)
    assert any(near_host_border(x, y, rpi["x"], rpi["y"]) for x, y in ends)


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


def test_fabric_index_for_server_case_insensitive_and_cheap():
    """Docker project keys are lowercased; no access-path resolve required."""
    rec_a = SimpleNamespace(
        id=1,
        fqdn="a.example.com",
        backend_server_id=7,
        docker_project="MyApp",
    )
    rec_b = SimpleNamespace(
        id=2,
        fqdn="b.example.com",
        backend_server_id=7,
        docker_project="myapp",
    )
    rec_other = SimpleNamespace(
        id=3,
        fqdn="c.example.com",
        backend_server_id=8,
        docker_project="other",
    )
    session = MagicMock()

    def _boom(*_a, **_k):
        raise AssertionError("fabric_index must not call build_access_path_for_record")

    with patch.object(fabric.core, "list_service_records", return_value=[rec_a, rec_b, rec_other]), patch.object(
        fabric.core, "build_access_path_for_record", side_effect=_boom
    ):
        idx = fabric.fabric_index_for_server(session, 7)
    by = idx["by_project"]
    assert "myapp" in by
    assert by["myapp"]["count"] == 2
    assert by["myapp"]["project"] in ("MyApp", "myapp")
    assert by["myapp"]["path_map_url"].startswith("/dns/logical")
    assert "other" not in by
    assert idx["hosts_map_url"] == "/dns/physical?focus=n:host-7#map"


def test_calendar_today_and_date_preset_app_tz():
    from app.services import app_settings as aset

    with patch.object(aset, "get_app_timezone", return_value="UTC"):
        today = aset.calendar_today_in_app_tz()
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", today)
        r = aset.calendar_date_range_preset(7)
        assert r["date_to"] == today
        assert r["date_from"] <= r["date_to"]

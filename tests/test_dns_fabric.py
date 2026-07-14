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

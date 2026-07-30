"""G Ports — published port parsing for stack topology."""
from __future__ import annotations

from app.services.dns_fabric.ports import (
    enrich_container_ports,
    format_ports_short,
    guess_port_role,
    parse_published_ports,
    PORT_ROLE_DB,
    PORT_ROLE_DNS,
    PORT_ROLE_WEB,
)


def test_parse_arrow_mappings():
    raw = "0.0.0.0:8080->80/tcp, :::443->443/tcp, 127.0.0.1:5432->5432/tcp"
    parsed = parse_published_ports(raw)
    labels = [p["label"] for p in parsed]
    assert any("8080→80" in x for x in labels)
    assert any("443→443" in x for x in labels)
    assert all(p["published"] for p in parsed if "→" in p["label"])


def test_parse_list_ports_and_empty():
    assert parse_published_ports(None, None) == []
    assert parse_published_ports("—") == []
    p = parse_published_ports(None, ["0.0.0.0:3000->3000/tcp"])
    assert len(p) == 1
    assert p[0]["host"] == "3000"


def test_format_and_enrich():
    c = {
        "ports_display": "0.0.0.0:8000->8000/tcp, 0.0.0.0:8443->443/tcp",
        "ports": [],
    }
    enrich_container_ports(c)
    assert c["ports_parsed"]
    assert "8000→8000" in c["ports_short"] or "8000" in c["ports_short"]
    assert c["ports_summary"]
    assert format_ports_short(c["ports_parsed"], limit=1).endswith("+1") or "8000" in format_ports_short(
        c["ports_parsed"], limit=1
    )
    assert all("role" in p for p in c["ports_parsed"])


def test_guess_port_role_pihole_style():
    assert guess_port_role(host_port=53, container_port=53, proto="udp") == PORT_ROLE_DNS
    assert (
        guess_port_role(
            host_port=443,
            container_port=443,
            service_name="pihole",
            image="pihole/pihole:latest",
        )
        == PORT_ROLE_WEB
    )
    assert guess_port_role(host_port=5432, image="postgres:16") == PORT_ROLE_DB


def test_enrich_adds_role_labels():
    c = {
        "name": "pihole",
        "compose_service": "pihole",
        "image": "pihole/pihole:latest",
        "ports_display": "0.0.0.0:53->53/tcp, 0.0.0.0:443->443/tcp",
    }
    enrich_container_ports(c)
    roles = {p["host"]: p["role"] for p in c["ports_parsed"]}
    assert roles.get("53") == PORT_ROLE_DNS
    assert roles.get("443") == PORT_ROLE_WEB
    assert any(p.get("role_label") == "DNS" for p in c["ports_parsed"])


def test_fleet_link_targets_shape():
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from app.services.dns_fabric.stack_panel import _fleet_link_targets

    s1 = SimpleNamespace(id=1, name="alpha", sort_order=0)
    s2 = SimpleNamespace(id=2, name="beta", sort_order=1)

    class FakeResult:
        def all(self):
            return [s1, s2]

    session = MagicMock()
    session.exec = MagicMock(return_value=FakeResult())
    inv = {
        "v": 2,
        "projects": [
            {
                "name": "web",
                "containers": [
                    {"name": "nginx", "compose_service": "web"},
                    {"name": "api", "compose_service": "api"},
                ],
            }
        ],
    }
    with patch(
        "app.services.dns_fabric.stack_panel.inv_svc.parse_inventory",
        side_effect=lambda s: inv if s.id == 2 else None,
    ):
        out = _fleet_link_targets(session, current_server_id=1)
    assert len(out) == 2
    cur = next(x for x in out if x["id"] == 1)
    assert cur["is_current"] is True
    other = next(x for x in out if x["id"] == 2)
    assert other["projects"][0]["name"] == "web"
    assert "web" in other["projects"][0]["containers"]

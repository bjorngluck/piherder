"""M4 host port inventory + sticky PortAnnotation."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.dns_fabric import host_ports as hp
from app.services.dns_fabric.ports import PORT_ROLE_DNS, PORT_ROLE_WEB


def _server_with_inventory():
    inv = {
        "v": 2,
        "projects": [
            {
                "name": "pihole",
                "containers": [
                    {
                        "name": "pihole",
                        "compose_service": "pihole",
                        "image": "pihole/pihole:latest",
                        "running": True,
                        "ports_display": "0.0.0.0:53->53/tcp, 0.0.0.0:443->443/tcp",
                    }
                ],
            },
            {
                "name": "dbstack",
                "containers": [
                    {
                        "name": "postgres",
                        "compose_service": "db",
                        "image": "postgres:16",
                        "running": True,
                        "ports_display": "0.0.0.0:5432->5432/tcp",
                    }
                ],
            },
        ],
        "orphan_containers": [],
    }
    return SimpleNamespace(
        id=7,
        name="rpi-lab",
        docker_inventory_json=json.dumps(inv),
        docker_inventory_status="ok",
    )


def test_docker_published_rows_and_roles():
    server = _server_with_inventory()
    with patch(
        "app.services.dns_fabric.host_ports.inv_svc.parse_inventory",
        return_value=json.loads(server.docker_inventory_json),
    ):
        rows = hp._docker_published_rows(server)
    by_port = {r["host_port"]: r for r in rows}
    assert 53 in by_port and by_port[53]["role"] == PORT_ROLE_DNS
    assert 443 in by_port and by_port[443]["role"] == PORT_ROLE_WEB
    assert by_port[53]["owner_project"] == "pihole"
    assert by_port[5432]["owner_project"] == "dbstack"


def test_build_inventory_merges_nmap_and_sticky():
    server = _server_with_inventory()
    ann = SimpleNamespace(
        id=1,
        host_port=443,
        proto="tcp",
        role_key="web",
        label="admin UI",
        note="Pi-hole web",
        owner_project=None,
        owner_container=None,
        hide=False,
    )
    # nmap has 22 (noise) + 8080 observed-only
    device = SimpleNamespace(
        id=99,
        display_name=None,
        hostname="rpi",
        ip_address="10.0.0.7",
        ports_json=json.dumps(
            [
                {"port": 22, "protocol": "tcp", "state": "open", "service": "ssh"},
                {"port": 53, "protocol": "tcp", "state": "open", "service": "domain"},
                {"port": 8080, "protocol": "tcp", "state": "open", "service": "http"},
            ]
        ),
    )

    session = MagicMock()

    def _get(model, pk):
        if getattr(model, "__name__", "") == "Server" or model is type(server):
            return server
        return None

    session.get = MagicMock(side_effect=lambda m, pk: server)

    with patch(
        "app.services.dns_fabric.host_ports.inv_svc.parse_inventory",
        return_value=json.loads(server.docker_inventory_json),
    ), patch(
        "app.services.dns_fabric.host_ports._linked_nmap_device",
        return_value=device,
    ), patch(
        "app.services.dns_fabric.host_ports.load_annotations_for_server",
        return_value={"443/tcp": ann},
    ):
        out = hp.build_host_port_inventory(session, server_id=7, show_noise=False)

    assert out["ok"] is True
    ports = {p["host_port"]: p for p in out["ports"]}
    assert 22 not in ports  # noise hidden
    assert ports[53]["source"] == "both"
    assert ports[443]["role_sticky"] is True
    assert ports[443]["sticky_label"] == "admin UI"
    assert 8080 in ports and ports[8080]["source"] == "nmap"
    assert out["stack_count"] >= 2
    assert "ports" in out["summary_line"].lower() or out["total_count"] >= 3


def test_focus_project_splits_other_on_host():
    server = _server_with_inventory()
    session = MagicMock()
    session.get = MagicMock(return_value=server)
    with patch(
        "app.services.dns_fabric.host_ports.inv_svc.parse_inventory",
        return_value=json.loads(server.docker_inventory_json),
    ), patch(
        "app.services.dns_fabric.host_ports._linked_nmap_device",
        return_value=None,
    ), patch(
        "app.services.dns_fabric.host_ports.load_annotations_for_server",
        return_value={},
    ):
        out = hp.build_host_port_inventory(
            session, server_id=7, focus_project="pihole"
        )
    focus = [p for p in out["ports"] if p.get("in_focus_stack")]
    other = [p for p in out["ports"] if p.get("other_on_host")]
    assert any(p["host_port"] == 53 for p in focus)
    assert any(p["host_port"] == 5432 for p in other)


def test_build_host_ports_expand_payload():
    server = _server_with_inventory()
    session = MagicMock()
    session.get = MagicMock(return_value=server)
    with patch(
        "app.services.dns_fabric.host_ports.inv_svc.parse_inventory",
        return_value=json.loads(server.docker_inventory_json),
    ), patch(
        "app.services.dns_fabric.host_ports._linked_nmap_device",
        return_value=None,
    ), patch(
        "app.services.dns_fabric.host_ports.load_annotations_for_server",
        return_value={},
    ):
        out = hp.build_host_ports_expand_payload(session, server_id=7)
    assert out["ok"] is True
    assert out["node_id"] == "host-7"
    # Service-first: pihole container owns 53 + 443
    assert out["services"]
    pi = next(s for s in out["services"] if s["project"] == "pihole")
    assert pi["kind"] == "service"
    assert pi["port_count"] >= 2
    assert len(pi["ports"]) <= 5
    ports = {p["host_port"] for p in pi["ports"]}
    assert 53 in ports and 443 in ports
    assert any(e["kind"] == "host_service" for e in out["edges"])
    assert out["panel_url"].startswith("/dns/host-ports-panel")


def test_apply_sticky_to_parsed():
    parsed = [
        {
            "host": "53",
            "proto": "tcp",
            "label": "53→53/tcp",
            "published": True,
            "role": "other",
            "role_label": "Other",
        }
    ]
    ann = SimpleNamespace(
        role_key=PORT_ROLE_DNS,
        label=None,
        note=None,
        hide=False,
    )
    hp.apply_sticky_to_parsed(parsed, {"53/tcp": ann})
    assert parsed[0]["role"] == PORT_ROLE_DNS
    assert parsed[0]["role_sticky"] is True

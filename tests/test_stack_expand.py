"""P4 stack expand payload for path map."""
from __future__ import annotations

from unittest.mock import patch

from app.services.dns_fabric import stack_expand as se


def test_expand_not_found():
    session = object()
    with patch.object(
        se,
        "build_stack_panel",
        return_value={"ok": False, "error": "missing", "code": "not_found"},
    ):
        out = se.build_stack_expand_payload(session, service_id=99)
    assert out["ok"] is False


def test_expand_containers_and_confirmed_edges_only():
    panel = {
        "ok": True,
        "service_id": 18,
        "server_id": 5,
        "server_name": "rpi",
        "project": "piherder",
        "fqdn": "app.lab",
        "containers": [
            {
                "compose_service": "web",
                "name": "web",
                "role": "app",
                "running": True,
                "mon_status": "covered",
                "ports": ["8000"],
            },
            {
                "compose_service": "db",
                "name": "db",
                "role": "data",
                "running": True,
                "mon_status": "infra",
                "ports": [],
            },
        ],
        "confirmed_edges": [
            {
                "from_container": "web",
                "to_container": "db",
                "kind": "depends_on",
                "source": "accepted",
                "same_project": True,
                "same_host": True,
            },
            {
                "from_container": "",
                "to_container": "db",
                "kind": "talks_to",
                "source": "manual",
                "same_project": True,
            },
        ],
        "suggested_edges": [
            {"from": "web", "to": "redis", "source": "compose"},
        ],
    }
    with patch.object(se, "build_stack_panel", return_value=panel):
        out = se.build_stack_expand_payload(session=object(), service_id=18)
    assert out["ok"] is True
    assert out["path_id"] == 18
    assert len(out["containers"]) == 2
    assert out["containers"][0]["id"] == "web"
    # only confirmed with both containers
    assert len(out["edges"]) == 1
    assert out["edges"][0]["from"] == "web"
    assert out["edges"][0]["to"] == "db"
    assert out["summary"]["edge_count"] == 1

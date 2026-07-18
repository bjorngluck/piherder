"""P5 monitored container down alerts from inventory."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import stack_monitor as sm


def test_fingerprint():
    assert "stack_container_down:1:piherder:web" == sm.fingerprint(1, "PiHerder", "Web")


def test_scan_skips_unbound():
    inv = {
        "v": 2,
        "projects": [
            {
                "name": "piherder",
                "containers": [
                    {"name": "web", "compose_service": "web", "running": False},
                ],
            }
        ],
    }
    server = SimpleNamespace(id=5, name="rpi", docker_inventory_json=json.dumps(inv))
    session = MagicMock()
    with (
        patch.object(sm, "inventory_down_alerts_enabled", return_value=True),
        patch.object(sm.inv_svc, "parse_inventory", return_value=inv),
        patch.object(sm, "_bindings_by_server", return_value=[]),
    ):
        out = sm.scan_server_inventory_for_down_alerts(session, server)
    assert out["alerted"] == 0


def test_scan_alerts_bound_down_and_resolves_up():
    inv = {
        "v": 2,
        "projects": [
            {
                "name": "piherder",
                "containers": [
                    {
                        "name": "web",
                        "compose_service": "web",
                        "running": False,
                    },
                    {
                        "name": "db",
                        "compose_service": "db",
                        "running": True,
                    },
                ],
            }
        ],
    }
    bind_web = SimpleNamespace(
        id=10,
        docker_project="piherder",
        docker_container="web",
        external_label="Web HTTPS",
        external_id="1",
        last_state="down",
    )
    bind_db = SimpleNamespace(
        id=11,
        docker_project="piherder",
        docker_container="db",
        external_label="DB",
        external_id="2",
        last_state="up",
    )
    server = SimpleNamespace(id=5, name="rpi")
    session = MagicMock()
    alerts = []
    resolves = []

    def _upsert(session, **kwargs):
        del session
        alerts.append(kwargs)
        return SimpleNamespace(id=1)

    def _resolve(session, fp):
        del session
        resolves.append(fp)
        return 1

    def _bound(binds, *, project, container, compose_service):
        key = (container or compose_service or "").strip()
        if key == "web":
            return bind_web
        if key == "db":
            return bind_db
        return None

    with (
        patch.object(sm, "inventory_down_alerts_enabled", return_value=True),
        patch.object(sm.inv_svc, "parse_inventory", return_value=inv),
        patch.object(sm, "_bindings_by_server", return_value=[bind_web, bind_db]),
        patch.object(sm, "_container_bound", side_effect=_bound),
        patch.object(sm.notif_svc, "upsert_notification", side_effect=_upsert),
        patch.object(sm.notif_svc, "resolve_by_fingerprint", side_effect=_resolve),
    ):
        out = sm.scan_server_inventory_for_down_alerts(session, server)

    assert out["alerted"] == 1
    assert out["resolved"] == 1
    assert alerts[0]["type"] == "stack_container_down"
    assert "web" in alerts[0]["title"]
    assert any("db" in r for r in resolves)

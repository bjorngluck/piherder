"""Runtime stack side panel (FEATURE_PLAN_RUNTIME_TOPOLOGY P1)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.dns_fabric import stack_panel as sp


def test_guess_container_role_data_and_cache():
    assert (
        sp.guess_container_role(
            name="db", image="postgres:16", compose_service="db"
        )
        == "data"
    )
    assert (
        sp.guess_container_role(
            name="redis", image="redis:7", compose_service="cache"
        )
        == "cache"
    )
    assert (
        sp.guess_container_role(
            name="caddy", image="caddy:2", compose_service="caddy"
        )
        == "edge"
    )
    assert (
        sp.guess_container_role(
            name="web", image="piherder:local", compose_service="web"
        )
        == "app"
    )


def test_guess_role_celery_is_queue():
    assert (
        sp.guess_container_role(
            name="celery-worker", image="app:1", compose_service="worker"
        )
        == "queue"
    )


def test_build_stack_panel_not_found():
    session = MagicMock()
    session.get.return_value = None
    out = sp.build_stack_panel(session, service_id=99)
    assert out["ok"] is False
    assert out["code"] == "not_found"


def test_build_stack_panel_from_server_project():
    inv = {
        "v": 2,
        "projects": [
            {
                "name": "piherder",
                "compose_graph": {
                    "depends_on": {"web": ["db", "redis"], "celery-worker": ["db"]},
                    "service_names": ["web", "db", "redis", "celery-worker"],
                },
                "containers": [
                    {
                        "name": "web",
                        "compose_service": "web",
                        "image": "piherder:local",
                        "running": True,
                        "ports_display": "0.0.0.0:8000->8000/tcp",
                        "ports": ["8000:8000"],
                    },
                    {
                        "name": "db",
                        "compose_service": "db",
                        "image": "postgres:16",
                        "running": True,
                        "ports_display": "",
                        "ports": [],
                    },
                    {
                        "name": "redis",
                        "compose_service": "redis",
                        "image": "redis:7",
                        "running": False,
                        "ports_display": "",
                    },
                ],
            }
        ],
        "meta": {"project_count": 1, "container_count": 3},
    }
    server = SimpleNamespace(
        id=5,
        name="rpi-core",
        docker_inventory_json=json.dumps(inv),
        docker_inventory_status="ok",
        docker_inventory_at=None,
        docker_inventory_error=None,
        container_patch_enabled=True,
    )
    session = MagicMock()
    session.get.return_value = server

    bind_web = SimpleNamespace(
        id=1,
        integration_id=2,
        server_id=5,
        role="service",
        docker_project="piherder",
        docker_container="web",
        external_id="10",
        external_label="PiHerder HTTPS",
        last_state="up",
    )

    def _exec(stmt):
        # IntegrationBinding query — return list-like
        m = MagicMock()
        m.all.return_value = [bind_web]
        return m

    session.exec.side_effect = _exec

    with (
        patch.object(sp.cov, "kuma_integrations_enabled", return_value=[]),
        patch.object(sp.cov, "_mute_patterns", return_value=list(sp.cov.DEFAULT_INFRA_MUTE_PATTERNS)),
        patch(
            "app.services.dns_fabric.stack_panel.inv_svc.parse_inventory",
            return_value=inv,
        ),
        patch(
            "app.services.dns_fabric.stack_panel.inv_svc.inventory_meta",
            return_value={
                "status": "ok",
                "at": None,
                "error": None,
                "has_snapshot": True,
            },
        ),
        patch(
            "app.services.runtime_edges.partition_for_panel",
            side_effect=lambda session, server_id, project, suggestions: {
                "confirmed": [],
                "suggested": list(suggestions or []),
                "dismissed_count": 0,
            },
        ),
    ):
        out = sp.build_stack_panel(session, server_id=5, project="piherder")

    assert out["ok"] is True
    assert out["project"] == "piherder"
    assert out["server_name"] == "rpi-core"
    assert out["summary"]["total"] == 3
    assert out["summary"]["running"] == 2
    names = {c["compose_service"]: c for c in out["containers"]}
    assert names["web"]["mon_status"] == "covered"
    assert names["web"]["role"] == "app"
    assert "8000" in names["web"]["ports"]
    assert names["db"]["role"] == "data"
    assert names["db"]["is_infra"] is True
    assert names["db"]["mon_status"] in ("infra", "muted")
    assert names["redis"]["running"] is False
    assert names["redis"]["role"] == "cache"
    pairs = {(e["from"], e["to"]) for e in out.get("suggested_edges") or []}
    assert ("web", "db") in pairs
    assert ("web", "redis") in pairs
    assert out["summary"]["has_compose_graph"] is True


def test_build_stack_panel_service_record():
    rec = SimpleNamespace(
        id=42,
        fqdn="app.lab",
        label="App",
        docker_project="myapp",
        backend_server_id=3,
        stack_deployment_id=None,
        target_server_id=3,
    )
    inv = {
        "v": 1,
        "projects": [
            {
                "name": "myapp",
                "containers": [
                    {
                        "name": "app",
                        "compose_service": "app",
                        "image": "app:1",
                        "running": True,
                        "ports_display": "8080:80",
                    }
                ],
            }
        ],
    }
    server = SimpleNamespace(
        id=3,
        name="pi1",
        docker_inventory_json=json.dumps(inv),
        docker_inventory_status="ok",
        docker_inventory_at=None,
        docker_inventory_error=None,
    )

    def _get(model, pk):
        # SQLModel session.get(Model, id)
        name = getattr(model, "__name__", str(model))
        if "ServiceDns" in name or pk == 42:
            return rec
        if pk == 3:
            return server
        return None

    session = MagicMock()
    session.get.side_effect = _get
    session.exec.return_value.all.return_value = []

    with (
        patch.object(sp.cov, "kuma_integrations_enabled", return_value=[]),
        patch.object(sp.cov, "_mute_patterns", return_value=[]),
        patch(
            "app.services.dns_fabric.stack_panel.inv_svc.parse_inventory",
            return_value=inv,
        ),
        patch(
            "app.services.dns_fabric.stack_panel.inv_svc.inventory_meta",
            return_value={"status": "ok", "at": None, "error": None},
        ),
        patch(
            "app.services.dns_fabric.stack_panel._path_kuma_status",
            return_value="none",
        ),
        patch(
            "app.services.dns_fabric.core.build_access_path_for_record",
            return_value={
                "path_kind": "app",
                "docker_project": "myapp",
                "hops": [],
                "chain": "app.lab",
            },
        ),
    ):
        out = sp.build_stack_panel(session, service_id=42)

    assert out["ok"] is True
    assert out["service_id"] == 42
    assert out["fqdn"] == "app.lab"
    assert out["project"] == "myapp"
    assert out["kuma_path_coverage"] == "none"
    assert len(out["containers"]) == 1
    assert out["next_url"] == "/dns?stack=42"


def test_resolve_no_host():
    rec = SimpleNamespace(
        id=1,
        fqdn="x.lab",
        label=None,
        docker_project=None,
        backend_server_id=None,
        stack_deployment_id=None,
    )
    session = MagicMock()
    session.get.return_value = rec
    out = sp.resolve_stack_target(session, service_id=1)
    assert out["ok"] is False
    assert out["code"] == "no_host"

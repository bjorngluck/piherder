"""Unit tests for DB-backed Docker inventory helpers (no live SSH)."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services import docker_inventory as inv


def _server(**kwargs):
    base = dict(
        id=1,
        container_patch_enabled=True,
        docker_inventory_json=None,
        docker_inventory_at=None,
        docker_inventory_status="never",
        docker_inventory_error=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_parse_inventory_ok():
    payload = {"v": 1, "projects": [], "orphan_containers": [], "meta": {}}
    import json

    s = _server(docker_inventory_json=json.dumps(payload))
    assert inv.parse_inventory(s)["v"] == 1


def test_parse_inventory_bad():
    assert inv.parse_inventory(_server(docker_inventory_json="not-json")) is None
    assert inv.parse_inventory(_server(docker_inventory_json='{"no":"v"}')) is None
    assert inv.parse_inventory(_server()) is None


def test_is_stale_never_and_error():
    assert inv.is_stale(_server(docker_inventory_status="never")) is True
    assert inv.is_stale(_server(docker_inventory_status="error")) is True
    assert inv.is_stale(_server(docker_inventory_status="stale")) is True


def test_is_stale_refreshing_skips():
    assert inv.is_stale(_server(docker_inventory_status="refreshing")) is False


def test_is_stale_age():
    fresh = _server(
        docker_inventory_status="ok",
        docker_inventory_at=datetime.utcnow() - timedelta(seconds=30),
    )
    old = _server(
        docker_inventory_status="ok",
        docker_inventory_at=datetime.utcnow() - timedelta(seconds=500),
    )
    assert inv.is_stale(fresh, max_age_sec=120) is False
    assert inv.is_stale(old, max_age_sec=120) is True


def test_inventory_meta():
    import json

    at = datetime.utcnow()
    s = _server(
        docker_inventory_status="ok",
        docker_inventory_at=at,
        docker_inventory_json=json.dumps(
            {
                "v": 1,
                "projects": [{}],
                "orphan_containers": [],
                "meta": {"project_count": 2, "container_count": 5, "duration_ms": 100},
            }
        ),
    )
    m = inv.inventory_meta(s)
    assert m["has_snapshot"] is True
    assert m["project_count"] == 2
    assert m["container_count"] == 5
    assert m["status"] == "ok"


def test_slim_project_and_container():
    c = {
        "name": "web",
        "running": True,
        "image": "nginx:latest",
        "mounts_detail": [{"huge": True}],
        "extra_noise": 1,
    }
    slim = inv._slim_container(c)
    assert slim["name"] == "web"
    assert "mounts_detail" not in slim
    assert "extra_noise" not in slim

    p = inv._slim_project(
        {
            "name": "app",
            "path": "/home/x/docker/app",
            "containers": [c],
            "has_build": True,
            "build_services": ["web"],
        }
    )
    assert p["name"] == "app"
    assert len(p["containers"]) == 1
    assert p["has_build"] is True


def test_is_refresh_stuck_when_not_in_flight_set():
    s = _server(docker_inventory_status="refreshing")
    # Process lock empty → stuck
    assert inv.is_refresh_stuck(s) is True

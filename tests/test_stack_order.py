"""Operator container order for stack + map."""
from __future__ import annotations

from unittest.mock import patch

from app.services import stack_order as so


def test_apply_order():
    containers = [
        {"name": "db", "compose_service": "db", "role": "data"},
        {"name": "web", "compose_service": "web", "role": "app"},
        {"name": "caddy", "compose_service": "caddy", "role": "edge"},
    ]
    out = so.apply_order(containers, ["caddy", "web", "db"])
    assert [c["compose_service"] for c in out] == ["caddy", "web", "db"]
    assert out[0]["order_index"] == 0
    assert out[2]["custom_ordered"] is True


def test_apply_order_unknowns_last():
    containers = [
        {"name": "redis", "compose_service": "redis"},
        {"name": "web", "compose_service": "web"},
    ]
    out = so.apply_order(containers, ["web"])
    assert out[0]["compose_service"] == "web"
    assert out[1]["compose_service"] == "redis"


def test_set_order_roundtrip():
    stored = {}

    def _load():
        return {"stack_container_order_json": stored.get("json", "{}")}

    def _save(payload):
        stored["json"] = payload.get("stack_container_order_json")

    with patch.object(so, "load_settings", side_effect=_load), patch.object(
        so, "save_settings", side_effect=_save
    ):
        so.set_order(5, "piherder", ["web", "web", "celery-worker", "db"])
        got = so.get_order(5, "piherder")
    assert got == ["web", "celery-worker", "db"]


def test_order_key_normalizes_project_case():
    assert so.order_key(1, "PiHerder") == so.order_key(1, "piherder")

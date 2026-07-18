"""Compose dependency graph (P1b inventory enrich)."""
from __future__ import annotations

from app.services import compose_graph as cg


SAMPLE = {
    "services": {
        "web": {
            "image": "app:1",
            "depends_on": ["db", "redis"],
        },
        "worker": {
            "image": "app:1",
            "depends_on": {
                "db": {"condition": "service_healthy"},
                "redis": {"condition": "service_started"},
            },
        },
        "db": {"image": "postgres:16"},
        "redis": {"image": "redis:7"},
        "caddy": {"image": "caddy:2", "depends_on": ["web"]},
    },
    "networks": {"default": {}, "internal": {}},
}


def test_extract_depends_on_list_and_dict():
    g = cg.extract_compose_graph(SAMPLE, raw_text="services:\n  web:\n")
    assert "web" in g["depends_on"]
    assert set(g["depends_on"]["web"]) == {"db", "redis"}
    assert set(g["depends_on"]["worker"]) == {"db", "redis"}
    assert g["depends_on"]["caddy"] == ["web"]
    assert "db" not in g["depends_on"]  # no deps
    assert "db" in g["service_names"]
    assert "internal" in g["networks"]
    assert g.get("compose_sha")


def test_edges_from_graph():
    g = cg.extract_compose_graph(SAMPLE)
    edges = cg.edges_from_graph(g)
    pairs = {(e["from"], e["to"]) for e in edges}
    assert ("web", "db") in pairs
    assert ("web", "redis") in pairs
    assert ("worker", "db") in pairs
    assert ("caddy", "web") in pairs
    assert all(e["source"] == "compose" for e in edges)
    assert all(e["kind"] == "depends_on" for e in edges)


def test_heuristic_when_no_depends():
    edges = cg.heuristic_edges_from_services(
        ["web", "api", "db", "redis", "celery-worker"],
    )
    pairs = {(e["from"], e["to"]) for e in edges}
    assert ("web", "db") in pairs or ("api", "db") in pairs
    assert any(e["to"] == "redis" for e in edges)
    assert any(e["from"] == "celery-worker" for e in edges)
    # app → queue (compose often omits web→celery)
    assert ("web", "celery-worker") in pairs or ("api", "celery-worker") in pairs
    assert all(e["source"] == "heuristic" for e in edges)
    assert all(e["confidence"] < 50 for e in edges)


def test_heuristic_edge_to_app():
    edges = cg.heuristic_edges_from_services(
        ["caddy", "web", "db"],
        roles={"caddy": "edge", "web": "app", "db": "data"},
    )
    pairs = {(e["from"], e["to"]) for e in edges}
    assert ("caddy", "web") in pairs
    assert ("web", "db") in pairs


def test_merge_prefers_compose():
    a = [{"from": "web", "to": "db", "kind": "depends_on", "source": "compose", "confidence": 85}]
    b = [{"from": "web", "to": "db", "kind": "talks_to", "source": "heuristic", "confidence": 40}]
    m = cg.merge_edge_lists(a, b)
    assert len(m) == 1
    assert m[0]["source"] == "compose"
    assert m[0]["confidence"] == 85


def test_deps_from_string():
    assert cg._deps_from_value("db") == ["db"]
    assert cg._deps_from_value(None) == []

"""Compose sets — multi-file under one project folder."""
from __future__ import annotations

from app.services import compose_sets as cs


def test_classify_primary_override_set():
    assert cs.classify_compose_filename("docker-compose.yml") == "primary"
    assert cs.classify_compose_filename("compose.yaml") == "primary"
    assert cs.classify_compose_filename("docker-compose.override.yml") == "override"
    assert cs.classify_compose_filename("docker-compose.e2e.yml") == "set"
    assert cs.classify_compose_filename("compose.workers.yaml") == "set"
    assert cs.classify_compose_filename("Dockerfile") == "other"


def test_set_label_from_filename():
    assert cs.set_label_from_filename("docker-compose.e2e.yml") == "e2e"
    assert cs.set_label_from_filename("compose.monitoring.yaml") == "monitoring"
    assert cs.set_label_from_filename("docker-compose.yml") is None
    assert cs.set_label_from_filename("docker-compose.override.yml") is None


def test_build_compose_sets_main_and_e2e():
    files = [
        "docker-compose.yml",
        "docker-compose.e2e.yml",
        "docker-compose.override.yml",
        ".env",
    ]
    svc = {
        "docker-compose.yml": ["web", "db", "redis"],
        "docker-compose.e2e.yml": ["e2e-web", "e2e-db", "e2e-redis"],
    }
    sets = cs.build_compose_sets(files, services_by_file=svc)
    keys = [s["key"] for s in sets]
    assert keys == ["main", "e2e"]
    assert sets[0]["is_primary"] is True
    assert sets[0]["filename"] == "docker-compose.yml"
    assert sets[1]["services"] == ["e2e-web", "e2e-db", "e2e-redis"]
    # Override must not appear as a set
    assert all(s["filename"] != "docker-compose.override.yml" for s in sets)


def test_service_to_set_key_prefers_non_primary():
    sets = [
        {
            "key": "main",
            "is_primary": True,
            "services": ["web", "db"],
            "filename": "docker-compose.yml",
        },
        {
            "key": "e2e",
            "is_primary": False,
            "services": ["e2e-web", "e2e-db"],
            "filename": "docker-compose.e2e.yml",
        },
    ]
    assert cs.service_to_set_key("web", sets) == "main"
    assert cs.service_to_set_key("e2e-web", sets) == "e2e"
    assert cs.service_to_set_key("unknown", sets) == "main"


def test_annotate_containers_with_sets():
    sets = [
        {"key": "main", "is_primary": True, "services": ["web"]},
        {"key": "e2e", "is_primary": False, "services": ["e2e-web"]},
    ]
    containers = [
        {"name": "piherder-web", "compose_service": "web"},
        {"name": "piherder-e2e-web", "compose_service": "e2e-web"},
    ]
    cs.annotate_containers_with_sets(containers, sets)
    assert containers[0]["compose_set"] == "main"
    assert containers[1]["compose_set"] == "e2e"

"""Compose project nesting must not merge multi-project same-directory stacks."""
from __future__ import annotations

from app.services.docker_management import nest_containers_under_projects


def test_label_only_e2e_project_is_synthesized():
    """Directory scan only finds folder name; -p piherder-e2e must still get a project."""
    from app.services.docker_management import ensure_label_projects

    path = "/home/bjorn/docker/piherder"
    projects = [{"name": "piherder", "path": path, "services": ["web"]}]
    containers = [
        {
            "name": "piherder-web",
            "compose_project": "piherder",
            "compose_service": "web",
            "compose_workdir": path,
        },
        {
            "name": "piherder-e2e-web",
            "compose_project": "piherder-e2e",
            "compose_service": "web",
            "compose_workdir": path,
        },
    ]
    out = ensure_label_projects(projects, containers)
    names = {p["name"] for p in out}
    assert "piherder" in names
    assert "piherder-e2e" in names
    e2e = next(p for p in out if p["name"] == "piherder-e2e")
    assert e2e.get("label_only") is True


def test_same_workdir_different_compose_projects_stay_split():
    """piherder + piherder-e2e often share the repo path as working_dir."""
    path = "/home/bjorn/docker/piherder"
    # Only directory-scanned project — e2e is synthesized from labels
    projects = [
        {"name": "piherder", "path": path, "services": ["web", "db", "redis"]},
    ]
    containers = [
        {
            "name": "piherder-web",
            "compose_project": "piherder",
            "compose_service": "web",
            "compose_workdir": path,
            "running": True,
            "image": "piherder:local",
        },
        {
            "name": "piherder-db",
            "compose_project": "piherder",
            "compose_service": "db",
            "compose_workdir": path,
            "running": True,
            "image": "postgres:16",
        },
        {
            "name": "piherder-e2e-web",
            "compose_project": "piherder-e2e",
            "compose_service": "web",
            "compose_workdir": path,
            "running": True,
            "image": "piherder:e2e",
        },
        {
            "name": "piherder-e2e-db",
            "compose_project": "piherder-e2e",
            "compose_service": "db",
            "compose_workdir": path,
            "running": True,
            "image": "postgres:16",
        },
        {
            "name": "piherder-e2e-redis",
            "compose_project": "piherder-e2e",
            "compose_service": "redis",
            "compose_workdir": path,
            "running": True,
            "image": "redis:7",
        },
    ]
    nested, orphans = nest_containers_under_projects(projects, containers)
    by_name = {p["name"]: p for p in nested}

    prod_names = {c["name"] for c in by_name["piherder"]["containers"] if not c.get("placeholder")}
    e2e_names = {c["name"] for c in by_name["piherder-e2e"]["containers"] if not c.get("placeholder")}

    assert prod_names == {"piherder-web", "piherder-db"}
    assert e2e_names == {"piherder-e2e-web", "piherder-e2e-db", "piherder-e2e-redis"}
    assert "piherder-e2e-web" not in prod_names
    assert "piherder-web" not in e2e_names
    assert orphans == []


def test_unlabeled_container_still_nests_by_workdir():
    path = "/opt/stacks/app"
    projects = [{"name": "app", "path": path, "services": ["api"]}]
    containers = [
        {
            "name": "orphanish",
            "compose_project": "",
            "compose_service": "",
            "compose_workdir": path,
            "running": True,
            "image": "busybox",
        }
    ]
    nested, orphans = nest_containers_under_projects(projects, containers)
    assert orphans == []
    names = [c["name"] for c in nested[0]["containers"] if not c.get("placeholder")]
    assert names == ["orphanish"]


def test_project_label_beats_wrong_workdir_project():
    """Container labeled for project B must not attach to project A even if path matches A."""
    projects = [
        {"name": "a", "path": "/shared", "services": ["web"]},
        {"name": "b", "path": "/shared", "services": ["web"]},
    ]
    containers = [
        {
            "name": "b-web",
            "compose_project": "b",
            "compose_service": "web",
            "compose_workdir": "/shared",
            "running": True,
            "image": "x",
        }
    ]
    nested, orphans = nest_containers_under_projects(projects, containers)
    by = {p["name"]: p for p in nested}
    a_real = [c for c in by["a"]["containers"] if not c.get("placeholder")]
    b_real = [c for c in by["b"]["containers"] if not c.get("placeholder")]
    assert a_real == []
    assert [c["name"] for c in b_real] == ["b-web"]
    assert orphans == []

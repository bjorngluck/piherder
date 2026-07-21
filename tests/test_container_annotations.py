"""Container topology annotations: categories, tags, visual stacks, exact project match."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.dns_fabric import stack_panel as sp
from app.services import container_annotations as ann


def test_find_project_exact_only_no_soft_match():
    inv = {
        "projects": [
            {"name": "piherder-e2e", "containers": [{"name": "web"}]},
            {"name": "piherder", "containers": [{"name": "web"}, {"name": "db"}]},
        ]
    }
    found = sp._find_project(inv, "piherder")
    assert found is not None
    assert found["name"] == "piherder"
    assert len(found["containers"]) == 2

    e2e = sp._find_project(inv, "piherder-e2e")
    assert e2e is not None
    assert e2e["name"] == "piherder-e2e"

    assert sp._find_project(inv, "piher") is None
    assert sp._find_project(inv, "other") is None


def test_find_project_case_insensitive_exact():
    inv = {"projects": [{"name": "PiHerder", "containers": []}]}
    assert sp._find_project(inv, "piherder")["name"] == "PiHerder"


def test_slugify():
    assert ann.slugify("E2E / testing") == "e2e-testing"
    assert ann.slugify("  Main App ") == "main-app"


def test_normalize_project_keeps_e2e_distinct():
    assert ann.normalize_project("piherder") == "piherder"
    assert ann.normalize_project("PiHerder") == "piherder"
    assert ann.normalize_project("piherder-e2e") == "piherder-e2e"
    assert ann.normalize_project("piherder") != ann.normalize_project("piherder-e2e")


def test_set_order_merge_preserves_other_view_group():
    """Reordering Main must not clear sort_index on e2e view-group containers."""
    from datetime import datetime

    from app.models import ContainerAnnotation

    now = datetime.utcnow()
    rows = [
        ContainerAnnotation(
            server_id=1,
            compose_project="piherder",
            container_key="web",
            sort_index=0,
            created_at=now,
            updated_at=now,
        ),
        ContainerAnnotation(
            server_id=1,
            compose_project="piherder",
            container_key="db",
            sort_index=1,
            created_at=now,
            updated_at=now,
        ),
        ContainerAnnotation(
            server_id=1,
            compose_project="piherder",
            container_key="e2e-web",
            sort_index=2,
            created_at=now,
            updated_at=now,
        ),
        ContainerAnnotation(
            server_id=1,
            compose_project="piherder",
            container_key="e2e-db",
            sort_index=3,
            created_at=now,
            updated_at=now,
        ),
    ]

    class _Sess:
        def exec(self, _q):
            m = MagicMock()
            m.all.return_value = list(rows)
            return m

        def add(self, obj):
            if obj not in rows:
                rows.append(obj)

        def commit(self):
            return None

    # Main view: reorder only web/db in place
    ann.set_order_via_annotations(
        _Sess(),
        server_id=1,
        project="piherder",
        names=["db", "web"],
        merge=True,
    )
    by = {r.container_key: r.sort_index for r in rows}
    assert by["e2e-web"] is not None
    assert by["e2e-db"] is not None
    assert by["db"] < by["web"]
    # e2e block still after Main, relative order intact
    assert by["e2e-web"] < by["e2e-db"]

    # Reorder e2e only — Main stays put
    ann.set_order_via_annotations(
        _Sess(),
        server_id=1,
        project="piherder",
        names=["e2e-db", "e2e-web"],
        merge=True,
    )
    by_e = {r.container_key: r.sort_index for r in rows}
    assert by_e["e2e-db"] < by_e["e2e-web"]
    assert by_e["db"] < by_e["web"]
    assert by_e["db"] < by_e["e2e-db"]

    # Full replace (All) clears names not submitted
    ann.set_order_via_annotations(
        _Sess(),
        server_id=1,
        project="piherder",
        names=["db", "web"],
        merge=False,
    )
    by2 = {r.container_key: r.sort_index for r in rows}
    assert by2["e2e-web"] is None
    assert by2["e2e-db"] is None
    assert by2["db"] == 0
    assert by2["web"] == 1


def test_annotations_scoped_by_project_not_service_name():
    """web@piherder and web@piherder-e2e must not share labels."""
    containers_prod = [
        {
            "name": "piherder-web",
            "compose_service": "web",
            "image": "piherder:local",
            "running": True,
            "compose_project": "piherder",
        }
    ]
    containers_e2e = [
        {
            "name": "piherder-e2e-web",
            "compose_service": "web",
            "image": "piherder:e2e",
            "running": True,
            "compose_project": "piherder-e2e",
        }
    ]
    session = MagicMock()
    prod_map = {
        "web": {
            "category_key": "app",
            "tags": ["web"],
            "visual_stack_id": None,
            "visual_stack_name": "Main",
            "sort_index": None,
            "notes": None,
            "compose_project": "piherder",
        }
    }
    e2e_map = {
        "web": {
            "category_key": "app",
            "tags": ["test"],
            "visual_stack_id": 9,
            "visual_stack_name": "helpers",
            "sort_index": None,
            "notes": None,
            "compose_project": "piherder-e2e",
        }
    }
    with (
        patch.object(ann, "list_categories", return_value=[{"key": "app", "label": "App"}]),
        patch.object(
            ann,
            "list_tags",
            return_value=[{"key": "web", "label": "Web"}, {"key": "test", "label": "Test"}],
        ),
    ):
        with patch.object(ann, "load_annotations_map", return_value=prod_map):
            prod = ann.apply_annotations_to_containers(
                session,
                containers_prod,
                server_id=1,
                project="piherder",
                guess_role=sp.guess_container_role,
            )
        with patch.object(ann, "load_annotations_map", return_value=e2e_map):
            e2e = ann.apply_annotations_to_containers(
                session,
                containers_e2e,
                server_id=1,
                project="piherder-e2e",
                guess_role=sp.guess_container_role,
            )
    assert prod[0]["tags"] == ["web"]
    assert prod[0]["visual_stack_name"] == "Main"
    assert e2e[0]["tags"] == ["test"]
    assert e2e[0]["visual_stack_name"] == "helpers"
    assert e2e[0]["visual_stack_id"] == 9


def test_apply_annotations_category_override():
    containers = [
        {
            "name": "web",
            "compose_service": "web",
            "image": "app:1",
            "running": True,
        },
        {
            "name": "db",
            "compose_service": "db",
            "image": "postgres:16",
            "running": True,
        },
    ]
    session = MagicMock()
    # load_annotations_map path — make apply use patched maps
    with (
        patch.object(
            ann,
            "load_annotations_map",
            return_value={
                "web": {
                    "category_key": "edge",
                    "tags": ["web", "proxy"],
                    "visual_stack_id": None,
                    "sort_index": 1,
                    "notes": None,
                },
                "db": {
                    "category_key": None,
                    "tags": ["db"],
                    "visual_stack_id": None,
                    "sort_index": 0,
                    "notes": None,
                },
            },
        ),
        patch.object(
            ann,
            "list_categories",
            return_value=[
                {"key": "edge", "label": "Edge", "sort_order": 0},
                {"key": "app", "label": "App", "sort_order": 1},
                {"key": "data", "label": "Data", "sort_order": 4},
            ],
        ),
        patch.object(
            ann,
            "list_tags",
            return_value=[
                {"key": "web", "label": "Web"},
                {"key": "proxy", "label": "Proxy"},
                {"key": "db", "label": "DB"},
            ],
        ),
    ):
        out = ann.apply_annotations_to_containers(
            session,
            containers,
            server_id=1,
            project="piherder",
            visual_stack_id="all",
            guess_role=sp.guess_container_role,
        )
    by = {c["compose_service"]: c for c in out}
    assert by["web"]["role"] == "edge"
    assert by["web"]["category_is_override"] is True
    assert by["web"]["tags"] == ["web", "proxy"]
    assert by["db"]["role"] == "data"  # heuristic from image
    assert by["db"]["category_is_override"] is False
    assert by["db"]["tags"] == ["db"]


def test_apply_annotations_visual_stack_filter():
    containers = [
        {"name": "web", "compose_service": "web", "image": "x", "running": True},
        {"name": "e2e", "compose_service": "e2e", "image": "x", "running": True},
    ]
    session = MagicMock()
    with (
        patch.object(
            ann,
            "load_annotations_map",
            return_value={
                "web": {
                    "category_key": "app",
                    "tags": [],
                    "visual_stack_id": None,
                    "sort_index": None,
                    "notes": None,
                },
                "e2e": {
                    "category_key": "app",
                    "tags": ["test"],
                    "visual_stack_id": 7,
                    "sort_index": None,
                    "notes": None,
                },
            },
        ),
        patch.object(ann, "list_categories", return_value=[{"key": "app", "label": "App"}]),
        patch.object(ann, "list_tags", return_value=[{"key": "test", "label": "Test"}]),
    ):
        main_only = ann.apply_annotations_to_containers(
            session,
            containers,
            server_id=1,
            project="piherder",
            visual_stack_id=None,
            guess_role=sp.guess_container_role,
        )
        main_str = ann.apply_annotations_to_containers(
            session,
            containers,
            server_id=1,
            project="piherder",
            visual_stack_id="main",
            guess_role=sp.guess_container_role,
        )
        stack7 = ann.apply_annotations_to_containers(
            session,
            containers,
            server_id=1,
            project="piherder",
            visual_stack_id=7,
            guess_role=sp.guess_container_role,
        )
        all_c = ann.apply_annotations_to_containers(
            session,
            containers,
            server_id=1,
            project="piherder",
            visual_stack_id="all",
            guess_role=sp.guess_container_role,
        )
    assert [c["compose_service"] for c in main_only] == ["web"]
    assert [c["compose_service"] for c in main_str] == ["web"]
    assert [c["compose_service"] for c in stack7] == ["e2e"]
    assert len(all_c) == 2


def test_build_stack_panel_does_not_merge_e2e_project():
    inv = {
        "v": 2,
        "projects": [
            {
                "name": "piherder-e2e",
                "containers": [
                    {
                        "name": "e2e-web",
                        "compose_service": "web",
                        "image": "piherder:e2e",
                        "running": True,
                        "ports_display": "",
                    }
                ],
            },
            {
                "name": "piherder",
                "containers": [
                    {
                        "name": "web",
                        "compose_service": "web",
                        "image": "piherder:local",
                        "running": True,
                        "ports_display": "8000:8000",
                    },
                    {
                        "name": "db",
                        "compose_service": "db",
                        "image": "postgres:16",
                        "running": True,
                        "ports_display": "",
                    },
                ],
            },
        ],
        "meta": {},
    }
    server = SimpleNamespace(
        id=5,
        name="rpi-core",
        docker_inventory_json=json.dumps(inv),
        docker_inventory_status="ok",
        docker_inventory_at=None,
        docker_inventory_error=None,
    )
    session = MagicMock()
    session.get.return_value = server
    session.exec.return_value.all.return_value = []

    with (
        patch(
            "app.services.dns_fabric.stack_panel.inv_svc.parse_inventory",
            return_value=inv,
        ),
        patch(
            "app.services.dns_fabric.stack_panel.inv_svc.inventory_meta",
            return_value={"status": "ok", "at": None, "error": None},
        ),
        patch(
            "app.services.dns_fabric.stack_panel.cov.kuma_integrations_enabled",
            return_value=[],
        ),
        patch(
            "app.services.container_annotations.apply_annotations_to_containers",
            side_effect=lambda session, containers, **kw: containers,
        ),
        patch(
            "app.services.container_annotations.list_visual_stacks",
            return_value=[{"id": None, "name": "Main", "slug": "main", "is_default": True}],
        ),
        patch(
            "app.services.container_annotations.list_categories",
            return_value=[
                {"key": "edge", "label": "Edge", "sort_order": 0},
                {"key": "app", "label": "App", "sort_order": 1},
                {"key": "data", "label": "Data", "sort_order": 4},
            ],
        ),
        patch(
            "app.services.container_annotations.list_tags",
            return_value=[],
        ),
        patch(
            "app.services.container_annotations.order_from_annotations",
            return_value=[],
        ),
        patch(
            "app.services.stack_order.get_order",
            return_value=[],
        ),
    ):
        out = sp.build_stack_panel(session, server_id=5, project="piherder")

    assert out["ok"] is True
    names = {c["compose_service"] for c in out["containers"]}
    assert names == {"web", "db"}
    assert "e2e-web" not in {c["name"] for c in out["containers"]}

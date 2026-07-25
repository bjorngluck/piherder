"""Unit tests for multi-file compose snapshot helpers (no SSH)."""
from __future__ import annotations

from app.services import docker_versions as dv


def test_merge_project_files_keeps_siblings():
    base = {
        "docker-compose.yml": "services: {}\n",
        "docker-compose.override.yml": "services:\n  web:\n    ports: []\n",
        ".env": "FOO=1\n",
    }
    merged = dv.merge_project_files(base, {"docker-compose.yml": "services:\n  a: {}\n"})
    assert "docker-compose.override.yml" in merged
    assert ".env" in merged
    assert "services:\n  a: {}" in merged["docker-compose.yml"]


def test_files_for_sftp_strips_meta():
    files = {
        "compose.yml": "x",
        dv.META_KEY: {"compose_files": ["compose.yml"]},
        "__other__": "nope",
    }
    out = dv.files_for_sftp(files)
    assert out == {"compose.yml": "x"}


def test_primary_compose_key():
    assert dv.primary_compose_key({"Dockerfile": "x"}) is None
    assert (
        dv.primary_compose_key(
            {".env": "a", "docker-compose.yml": "b", "docker-compose.override.yml": "c"}
        )
        == "docker-compose.yml"
    )
    assert dv.primary_compose_key({"compose.yaml": "z"}) == "compose.yaml"


def test_sort_project_filenames():
    names = [".env", "Dockerfile", "docker-compose.override.yml", "docker-compose.yml"]
    ordered = dv.sort_project_filenames(names)
    assert ordered[0] == "docker-compose.yml"
    assert ordered[1] == "docker-compose.override.yml"
    assert ".env" in ordered
    assert ordered[-1] == "Dockerfile" or "Dockerfile" in ordered


def test_file_role():
    assert dv.file_role("docker-compose.yml") == "compose"
    assert dv.file_role("compose.override.yml") == "override"
    assert dv.file_role(".env") == "env"
    assert dv.file_role("Dockerfile") == "dockerfile"
    assert dv.file_role("promtail-config.yaml") == "config"
    assert dv.file_role("secrets/DB_PASSWORD") == "secret"


def test_merge_template_desired_sidecars_and_draft():
    from types import SimpleNamespace

    from app.services.compose_editor import (
        apply_draft_snapshot,
        merge_template_desired_sidecars,
    )

    live = {"docker-compose.yml": "services: {}\n"}
    dep = SimpleNamespace(
        files_json='{"promtail-config.yaml": "server: {}\\n", "secrets/x": "nope"}'
    )
    merged = merge_template_desired_sidecars(live, dep, project="demo")
    assert merged["docker-compose.yml"] == "services: {}\n"
    assert merged["promtail-config.yaml"] == "server: {}\n"
    assert "secrets/x" not in merged

    drafts = [
        SimpleNamespace(
            id=9,
            is_draft=True,
            files='{"docker-compose.yml": "services:\\n  a: {}\\n"}',
        )
    ]
    with_draft, edit_id = apply_draft_snapshot(merged, drafts, "9")
    assert edit_id == 9
    assert "services:\n  a: {}" in with_draft["docker-compose.yml"]
    assert with_draft["promtail-config.yaml"] == "server: {}\n"


def test_resolve_project_fallback_without_inventory(monkeypatch):
    from types import SimpleNamespace

    from app.services import compose_editor as ce

    server = SimpleNamespace(id=1, docker_base_dir="/home/x/docker")
    live = {"docker-compose.yml": "x: 1\n"}
    monkeypatch.setattr(
        ce,
        "docker_base_expanded",
        lambda s: "/home/x/docker",
    )
    monkeypatch.setattr(
        ce.docker_svc,
        "get_project_live_files",
        lambda s, path: live if path.endswith("/demo") else {},
    )
    proj, files = ce.resolve_project_and_live_files(
        server, "demo", inventory_projects=[], template_dep=None
    )
    assert proj["name"] == "demo"
    assert proj["path"] == "/home/x/docker/demo"
    assert files == live

    try:
        ce.resolve_project_and_live_files(
            server, "missing", inventory_projects=[], template_dep=None
        )
        assert False, "expected ComposeEditorNotFound"
    except ce.ComposeEditorNotFound:
        pass

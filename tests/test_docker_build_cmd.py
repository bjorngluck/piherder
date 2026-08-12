"""Quoted compose build commands + named-project resolution (no live SSH)."""
from __future__ import annotations

import pytest

from app.services import docker_management as dm


def test_compose_build_shell_cmd_quotes_metacharacters():
    cmd = dm.compose_build_shell_cmd("/tmp/x; curl evil", None, False)
    assert "cd '/tmp/x; curl evil'" in cmd
    assert cmd.startswith("cd ")
    assert "&& docker compose build" in cmd
    # Unquoted interpolation would leave a naked semicolon command
    assert "cd /tmp/x; curl" not in cmd


def test_compose_build_shell_cmd_quotes_services_and_no_cache():
    cmd = dm.compose_build_shell_cmd("/opt/app", ["web;id", "db"], True)
    assert "--no-cache" in cmd
    assert "'web;id'" in cmd
    assert "db" in cmd


def test_resolve_compose_project_path_rejects_raw_paths(monkeypatch):
    monkeypatch.setattr(dm, "list_compose_projects", lambda server: [{"name": "app", "path": "/opt/app"}])
    with pytest.raises(ValueError, match="invalid"):
        dm.resolve_compose_project_path(object(), "/tmp/x;id")
    with pytest.raises(ValueError, match="invalid"):
        dm.resolve_compose_project_path(object(), "../etc")
    with pytest.raises(ValueError, match="invalid"):
        dm.resolve_compose_project_path(object(), "foo/bar")
    assert dm.resolve_compose_project_path(object(), "app") == "/opt/app"
    with pytest.raises(ValueError, match="not found"):
        dm.resolve_compose_project_path(object(), "missing")

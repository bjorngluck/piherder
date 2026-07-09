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

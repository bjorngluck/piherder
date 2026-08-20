"""HTTP gates for Host Files (flag, RBAC, demo, API scope)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import Server, User
from app.security.auth import create_access_token, get_password_hash
from app.security.encryption import encrypt_str
from app.services import host_files as hf
from app.services import api_tokens as tok


@pytest.fixture()
def files_client(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'files.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    with Session(engine) as s:
        admin = User(
            email="admin@files.test",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        viewer = User(
            email="viewer@files.test",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="viewer",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        s.add(admin)
        s.add(viewer)
        s.commit()
        s.refresh(admin)
        s.refresh(viewer)
        srv = Server(
            name="Lab Pi",
            hostname="lab.local",
            ssh_username="pi",
            ssh_password_encrypted=encrypt_str("x"),
            container_patch_enabled=True,
            docker_base_dir="~/docker",
        )
        s.add(srv)
        s.commit()
        s.refresh(srv)
        ids = {"admin": admin.id, "viewer": viewer.id, "server": srv.id}
    try:
        yield client, ids
    finally:
        app.dependency_overrides.clear()


def _cookie(uid: int) -> dict[str, str]:
    return {"access_token": create_access_token({"sub": str(uid)})}


def test_files_flag_off_404(files_client, monkeypatch):
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES", False, raising=False)
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", False, raising=False)
    client, ids = files_client
    r = client.get(f"/servers/{ids['server']}/files", cookies=_cookie(ids["admin"]))
    assert r.status_code == 404


def test_files_viewer_403(files_client, monkeypatch):
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES", True, raising=False)
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", False, raising=False)
    client, ids = files_client
    r = client.get(f"/servers/{ids['server']}/files", cookies=_cookie(ids["viewer"]))
    assert r.status_code == 403


def test_files_anonymous_401(files_client, monkeypatch):
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES", True, raising=False)
    client, ids = files_client
    r = client.get(f"/servers/{ids['server']}/files")
    assert r.status_code == 401


def test_files_operator_200_when_flag_on(files_client, monkeypatch):
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES", True, raising=False)
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", False, raising=False)
    client, ids = files_client
    r = client.get(f"/servers/{ids['server']}/files", cookies=_cookie(ids["admin"]))
    assert r.status_code == 200
    assert 'data-testid="host-files-page"' in r.text
    assert 'data-testid="files-hero"' in r.text
    assert "ops-hero--files" in r.text
    assert 'id="hf-win"' in r.text
    assert 'id="hf-tree"' in r.text
    assert 'id="hf-list"' in r.text
    assert 'id="hf-busy"' in r.text
    assert 'id="hf-prompt"' in r.text
    assert 'id="hf-editor"' in r.text
    assert 'id="hf-zip"' in r.text
    assert 'id="hf-unzip"' in r.text
    assert 'id="hf-edit"' in r.text
    assert 'id="hf-all"' in r.text
    assert 'id="hf-rm"' in r.text
    assert 'id="hf-perms"' in r.text
    assert 'id="hf-perms-box"' in r.text
    assert 'id="hf-q"' in r.text
    assert 'id="hf-move"' in r.text
    assert 'id="hf-folder"' in r.text
    assert 'id="hf-move-box"' in r.text
    assert 'id="hf-zip-box"' in r.text
    assert 'id="hf-q-contents"' in r.text
    assert 'id="hf-q-go"' in r.text
    assert 'id="hf-more"' in r.text
    assert 'id="hf-selbar"' in r.text
    assert 'id="hf-preview"' in r.text
    assert 'id="hf-preview-prev"' in r.text
    assert 'id="hf-preview-next"' in r.text
    assert 'id="hf-editor-err"' in r.text
    assert 'id="hf-tree-toggle"' in r.text
    assert 'id="hf-upload-box"' in r.text
    assert 'placeholder="Search"' in r.text
    assert "press Enter" not in r.text
    assert ">Close</a>" not in r.text
    assert "Connect as" not in r.text
    assert "name opens" not in r.text
    assert "hf-host-dest" not in r.text
    assert 'id="hf-mkdir"' not in r.text
    assert 'id="hf-folder-btn"' not in r.text
    assert 'id="hf-docker-btn"' not in r.text
    assert 'id="hf-refresh"' not in r.text
    assert ">New folder<" not in r.text
    assert ">Upload folder<" not in r.text
    assert ">Refresh<" not in r.text
    # Same fleet chrome as every other page — never hide it for Files.
    import re
    body_open = re.search(r"<body([^>]*)>", r.text)
    assert body_open, "Files page must render base.html <body>"
    body_cls = body_open.group(1)
    assert "is-files-app" in body_cls
    assert "is-console-popup" not in body_cls
    assert "is-console-app" not in body_cls
    assert 'class="nav-header"' in r.text
    assert 'id="nav-hamburger-btn"' in r.text
    assert 'class="desktop-nav"' in r.text
    assert ">Dashboard<" in r.text
    assert ">Servers<" in r.text
    assert 'id="mobile-menu-panel"' in r.text
    assert "body.is-files-app .nav-header" in r.text
    assert "display: none !important" not in r.text.split("body.is-files-app .nav-header")[1][:180]


def test_files_ls_json_flag_on(files_client, monkeypatch):
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES", True, raising=False)
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", False, raising=False)
    client, ids = files_client
    r = client.get(
        f"/servers/{ids['server']}/files/ls",
        cookies=_cookie(ids["admin"]),
        headers={"Accept": "application/json", "X-PiHerder-Files": "1"},
    )
    # No live SSH in this fixture — jail listing talks to the host and must fail closed, not HTML.
    assert r.status_code in (200, 400, 502)
    if r.status_code == 200:
        body = r.json()
        assert body.get("ok") is True
        assert "entries" in body


def test_files_demo_403(files_client, monkeypatch):
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES", True, raising=False)
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", True, raising=False)
    client, ids = files_client
    r = client.get(f"/servers/{ids['server']}/files", cookies=_cookie(ids["admin"]))
    assert r.status_code == 403
    assert "demo" in (r.json() or {}).get("detail", r.text).lower()


def test_files_webauthn_options_same_origin_only(files_client, monkeypatch):
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES", True, raising=False)
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", False, raising=False)
    client, ids = files_client
    r = client.post(
        f"/servers/{ids['server']}/files/webauthn/options",
        cookies=_cookie(ids["admin"]),
    )
    assert r.status_code == 403


def test_api_files_requires_scope(files_client, monkeypatch):
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES", True, raising=False)
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", False, raising=False)
    client, ids = files_client
    r = client.get(
        f"/api/v1/servers/{ids['server']}/files",
        headers={"Authorization": "Bearer ph_nope"},
    )
    assert r.status_code in (401, 403)


def test_api_catalog_includes_files_scope():
    meta = tok.api_meta_dict()
    assert "files" in meta["scope_groups"]["capability"]
    paths = [e["path"] for e in meta["endpoints"]]
    assert "/api/v1/servers/{id}/files" in paths


def test_normalize_files_scope():
    assert "files" in tok.normalize_scopes(["files"])
    assert "files" not in tok.normalize_scopes(["read", "jobs"])

"""v1.14 coverage for token file routes and feature patches."""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.database import get_engine
from app.models import Server
from app.routers import api_v1
from app.services.host_files import FilesError


def _auth():
    token = SimpleNamespace(id=1, name="token-example", created_by_user_id=1, scopes="read,files,edit")
    return api_v1.ApiAuth(token, "192.0.2.10")


def test_api_files_and_feature_flags(monkeypatch):
    import asyncio
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr("app.services.host_files.files_enabled", lambda: True)
    monkeypatch.setattr(
        "app.services.host_files.list_dir",
        lambda *_a, **_k: {"jail": "/home/user", "rel": "", "truncated": False, "entries": [{"name": "a.txt", "rel": "a.txt", "kind": "file", "size": 1, "secretish": False, "escaped": False}]},
    )
    monkeypatch.setattr("app.services.host_files.stat_file", lambda *_a, **_k: {"rel": "a.txt"})
    monkeypatch.setattr("app.services.host_files.iter_file", lambda *_a, **_k: iter([b"hi"]))
    monkeypatch.setattr(
        "app.services.host_files.put_file",
        lambda *_a, **_k: {"rel": "a.txt", "bytes": 2, "sha256": "ab", "overwrite": False},
    )
    monkeypatch.setattr("app.services.host_files.mkdir", lambda *_a, **_k: {"rel": "box"})
    monkeypatch.setattr("app.services.host_files.rename", lambda *_a, **_k: {"from": "a.txt", "to": "b.txt"})
    monkeypatch.setattr("app.services.host_files.remove", lambda *_a, **_k: {"rel": "a.txt"})
    monkeypatch.setattr("app.services.host_files.sanitize_basename", lambda name: name or "upload.bin")
    monkeypatch.setattr("app.routers.server_patch.sync_server_schedules", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cron")))

    with Session(get_engine()) as session:
        server = Server(
            name="Host A",
            hostname="host-a.example.com",
            ssh_username="user",
            ssh_private_key_encrypted="key-example",
        )
        session.add(server)
        session.commit()
        session.refresh(server)
        auth = _auth()
        listed = api_v1.api_files_list(server.id, "", session, auth)
        assert listed["entries"][0]["name"] == "a.txt"
        download = api_v1.api_files_download(server.id, "a.txt", session, auth)

        async def _drain():
            out = b""
            async for chunk in download.body_iterator:
                out += chunk if isinstance(chunk, bytes) else bytes(chunk)
            return out

        assert asyncio.run(_drain()) == b"hi"
        upload = SimpleNamespace(filename="a.txt", file=io.BytesIO(b"hi"), size=2)
        saved = asyncio.run(api_v1.api_files_upload(server.id, upload, "", session, auth))
        assert saved["ok"] is True
        made = api_v1.api_files_mkdir(server.id, api_v1.FilesMkdirBody(p="", name="box"), session, auth)
        assert made["rel"] == "box"
        renamed = api_v1.api_files_rename(
            server.id, api_v1.FilesRenameBody(p="", src="a.txt", dest="b.txt"), session, auth
        )
        assert renamed["to"] == "b.txt"
        deleted = api_v1.api_files_delete(server.id, "a.txt", session, auth)
        assert deleted["ok"] is True

        monkeypatch.setattr(
            "app.services.host_files.list_dir",
            lambda *_a, **_k: (_ for _ in ()).throw(FilesError("denied", "no")),
        )
        with pytest.raises(HTTPException):
            api_v1.api_files_list(server.id, "", session, auth)

        with pytest.raises(HTTPException):
            api_v1.patch_server_features(999999, api_v1.ServerFeaturesBody(backup=True), session, auth)
        with pytest.raises(HTTPException):
            api_v1.patch_server_features(server.id, api_v1.ServerFeaturesBody(), session, auth)
        changed = api_v1.patch_server_features(
            server.id, api_v1.ServerFeaturesBody(backup=True, os_patch=False, docker=True), session, auth
        )
        assert changed["ok"] is True

"""Files API and MCP read_file refuse secret-looking names."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.database import get_engine
from app.models import Server
from app.routers import api_v1
from app.services import host_files as hf
from app.services import mcp_hosted

SECRET_PATHS = (
    ".env",
    "app/.env",
    ".env.local",
    "tls.key",
    "dir/host.key",
    "id_ed25519",
    "id_example",
    "certs/id_rsa",
    "server.pem",
    "secrets",
    "app.secrets",
    "secrets.yml",
    "notes/secrets.txt",
    "bundle.p12",
    "store.pfx",
    "key.ppk",
)


def test_secretish_covers_each_pattern():
    for path in SECRET_PATHS:
        assert hf.is_secretish(path), path
    assert hf.is_secretish("ID_RSA")
    assert not hf.is_secretish("config.yml")
    assert not hf.is_secretish("notes/readme.txt")
    assert not hf.is_secretish("identity.txt")


def test_api_and_mcp_refuse_each_pattern(monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr("app.services.host_files.files_enabled", lambda: True)
    calls = {"stat": 0, "read": 0}

    def stat_file(*_args, **_kwargs):
        calls["stat"] += 1
        return {"rel": "readme.txt"}

    def iter_file(*_args, **_kwargs):
        calls["read"] += 1
        return iter([b"hello"])

    monkeypatch.setattr("app.services.host_files.stat_file", stat_file)
    monkeypatch.setattr("app.services.host_files.iter_file", iter_file)

    token = SimpleNamespace(
        id=1,
        name="token-example",
        created_by_user_id=1,
        scopes="read,files",
    )
    auth = api_v1.ApiAuth(token, "192.0.2.10")

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

        async def _read(path: str):
            return await mcp_hosted.call_tool(
                "read_file",
                {"server_id": int(server.id), "p": path},
                session,
                auth,
            )

        for path in SECRET_PATHS:
            with pytest.raises(HTTPException) as exc:
                api_v1.api_files_download(server.id, path, session, auth)
            assert exc.value.status_code == 403
            assert exc.value.detail == "secret_file"
            result = asyncio.run(_read(path))
            assert result["isError"] is True
            body = json.loads(result["content"][0]["text"])
            assert body["status"] == 403
            assert body["detail"] == "secret_file"

        assert calls == {"stat": 0, "read": 0}
        download = api_v1.api_files_download(server.id, "readme.txt", session, auth)

        async def _drain():
            out = b""
            async for chunk in download.body_iterator:
                out += chunk if isinstance(chunk, bytes) else bytes(chunk)
            return out

        assert asyncio.run(_drain()) == b"hello"
        assert calls == {"stat": 1, "read": 1}


def test_read_file_tool_text_marks_it_sensitive():
    tool = next(item for item in mcp_hosted.tool_catalog() if item["name"] == "read_file")
    assert tool["annotations"]["readOnlyHint"] is False
    text = tool["description"].lower()
    assert "sensitive" in text
    assert "read-only" not in text
    assert "read only" not in text

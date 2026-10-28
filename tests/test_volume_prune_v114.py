"""Unused-volume prune on the API and hosted MCP. Not the scheduled prune."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AuditLog, Job, Server
from app.routers.api_v1 import VolumePruneBody, prune_volumes
from app.services.mcp_hosted import call_tool, tool_catalog


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _auth():
    return SimpleNamespace(
        user_id=None,
        token_id=None,
        token_name=None,
        client_ip="192.0.2.10",
        scopes=["read", "edit"],
        require=lambda _scope: None,
        require_feature=lambda _name: None,
        require_server_feature=lambda _server, _name: None,
    )


def test_volume_prune_needs_confirm_and_writes_one_audit(monkeypatch):
    calls = []

    def _prune(_server, prune_type="both"):
        calls.append(prune_type)
        return {"success": True, "output": "Volumes: Total reclaimed space: 0B"}

    monkeypatch.setattr("app.services.docker_management.prune_unused", _prune)
    engine = _engine()
    auth = _auth()
    with Session(engine) as session:
        server = Server(
            name="host-a",
            hostname="host-a.example.com",
            ssh_username="pi",
            container_patch_enabled=True,
        )
        session.add(server)
        session.commit()
        session.refresh(server)
        try:
            prune_volumes(server.id, VolumePruneBody(confirm=False), session, auth)
            raise AssertionError("confirm false accepted")
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 400
            assert "confirm" in str(exc.detail)
        assert calls == []
        assert list(session.exec(select(AuditLog)).all()) == []
        assert list(session.exec(select(Job)).all()) == []

        first = prune_volumes(server.id, VolumePruneBody(confirm=True), session, auth)
        second = prune_volumes(server.id, VolumePruneBody(confirm=True), session, auth)
        rows = list(
            session.exec(
                select(AuditLog).where(AuditLog.action == "docker_volume_prune")
            ).all()
        )
        jobs = list(session.exec(select(Job)).all())
    assert calls == ["volumes", "volumes"]
    assert first["ok"] is True
    assert second["audit_id"] != first["audit_id"]
    assert len(rows) == 2
    assert jobs == []


def test_hosted_prune_volumes_requires_confirm(monkeypatch):
    tool = [row for row in tool_catalog() if row["name"] == "prune_volumes"][0]
    assert tool["scope"] == "edit"
    assert "confirm" in tool["inputSchema"]["required"]
    called = []

    def _boom(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("volume prune ran")

    monkeypatch.setattr("app.routers.api_v1.prune_volumes", _boom)
    auth = _auth()
    result = asyncio.run(
        call_tool("prune_volumes", {"server_id": 1, "confirm": False}, None, auth)
    )
    assert result["isError"] is True
    assert "confirm" in result["content"][0]["text"]
    assert called == []

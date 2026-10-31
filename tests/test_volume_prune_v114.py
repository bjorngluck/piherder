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
    assert tool["inputSchema"]["required"] == ["server_id"]
    assert "dry_run" in tool["inputSchema"]["properties"]
    notes = tool["annotations"]
    assert notes["destructiveHint"] is True
    assert notes["readOnlyHint"] is False
    assert notes["idempotentHint"] is False
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


def test_volume_prune_refuses_when_an_exclusive_job_is_active():
    from fastapi import HTTPException

    engine = _engine()
    auth = _auth()
    with Session(engine) as session:
        server = Server(name="host-a", hostname="host-a.example.com", ssh_username="pi")
        session.add(server)
        session.commit()
        session.refresh(server)
        session.add(Job(server_id=server.id, job_type="backup", status="running", details="{}"))
        session.commit()
        try:
            prune_volumes(server.id, VolumePruneBody(dry_run=True), session, auth)
        except HTTPException as exc:
            assert exc.status_code == 409
            assert "exclusive" in str(exc.detail)
        else:
            raise AssertionError("prune ran while a backup was active")


def test_docker_write_demo_hint():
    from app.services.demo import demo_message

    text = demo_message("docker_write")
    assert "demo" in text.lower()
    assert text != "This action is disabled in the demo."


def test_volume_size_and_reclaim_parsers():
    from app.services.docker_management import (
        parse_deleted_volumes,
        parse_reclaimed_bytes,
        parse_volume_df,
    )

    text = """
Local Volumes space usage:

VOLUME NAME   LINKS   SIZE
data-a        0       1.5kB
data-b        1       2MB

Build cache usage:
"""
    assert parse_volume_df(text)["data-a"] == 1500
    assert parse_volume_df(text)["data-b"] == 2_000_000
    output = "Deleted Volumes:\ndata-a\n\nTotal reclaimed space: 1.5kB\n"
    assert parse_deleted_volumes(output) == ["data-a"]
    assert parse_reclaimed_bytes(output) == 1500


def test_dry_run_lists_volumes_and_does_not_audit(monkeypatch):
    import json

    def _list(_server):
        return {
            "success": True,
            "volumes": [{"name": "data-a", "size_bytes": 1500}],
            "output": "",
        }

    def _prune(*_args, **_kwargs):
        raise AssertionError("prune ran")

    monkeypatch.setattr("app.services.docker_management.list_dangling_volumes", _list)
    monkeypatch.setattr("app.services.docker_management.prune_unused", _prune)
    engine = _engine()
    auth = _auth()
    with Session(engine) as session:
        server = Server(name="host-a", hostname="host-a.example.com", ssh_username="pi")
        session.add(server)
        session.commit()
        session.refresh(server)
        listed = prune_volumes(server.id, VolumePruneBody(dry_run=True), session, auth)
        rows = list(session.exec(select(AuditLog)).all())
    assert listed["ok"] is True
    assert listed["dry_run"] is True
    assert listed["volumes"] == [{"name": "data-a", "size_bytes": 1500}]
    assert rows == []

    def _real(_server, prune_type="both"):
        assert prune_type == "volumes"
        return {
            "success": True,
            "output": "Deleted Volumes:\ndata-a\n\nTotal reclaimed space: 1.5kB\n",
        }

    monkeypatch.setattr("app.services.docker_management.prune_unused", _real)
    with Session(engine) as session:
        server = session.exec(select(Server)).first()
        done = prune_volumes(server.id, VolumePruneBody(confirm=True), session, auth)
        row = session.get(AuditLog, done["audit_id"])
        details = json.loads(row.details)
    assert done["volumes"] == ["data-a"]
    assert done["bytes_reclaimed"] == 1500
    assert details["source"] == "rest"
    assert details["volumes"] == ["data-a"]
    assert details["bytes_reclaimed"] == 1500


def test_mcp_and_ui_prune_audits_name_the_source(monkeypatch):
    import json
    from contextlib import contextmanager

    from fastapi import HTTPException

    from app.routers.api_v1 import prune_build_cache, volume_prune_source
    from app.services.audit_write import make_audit_log
    from app.services import jobs as job_service

    output = "Deleted Volumes:\ndata-a\ndata-b\n\nTotal reclaimed space: 2MB\n"

    def _real(_server, prune_type="both"):
        assert prune_type == "volumes"
        return {"success": True, "output": output}

    monkeypatch.setattr("app.services.docker_management.prune_unused", _real)
    engine = _engine()
    auth = _auth()
    token = volume_prune_source.set("mcp")
    try:
        with Session(engine) as session:
            server = Server(name="host-a", hostname="host-a.example.com", ssh_username="pi")
            session.add(server)
            session.commit()
            session.refresh(server)
            done = prune_volumes(server.id, VolumePruneBody(confirm=True), session, auth)
            details = json.loads(session.get(AuditLog, done["audit_id"]).details)
    finally:
        volume_prune_source.reset(token)
    assert details["source"] == "mcp"
    assert details["volumes"] == ["data-a", "data-b"]
    assert details["bytes_reclaimed"] == 2_000_000

    @contextmanager
    def _fresh():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(job_service, "_get_fresh_session", _fresh)
    monkeypatch.setattr(job_service, "_send_summary_webhook", lambda *_a, **_k: None)
    with Session(engine) as session:
        server = session.exec(select(Server)).first()
        job = Job(server_id=server.id, job_type="docker_prune", status="pending", details="{}")
        audit = make_audit_log(
            server_id=server.id,
            action="docker_prune",
            status="running",
            details="Job started",
        )
        session.add(job)
        session.add(audit)
        session.commit()
        session.refresh(job)
        session.refresh(audit)
        job_id, audit_id, server_id = job.id, audit.id, server.id
    job_service._execute_docker_prune(job_id, server_id, audit_id, "volumes")
    with Session(engine) as session:
        stored = json.loads(session.get(AuditLog, audit_id).details)
    assert stored["source"] == "ui"
    assert stored["volumes"] == ["data-a", "data-b"]
    assert stored["bytes_reclaimed"] == 2_000_000

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)

    def _ssh(*_args, **_kwargs):
        raise AssertionError("host was contacted")

    monkeypatch.setattr("app.services.docker_management.prune_unused", _ssh)
    monkeypatch.setattr("app.services.docker_management.prune_build_cache", _ssh)
    with Session(engine) as session:
        server = session.exec(select(Server)).first()
        for call in (
            lambda: prune_volumes(server.id, VolumePruneBody(confirm=True), session, auth),
            lambda: prune_build_cache(server.id, session, auth),
        ):
            try:
                call()
            except HTTPException as exc:
                assert exc.status_code == 403
                assert "demo" in str(exc.detail).lower()
                assert "Docker" in str(exc.detail)
            else:
                raise AssertionError("demo allowed a docker write")

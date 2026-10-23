"""Dangling prune after an update, prune schedule, and build cache."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AuditLog, Job, Notification, Server
from app.routers.api_v1 import prune_build_cache
from app.services import docker_management as docker_svc
from app.services import jobs as jobs_svc
from app.services import schedule_watch as watch
from app.services.mcp_hosted import tool_catalog
from app.services.scheduler import schedule_docker_prune_job, sync_server_cron_jobs
from app.services.scheduler_visibility import classify_scheduler_job


def test_dangling_prune_uses_image_prune_f(monkeypatch):
    calls = []

    class _Client:
        def close(self):
            calls.append("close")

    monkeypatch.setattr(docker_svc, "get_ssh_client", lambda _server: _Client())

    def _run(_client, cmd, timeout=60):
        calls.append(cmd)
        return 0, "Total reclaimed space: 0B", ""

    monkeypatch.setattr(docker_svc, "run_command", _run)
    result = docker_svc.prune_dangling_images(SimpleNamespace())
    assert calls[0] == "docker image prune -f"
    assert result["success"] is True


def test_build_cache_prune_runs_builder_and_buildx(monkeypatch):
    calls = []

    class _Client:
        def close(self):
            return None

    monkeypatch.setattr(docker_svc, "get_ssh_client", lambda _server: _Client())

    def _run(_client, cmd, timeout=120):
        calls.append(cmd)
        return 0, "ok", ""

    monkeypatch.setattr(docker_svc, "run_command", _run)
    result = docker_svc.prune_build_cache(SimpleNamespace())
    assert calls == ["docker builder prune -f", "docker buildx prune -f"]
    assert result["success"] is True


def test_prune_after_update_logs_the_dangling_prune(monkeypatch):
    import inspect

    monkeypatch.setattr(
        "app.services.docker_management.prune_dangling_images",
        lambda _server: {"success": True, "output": "reclaimed"},
    )
    flushed = []
    monkeypatch.setattr(
        jobs_svc,
        "_flush_job_progress",
        lambda job_id, current, line, default_current="": flushed.append(line),
    )
    jobs_svc._prune_dangling_images_after_update(SimpleNamespace(), 7)
    assert "reclaimed" in flushed[0]
    for fn in (
        jobs_svc._run_container_job,
        jobs_svc._execute_container_patch_sync,
        jobs_svc._execute_container_service,
    ):
        assert "_prune_dangling_images_after_update" in inspect.getsource(fn)


def test_prune_schedule_is_visible_and_starts_both(monkeypatch):
    classified = classify_scheduler_job("docker_prune_4", "Docker prune host-a")
    assert classified["group_key"] == "docker_prune"
    assert classified["kind"] == "host"
    scheduler = MagicMock()
    server = SimpleNamespace(
        id=4,
        name="host-a",
        container_patch_enabled=True,
        docker_prune_schedule="0 3 * * 0",
        backup_enabled=False,
        os_patch_enabled=False,
        os_check_enabled=False,
        container_check_enabled=False,
        os_apply_enabled=False,
        container_apply_enabled=False,
    )
    with patch("app.services.app_settings.get_app_timezone", return_value="UTC"):
        sync_server_cron_jobs(scheduler, True, server)
    ids = {call.kwargs.get("id") for call in scheduler.add_job.call_args_list}
    assert "docker_prune_4" in ids

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.database.get_engine", lambda: engine)
    captured = {}

    def _create(_bg, _session, _server, job_type, source_filter=None, **_kwargs):
        captured["job_type"] = job_type
        captured["source_filter"] = source_filter
        return SimpleNamespace(id=9)

    monkeypatch.setattr(jobs_svc, "create_job_and_run", _create)
    with Session(engine) as session:
        row = Server(
            name="host-a",
            hostname="host-a.example.com",
            ssh_username="pi",
            container_patch_enabled=True,
            docker_prune_schedule="0 3 * * 0",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        schedule_docker_prune_job(row.id)
    assert captured == {"job_type": "docker_prune", "source_filter": "both"}


def test_missed_prune_schedule_opens_one_warning(monkeypatch):
    monkeypatch.setattr("app.services.app_settings.get_app_timezone", lambda: "UTC")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    moment = datetime(2026, 10, 19, 3, 20, tzinfo=timezone.utc)
    with Session(engine) as session:
        server = Server(
            name="host-a",
            hostname="host-a.example.com",
            ssh_username="pi",
            container_patch_enabled=True,
            docker_prune_schedule="0 3 * * *",
            created_at=datetime(2026, 1, 1),
        )
        session.add(server)
        session.commit()
        assert watch.sync_missed_schedule_alerts(session, now=moment) == 1
        rows = list(
            session.exec(select(Notification).where(Notification.status == "open")).all()
        )
        assert len(rows) == 1
        assert rows[0].type == "docker_prune_missed"


def test_build_cache_api_writes_one_audit_per_run(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(
        "app.services.docker_management.prune_build_cache",
        lambda _server: {"success": True, "output": "builder: 0B"},
    )
    auth = SimpleNamespace(
        user_id=None,
        token_id=None,
        token_name=None,
        client_ip="192.0.2.10",
        require=lambda _scope: None,
        require_feature=lambda _name: None,
        require_server_feature=lambda _server, _name: None,
    )
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
        first = prune_build_cache(server.id, session, auth)
        second = prune_build_cache(server.id, session, auth)
        rows = list(
            session.exec(
                select(AuditLog).where(AuditLog.action == "docker_build_cache_prune")
            ).all()
        )
    assert first["ok"] is True
    assert second["audit_id"] != first["audit_id"]
    assert len(rows) == 2
    assert "prune_build_cache" in {tool["name"] for tool in tool_catalog()}
    edit = [tool for tool in tool_catalog() if tool["name"] == "prune_build_cache"][0]
    assert edit["scope"] == "edit"


def test_ui_prune_routes_reject_when_docker_feature_is_off(monkeypatch):
    from app.routers.server_docker import prune_build_cache_route, prune_unused_route

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("docker or job work must not run")

    monkeypatch.setattr(docker_svc, "prune_build_cache", _forbidden)
    monkeypatch.setattr(docker_svc, "prune_unused", _forbidden)
    monkeypatch.setattr(jobs_svc, "create_job_and_run", _forbidden)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        server = Server(
            name="host-a",
            hostname="host-a.example.com",
            ssh_username="pi",
            container_patch_enabled=False,
        )
        session.add(server)
        session.commit()
        session.refresh(server)
        user = SimpleNamespace(id=1)
        calls = (
            prune_unused_route(server.id, MagicMock(), "both", "", session, user),
            prune_build_cache_route(server.id, session, user),
        )
        for call in calls:
            with pytest.raises(HTTPException) as caught:
                asyncio.run(call)
            assert caught.value.status_code == 400
            assert caught.value.detail == "Enable the Docker feature on this server first"
        assert list(session.exec(select(AuditLog)).all()) == []
        assert list(session.exec(select(Job)).all()) == []

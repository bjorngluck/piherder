"""v1.12 quiet lists: optional API/MCP read audit, and routine host_facts rows."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AuditLog, Job
from app.services import api_read_audit as reads
from app.services import jobs as job_service
from app.services import stale_data_cleanup as sdc


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _settings(on: bool, days: int = 7):
    from app.services.app_settings import DEFAULTS

    def _load():
        return {**DEFAULTS, "audit_api_reads": on, "audit_read_retention_days": days}

    return _load


def test_retention_clamp_and_public_detail():
    assert reads.retention_days({}) == 7
    assert reads.retention_days({"audit_read_retention_days": 0}) == 1
    assert reads.retention_days({"audit_read_retention_days": 400}) == 90
    assert reads.retention_days({"audit_read_retention_days": "nope"}) == 7
    assert reads.reads_enabled({}) is False
    assert reads.reads_enabled({"audit_api_reads": True}) is True
    assert reads.public_detail("GET /api/v1/health") == "GET /api/v1/health"
    assert reads.public_detail("GET /api/v1/health ph_example") == "read"
    assert reads.public_detail("   ") == "read"
    assert reads.is_routine_audit("api_read", "success") is True
    assert reads.is_routine_audit("host_facts", "success") is True
    assert reads.is_routine_audit("host_facts", "failed") is False
    assert reads.is_routine_job("host_facts", "success") is True
    assert reads.is_routine_job("host_facts", "failed") is False
    assert reads.is_routine_job("backup", "success") is False


def test_flag_off_writes_nothing_and_errors_do_not_raise(monkeypatch):
    monkeypatch.setattr("app.services.app_settings.load_settings", _settings(False))
    engine = _engine()
    with Session(engine) as session:
        reads.record_api_read(
            session,
            token_id=3,
            token_name="reader",
            user_id=1,
            client_ip="192.0.2.10",
            what="GET /api/v1/health",
        )
        assert list(session.exec(select(AuditLog)).all()) == []

    monkeypatch.setattr("app.services.app_settings.load_settings", _settings(True))

    def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.services.api_read_audit.make_audit_log", _boom)
    with Session(engine) as session:
        reads.record_api_read(
            session,
            token_id=3,
            token_name="reader",
            user_id=1,
            client_ip="192.0.2.10",
            what="GET /api/v1/health",
        )
        assert list(session.exec(select(AuditLog)).all()) == []


def test_failing_audit_write_keeps_the_caller_change(monkeypatch):
    monkeypatch.setattr("app.services.app_settings.load_settings", _settings(True))

    def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.services.api_read_audit.make_audit_log", _boom)
    engine = _engine()
    with Session(engine) as session:
        session.add(AuditLog(action="pending", status="success", details="keep"))
        reads.record_api_read(
            session,
            token_id=3,
            token_name="reader",
            user_id=1,
            client_ip="192.0.2.10",
            what="GET /api/v1/health",
        )
        session.commit()
    with Session(engine) as session:
        rows = list(session.exec(select(AuditLog)).all())
    assert [r.action for r in rows] == ["pending"]
    assert rows[0].details == "keep"


def test_flag_on_stores_id_and_name_not_the_secret(monkeypatch):
    monkeypatch.setattr("app.services.app_settings.load_settings", _settings(True))
    engine = _engine()
    secret = "ph_example"
    with Session(engine) as session:
        reads.record_api_read(
            session,
            token_id=9,
            token_name="reader",
            user_id=2,
            client_ip="192.0.2.10",
            what=f"GET /api/v1/health {secret}",
        )
        reads.record_flag_change(session, user_id=2, enabled=True)
        reads.record_flag_change(session, user_id=2, enabled=False)
        rows = list(session.exec(select(AuditLog).order_by(AuditLog.id)).all())
    assert [r.action for r in rows] == [
        "api_read",
        "audit_api_reads_changed",
        "audit_api_reads_changed",
    ]
    read = rows[0]
    assert read.api_token_id == 9
    assert read.api_token_name == "reader"
    assert read.client_ip == "192.0.2.10"
    assert read.details == "read"
    assert secret not in (read.details or "")
    assert rows[1].details == "on"
    assert rows[2].details == "off"


def test_jobs_list_hides_successful_host_facts_unless_type_is_explicit():
    engine = _engine()
    with Session(engine) as session:
        session.add(Job(job_type="host_facts", status="success"))
        session.add(Job(job_type="host_facts", status="failed"))
        session.add(Job(job_type="host_facts", status="running"))
        session.add(Job(job_type="backup", status="success"))
        session.commit()
        hidden = job_service.list_jobs(session, hide_routine=True, limit=50)
        seen = {(j.job_type, j.status) for j in hidden}
        assert ("host_facts", "success") not in seen
        assert ("host_facts", "failed") in seen
        assert ("host_facts", "running") in seen
        assert ("backup", "success") in seen
        assert job_service.count_jobs(session, hide_routine=True) == 3
        assert job_service.count_jobs(session, hide_routine=False) == 4
        explicit = job_service.list_jobs(
            session, job_type="host_facts", hide_routine=True, limit=50
        )
        assert len(explicit) == 3


def test_audit_list_hides_reads_and_successful_snapshots():
    from app.routers.audit import _apply_audit_filters

    engine = _engine()
    with Session(engine) as session:
        session.add(AuditLog(action="api_read", status="success", details="GET /api/v1/health"))
        session.add(AuditLog(action="host_facts", status="success"))
        session.add(AuditLog(action="host_facts", status="failed"))
        session.add(AuditLog(action="backup", status="success"))
        session.commit()

        def _rows(**extra):
            query = _apply_audit_filters(
                select(AuditLog),
                search="",
                server_id=None,
                user_id=None,
                api_token_id=None,
                status=None,
                action=None,
                date_from="",
                date_to="",
                **extra,
            )
            return list(session.exec(query).all())

        default = {(r.action, r.status) for r in _rows(hide_routine=True)}
        assert ("api_read", "success") not in default
        assert ("host_facts", "success") not in default
        assert ("host_facts", "failed") in default
        assert ("backup", "success") in default
        shown = {(r.action, r.status) for r in _rows(hide_routine=False)}
        assert ("api_read", "success") in shown
        assert ("host_facts", "success") in shown

        reads_only = _apply_audit_filters(
            select(AuditLog),
            search="",
            server_id=None,
            user_id=None,
            api_token_id=None,
            status=None,
            action="api_read",
            date_from="",
            date_to="",
            hide_routine=True,
        )
        assert [r.action for r in session.exec(reads_only).all()] == ["api_read"]
        facts = _apply_audit_filters(
            select(AuditLog),
            search="",
            server_id=None,
            user_id=None,
            api_token_id=None,
            status=None,
            action="host_facts",
            date_from="",
            date_to="",
            hide_routine=True,
        )
        assert len(list(session.exec(facts).all())) == 2


def test_read_rows_use_their_own_retention(monkeypatch):
    monkeypatch.setattr("app.services.app_settings.load_settings", _settings(True, days=7))
    engine = _engine()
    old = datetime.utcnow() - timedelta(days=40)
    recent = datetime.utcnow() - timedelta(days=2)
    with Session(engine) as session:
        session.add(AuditLog(action="api_read", status="success", started_at=old))
        session.add(AuditLog(action="api_read", status="success", started_at=recent))
        session.add(AuditLog(action="backup", status="success", started_at=old))
        session.add(AuditLog(action="host_facts", status="success", started_at=old))
        session.add(AuditLog(action="backup", status="success", started_at=recent))
        session.commit()
        conf = {
            "data_cleanup_enabled": True,
            "data_cleanup_jobs_enabled": False,
            "data_cleanup_jobs_days": 30,
            "data_cleanup_audit_enabled": True,
            "data_cleanup_audit_days": 30,
            "data_cleanup_nmap_enabled": False,
            "data_cleanup_nmap_days": 30,
        }
        prev = sdc.preview_cleanup(session, conf)
        assert prev["audit"] == 2
        assert prev["read_audit"] == 1
        assert prev["total"] == prev["jobs"] + prev["audit"] + prev["read_audit"] + prev["nmap_runs"]
        res = sdc.run_stale_data_cleanup(session, job_id=None, dry_run=False, cfg=conf)
        assert res["deleted_audit"] == 2
        assert res["deleted_read_audit"] == 1
        left = list(session.exec(select(AuditLog)).all())
        kept = sorted(r.action for r in left if r.action != "stale_data_cleanup")
        assert kept == ["api_read", "backup"]
        reads_left = [r for r in left if r.action == "api_read"]
        assert len(reads_left) == 1
        assert reads_left[0].started_at > datetime.utcnow() - timedelta(days=7)


def test_mcp_read_tools_are_audited_and_writes_are_not(monkeypatch):
    from app.services import mcp_hosted

    monkeypatch.setattr("app.services.app_settings.load_settings", _settings(True))
    engine = _engine()

    async def _ok(*_args, **_kwargs):
        return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(mcp_hosted, "call_tool", _ok)
    auth = type(
        "Auth",
        (),
        {
            "scopes": ["read", "edit"],
            "token_id": 4,
            "token_name": "agent",
            "user_id": 2,
            "client_ip": "192.0.2.20",
        },
    )()
    with Session(engine) as session:
        listed = asyncio.run(
            mcp_hosted.dispatch_rpc(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                session,
                auth,
            )
        )
        assert listed["result"]["tools"]
        called = asyncio.run(
            mcp_hosted.dispatch_rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "health",
                        "arguments": {"note": "ph_example"},
                    },
                },
                session,
                auth,
            )
        )
        assert called["result"]["content"][0]["text"] == "ok"
        asyncio.run(
            mcp_hosted.dispatch_rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "set_features", "arguments": {"server_id": 1}},
                },
                session,
                auth,
            )
        )
        asyncio.run(
            mcp_hosted.dispatch_rpc(
                {"jsonrpc": "2.0", "id": 4, "method": "ping"},
                session,
                auth,
            )
        )
        rows = list(session.exec(select(AuditLog).order_by(AuditLog.id)).all())
    assert [r.details for r in rows] == ["mcp tools/list", "mcp health"]
    assert all(r.action == "api_read" for r in rows)
    assert all(r.api_token_id == 4 and r.api_token_name == "agent" for r in rows)
    blob = " ".join(r.details or "" for r in rows)
    assert "ph_example" not in blob
    assert mcp_hosted._mcp_tool_is_read("health") is True
    assert mcp_hosted._mcp_tool_is_read("set_features") is False


def test_get_health_audits_path_without_the_secret_when_flag_is_on(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.database import get_session
    from app.main import app
    from app.models import Server, User
    from app.security.auth import get_password_hash
    from app.services import api_tokens as tok

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr("app.services.app_settings.load_settings", _settings(True))
    engine = create_engine(
        f"sqlite:///{tmp_path / 'reads.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with Session(engine) as session:
            user = User(
                email="reader@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            session.add(user)
            session.add(
                Server(name="host-a", hostname="host-a.local", ssh_username="pi")
            )
            session.commit()
            session.refresh(user)
            row, plain = tok.create_api_token(
                session, name="reader", created_by=user, scopes=["read"]
            )
            token_id = row.id
        health = client.get(
            "/api/v1/health?unused=1",
            headers={"Authorization": f"Bearer {plain}"},
        )
        assert health.status_code == 200
        with Session(engine) as session:
            rows = list(session.exec(select(AuditLog)).all())
        assert len(rows) == 1
        assert rows[0].action == "api_read"
        assert rows[0].api_token_id == token_id
        assert rows[0].api_token_name == "reader"
        assert rows[0].details == "GET /api/v1/health"
        assert "ph_" not in (rows[0].details or "").lower()
        assert plain not in (rows[0].details or "")

        from app.security.auth import create_user_access_token

        with Session(engine) as session:
            viewer = session.exec(
                select(User).where(User.email == "reader@test.local")
            ).one()
            client.cookies.set("access_token", create_user_access_token(viewer))
        jobs = client.get("/jobs")
        audit = client.get("/audit")
        shown = client.get("/jobs?show_routine=1")
        settings = client.get("/herder-backups?tab=general")
        assert jobs.status_code == 200
        assert audit.status_code == 200
        assert shown.status_code == 200
        assert settings.status_code == 200
        assert "Show routine host snapshots" in jobs.text
        assert 'name="show_routine" value="1" checked' not in jobs.text
        assert 'name="show_routine" value="1" checked' in shown.text
        assert "Show routine rows" in audit.text
        assert 'data-testid="settings-audit-reads"' in settings.text
        assert 'name="audit_api_reads"' in settings.text
    finally:
        app.dependency_overrides.clear()


def test_templates_expose_the_flag_and_the_routine_checkbox():
    settings = Path("app/templates/partials/settings_general.html").read_text()
    jobs = Path("app/templates/jobs.html").read_text()
    audit = Path("app/templates/audit.html").read_text()
    assert 'data-testid="settings-audit-reads"' in settings
    assert 'name="audit_api_reads"' in settings
    assert 'name="audit_read_retention_days"' in audit or 'name="audit_read_retention_days"' in settings
    assert 'name="show_routine"' in jobs
    assert "Show routine host snapshots" in jobs
    assert 'name="show_routine"' in audit
    assert "Show routine rows" in audit

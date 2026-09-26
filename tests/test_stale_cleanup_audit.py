"""Stale data cleanup audit keeps the API token and client IP."""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import AuditLog, User
from app.security.auth import get_password_hash
from app.services import api_tokens as tok
from app.services import stale_data_cleanup as sdc


_CFG = {
    "enabled": False,
    "cron": "30 4 * * *",
    "jobs_enabled": False,
    "jobs_days": 30,
    "audit_enabled": False,
    "audit_days": 30,
    "nmap_enabled": False,
    "nmap_days": 30,
}


def _engine(tmp_path):
    return create_engine(
        f"sqlite:///{tmp_path / 'cleanup-audit.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _patch_enqueue(monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(sdc, "cleanup_config", lambda cfg=None: dict(_CFG))
    monkeypatch.setattr(
        "app.celery_app.celery.send_task",
        lambda *a, **k: SimpleNamespace(id="cleanup-task"),
    )


def test_worker_audit_copies_token_and_ip(tmp_path, monkeypatch):
    _patch_enqueue(monkeypatch)
    engine = _engine(tmp_path)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(
            email="cleanup-audit@test.local",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        row, _plain = tok.create_api_token(
            session, name="mcp", created_by=user, scopes=["read", "jobs"]
        )
        job = sdc.enqueue_stale_data_cleanup(
            session,
            user_id=user.id,
            api_token_id=row.id,
            api_token_name="mcp",
            client_ip="203.0.113.44",
            dry_run=True,
        )
        details = json.loads(job.details)
        assert details["api_token_id"] == row.id
        assert details["api_token_name"] == "mcp"
        assert details["client_ip"] == "203.0.113.44"
        assert details["user_id"] == user.id

        sdc.run_stale_data_cleanup(session, job_id=job.id, dry_run=True, cfg=_CFG)
        rows = list(session.exec(select(AuditLog).where(AuditLog.action == "stale_data_cleanup")).all())
        assert len(rows) == 1
        assert rows[0].api_token_id == row.id
        assert rows[0].api_token_name == "mcp"
        assert rows[0].client_ip == "203.0.113.44"
        assert rows[0].user_id == user.id
        assert rows[0].status == "success"

        queued = list(
            session.exec(select(AuditLog).where(AuditLog.action == "stale_data_cleanup_queued")).all()
        )
        assert len(queued) == 1
        assert queued[0].api_token_id == row.id
        assert queued[0].client_ip == "203.0.113.44"


def test_bearer_cleanup_records_token(tmp_path, monkeypatch):
    _patch_enqueue(monkeypatch)
    engine = _engine(tmp_path)
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        with Session(engine) as session:
            user = User(
                email="cleanup-api@test.local",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            _row, plain = tok.create_api_token(
                session, name="mcp", created_by=user, scopes=["read", "jobs", "edit"]
            )
            limited, limited_plain = tok.create_api_token(
                session,
                name="backup-only",
                created_by=user,
                scopes=["read", "jobs", "feature:backup"],
            )
            token_id = _row.id
            assert limited.id

        denied = client.post(
            "/api/v1/maintenance/stale-data-cleanup",
            headers={"Authorization": f"Bearer {limited_plain}"},
            json={"dry_run": True},
        )
        assert denied.status_code == 403

        accepted = client.post(
            "/api/v1/maintenance/stale-data-cleanup",
            headers={"Authorization": f"Bearer {plain}"},
            json={"dry_run": True},
        )
        assert accepted.status_code == 202, accepted.text
        body = accepted.json()
        assert body["job_type"] == "stale_data_cleanup"
        assert body["dry_run"] is True

        with Session(engine) as session:
            queued = list(
                session.exec(
                    select(AuditLog).where(AuditLog.action == "stale_data_cleanup_queued")
                ).all()
            )
            assert len(queued) == 1
            assert queued[0].api_token_id == token_id
            assert queued[0].api_token_name == "mcp"
            assert queued[0].client_ip
            job_id = body["job_id"]
            sdc.run_stale_data_cleanup(session, job_id=job_id, dry_run=True, cfg=_CFG)
            done = list(
                session.exec(select(AuditLog).where(AuditLog.action == "stale_data_cleanup")).all()
            )
            assert len(done) == 1
            assert done[0].api_token_id == token_id
            assert done[0].api_token_name == "mcp"
            assert done[0].client_ip == queued[0].client_ip
    finally:
        app.dependency_overrides.clear()

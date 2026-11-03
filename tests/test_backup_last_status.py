"""The backup page reads the most recent backup audit row for its own host."""
from datetime import datetime, timedelta
import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import AuditLog, Server, User
from app.security.auth import get_current_user


@pytest.fixture
def backup_page(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'backups.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(id=1, email="viewer@example.com", hashed_password="unused", role="viewer")
        session.add(user)
        session.add(Server(id=1, name="host-a", hostname="host-a.example.com"))
        session.add(Server(id=2, name="host-b", hostname="host-b.example.com"))
        session.commit()
        session.refresh(user)
        session.expunge(user)

    def get_test_session():
        with Session(engine) as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = get_test_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield TestClient(app), engine
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        engine.dispose()


@pytest.mark.parametrize(
    "status,output,expected",
    [
        ("success", None, "last ok"),
        ("failed", None, "last failed"),
        ("success", json.dumps({"results": [{"error": "synthetic failure"}]}), "last failed"),
    ],
)
def test_backup_page_shows_latest_backup_for_this_host(backup_page, status, output, expected):
    client, engine = backup_page
    now = datetime(2026, 1, 1)
    with Session(engine) as session:
        session.add_all([
            AuditLog(server_id=1, action="backup", status="failed", started_at=now),
            AuditLog(server_id=1, action="backup", status=status, output_snippet=output,
                     started_at=now + timedelta(hours=1)),
            AuditLog(server_id=1, action="os_patch", status="running",
                     started_at=now + timedelta(hours=2)),
            AuditLog(server_id=2, action="backup", status="running",
                     started_at=now + timedelta(hours=3)),
        ])
        session.commit()
    response = client.get("/servers/1/backups")
    assert response.status_code == 200
    assert f">{expected}</span>" in response.text


def test_backup_page_without_history_is_ready(backup_page):
    client, _ = backup_page
    response = client.get("/servers/1/backups")
    assert response.status_code == 200
    assert ">ready</span>" in response.text


def test_backup_audit_failure_is_logged(backup_page, monkeypatch, caplog):
    client, _ = backup_page
    original = Session.exec

    def fail_audit_query(self, statement, *args, **kwargs):
        if str(statement).startswith("SELECT auditlog."):
            raise RuntimeError("synthetic audit failure")
        return original(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "exec", fail_audit_query)
    with caplog.at_level(logging.ERROR, logger="piherder.servers"):
        response = client.get("/servers/1/backups")
    assert response.status_code == 200
    assert any("last backup status" in record.message and record.exc_info for record in caplog.records)

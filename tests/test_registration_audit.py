"""Self-registration writes an audit row (first admin and later open signup)."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import AuditLog, User
from app.services.audit_format import action_label


PASSWORD = "RegisterTest1ok"


def test_action_label_for_registration():
    assert action_label("user_registered") == "User registered"


def test_first_admin_and_open_registration_are_audited(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'register-audit.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(
        "app.routers.auth.rate_limit_auth", lambda *a, **k: True
    )
    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        denied = client.post(
            "/auth/register",
            data={"email": "weak@example.com", "password": "short"},
            follow_redirects=False,
        )
        assert denied.status_code == 200

        first = client.post(
            "/auth/register",
            data={"email": "admin@example.com", "password": PASSWORD},
            headers={"X-Forwarded-For": "203.0.113.10"},
            follow_redirects=False,
        )
        assert first.status_code == 303
        assert first.headers["location"] == "/auth/login"

        closed = client.post(
            "/auth/register",
            data={"email": "second@example.com", "password": PASSWORD},
            follow_redirects=False,
        )
        assert closed.status_code == 200

        monkeypatch.setattr(
            "app.config.settings.ALLOW_OPEN_REGISTRATION", True, raising=False
        )
        second = client.post(
            "/auth/register",
            data={"email": "operator@example.com", "password": PASSWORD},
            follow_redirects=False,
        )
        assert second.status_code == 303
    finally:
        app.dependency_overrides.clear()

    with Session(engine) as session:
        users = {u.email: u for u in session.exec(select(User)).all()}
        audits = list(session.exec(select(AuditLog).order_by(AuditLog.id)).all())

    assert set(users) == {"admin@example.com", "operator@example.com"}
    assert users["admin@example.com"].role == "admin"
    assert users["operator@example.com"].role == "operator"
    assert [a.action for a in audits] == ["user_registered", "user_registered"]

    first_row, second_row = audits
    assert first_row.user_id == users["admin@example.com"].id
    assert first_row.status == "success"
    assert first_row.details == "First administrator registered: admin@example.com"
    assert second_row.user_id == users["operator@example.com"].id
    assert second_row.status == "success"
    assert second_row.details == "Registered operator@example.com as operator"
    assert "short" not in (first_row.details or "")
    assert PASSWORD not in (first_row.details or "")

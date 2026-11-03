"""The backups page shows the latest backup audit status."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import AuditLog, Server, User
from app.security.auth import create_user_access_token, get_password_hash


def test_backups_page_shows_last_backup(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'backups.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    try:
        with Session(engine) as session:
            user = User(
                email="user@example.com",
                hashed_password=get_password_hash("SmokeTest1ok"),
                role="admin",
                is_active=True,
                must_change_password=False,
                totp_enabled=False,
            )
            host = Server(
                name="host-a",
                hostname="host-a.example.com",
                ip_address="192.0.2.10",
            )
            session.add(user)
            session.add(host)
            session.commit()
            session.refresh(user)
            session.refresh(host)
            session.add(
                AuditLog(
                    server_id=host.id,
                    action="backup",
                    status="success",
                    details="backup finished",
                )
            )
            session.commit()
            token = create_user_access_token(user)
            server_id = host.id
        client = TestClient(app, raise_server_exceptions=True)
        client.cookies.set("access_token", token)
        response = client.get(f"/servers/{server_id}/backups")
        assert response.status_code == 200
        assert "last ok" in response.text
    finally:
        app.dependency_overrides.pop(get_session, None)

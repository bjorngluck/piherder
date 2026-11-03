"""Redis lock errors report lock_unavailable. A held lock stays busy."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import BackupDestination, Server, User
from app.security.auth import get_password_hash
from app.services import api_tokens as tok_svc
from app.services import server_job_lock as lock
from app.services.backup_direct import push_server

_MARKER = "marker-secret-broker redis://192.0.2.40:6379/0"


class _RedisDown:
    def set(self, *_args, **_kwargs):
        raise ConnectionError(_MARKER)


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lock-unavailable.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_restore_preview_409_hides_the_redis_exception(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(lock, "_get_redis", lambda: _RedisDown())
    engine = _engine(tmp_path)
    app.dependency_overrides[get_session] = _session(engine)
    client = TestClient(app, raise_server_exceptions=False)
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
                name="Host A",
                hostname="host-a.example.com",
                ssh_username="user",
                ip_address="192.0.2.10",
                backup_enabled=True,
            )
            session.add(user)
            session.add(host)
            session.commit()
            session.refresh(user)
            session.refresh(host)
            host_id = host.id
            _token, plain = tok_svc.create_api_token(
                session, name="read", created_by=user, scopes=["read"]
            )
        response = client.get(
            f"/api/v1/servers/{host_id}/backup/restore-preview",
            headers={"Authorization": f"Bearer {plain}"},
            params={"source": "/data/app"},
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail == {
            "code": "lock_unavailable",
            "message": "The host lock is unavailable",
        }
        assert _MARKER not in response.text
        assert "redis://" not in response.text
        assert "ConnectionError" not in response.text
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_direct_backup_error_row_is_lock_unavailable(monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(lock, "_get_redis", lambda: _RedisDown())
    server = Server(id=4, name="Host A", hostname="host-a.example.com", backup_enabled=True)
    destination = BackupDestination(
        provider="drive",
        enabled=True,
        credentials_encrypted="tok",
        name="Drive",
        config_json='{"remote_dir":"PiHerder"}',
    )
    rows = push_server(server, destination, ["host-a.example.com"], [])
    assert rows[0]["error"] == "lock_unavailable"
    blob = str(rows)
    assert _MARKER not in blob
    assert "redis://" not in blob
    assert "Another backup" not in blob


def test_held_lock_stays_busy(monkeypatch):
    monkeypatch.setattr(lock, "_get_redis", lambda: None)
    lock.clear_local_locks_for_tests()
    lock.reset_lock_acquire_failures_for_tests()
    try:
        token = lock.try_acquire_server_lock("backup", 8, holder="held")
        assert token
        assert lock.try_acquire_server_lock("backup", 8, holder="other") is None
        assert lock.lock_acquire_failure_counts()["busy"] == 1
        assert lock.lock_acquire_failure_counts()["redis_error"] == 0
    finally:
        lock.clear_local_locks_for_tests()
        lock.reset_lock_acquire_failures_for_tests()


def _session(engine):
    def _open():
        with Session(engine) as session:
            yield session

    return _open

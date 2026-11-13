"""/ready fails closed, and a down worker fails the worker check."""
from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.main import app
from app.models import Notification
from app.services import notifications as notif
from app.services import readiness


def test_ready_fails_when_database_redis_or_migrations_are_wrong(monkeypatch):
    monkeypatch.setattr(readiness, "check_database", lambda: True)
    monkeypatch.setattr(readiness, "check_redis", lambda: True)
    monkeypatch.setattr(readiness, "migration_matches_head", lambda: True)
    client = TestClient(app)
    ok = client.get("/ready")
    assert ok.status_code == 200
    assert ok.json()["status"] == "ready"

    monkeypatch.setattr(readiness, "check_database", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    denied = client.get("/ready")
    assert denied.status_code == 503
    assert denied.json()["checks"]["database"] == "error"
    assert denied.json()["checks"]["redis"] == "ok"

    monkeypatch.setattr(readiness, "check_database", lambda: True)
    monkeypatch.setattr(readiness, "check_redis", lambda: (_ for _ in ()).throw(RuntimeError("redis://127.0.0.1:1/test")))
    redis_down = client.get("/ready")
    assert redis_down.status_code == 503
    assert redis_down.json()["checks"]["redis"] == "error"
    assert "redis://" not in redis_down.text
    assert "127.0.0.1" not in redis_down.text

    monkeypatch.setattr(readiness, "check_redis", lambda: True)
    monkeypatch.setattr(readiness, "migration_matches_head", lambda: False)
    behind = client.get("/ready")
    assert behind.status_code == 503
    assert behind.json()["checks"]["migrations"] == "error"


def test_ready_pings_every_redis_url_and_hides_credentials(monkeypatch):
    import redis

    seen = []

    def from_url(url, **_kwargs):
        class Client:
            def ping(self):
                seen.append(url)
                if "s3cret" in url:
                    raise redis.exceptions.AuthenticationError(url)
                return True

            def close(self):
                return None

        return Client()

    monkeypatch.setattr(redis, "from_url", from_url)
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://192.0.2.8:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://:s3cret@192.0.2.9:6379/1")
    monkeypatch.setenv("REDIS_URL", "redis://192.0.2.8:6379/0")
    monkeypatch.delenv("PIHERDER_NMAP_REDIS_URL", raising=False)
    monkeypatch.setattr(readiness, "check_database", lambda: True)
    monkeypatch.setattr(readiness, "migration_matches_head", lambda: True)
    payload = readiness.readiness()
    assert payload["redis"] == "error"
    assert payload["database"] == "ok"
    assert "s3cret" not in str(payload)
    assert "redis://" not in str(payload)
    assert seen[0] == "redis://192.0.2.8:6379/0"
    assert "s3cret" in seen[1]


def test_worker_check_fails_when_no_worker_answers(monkeypatch):
    class _Inspector:
        def ping(self):
            return None

    celery = MagicMock()
    celery.control.inspect.return_value = _Inspector()
    monkeypatch.setattr("app.celery_app.celery", celery)
    assert readiness.worker_responds() is False
    celery.control.inspect.return_value.ping = lambda: {"celery@host-a": {"ok": "pong"}}
    assert readiness.worker_responds() is True
    celery.control.inspect.return_value = None
    assert readiness.worker_responds() is False


def test_unhealthy_container_alert_is_one_row(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/ready.db")
    SQLModel.metadata.create_all(engine)
    containers = [
        {"name": "app-1", "status": "Up 2 hours (unhealthy)", "state": "running"},
        {"name": "db-1", "status": "Up 2 hours (healthy)", "state": "running"},
    ]
    names = notif.unhealthy_container_names(containers)
    assert names == ["app-1"]
    with Session(engine) as session:
        notif.notify_unhealthy_containers(session, 4, "host-a", names)
        notif.notify_unhealthy_containers(session, 4, "host-a", names)
        session.commit()
        rows = list(session.exec(select(Notification)))
        open_rows = [row for row in rows if row.fingerprint == "container_unhealthy:server:4"]
        assert len(open_rows) == 1
        assert "app-1" in (open_rows[0].body or "")
        notif.notify_unhealthy_containers(session, 4, "host-a", [])
        session.commit()
        session.refresh(open_rows[0])
        assert open_rows[0].resolved_at is not None

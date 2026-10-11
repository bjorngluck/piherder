"""The suite must not touch a live database, broker, or settings directory."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.celery_app import celery
from app.database import engine
from app.models import Job, User
from app.security.auth import create_user_access_token, get_password_hash
from app.services import jobs as js
from tests.conftest import _TEST_BROKER_URL, _TEST_DATABASE_URL, assert_test_targets


@pytest.mark.parametrize(
    ("database_url", "broker_url", "settings_path", "variable"),
    [
        (
            "postgresql://piherder:piherder@127.0.0.1:5432/piherder",
            "redis://127.0.0.1:1/test",
            "/tmp/piherder-test/data",
            "DATABASE_URL",
        ),
        (
            "sqlite:////tmp/piherder-test/test.db",
            "redis://localhost:6379/0",
            "/tmp/piherder-test/data",
            "CELERY_BROKER_URL",
        ),
        (
            "sqlite:////tmp/piherder-test/test.db",
            "redis://127.0.0.1:1/test",
            "/data",
            "DATA_ROOT",
        ),
    ],
)
def test_guard_rejects_a_non_test_target(database_url, broker_url, settings_path, variable):
    with pytest.raises(RuntimeError, match="tests refuse to run against a non-test target") as raised:
        assert_test_targets(
            database_url=database_url,
            broker_url=broker_url,
            settings_path=settings_path,
        )
    message = str(raised.value)
    assert message.startswith(f"{variable}:")


def test_herder_backup_request_stays_in_the_test_database(monkeypatch):
    """POST /herder-backups/run uses get_session() directly, not the dependency override."""
    assert str(engine.url).startswith("sqlite")
    assert _TEST_DATABASE_URL.endswith("test.db")
    assert "test" in str(engine.url)
    assert str(celery.conf.broker_url) == _TEST_BROKER_URL
    assert js.engine is engine

    broker_calls: list[str] = []

    def _refuse_broker(*_args, **_kwargs):
        broker_calls.append(str(celery.conf.broker_url))
        raise AssertionError("CELERY_BROKER_URL: tests refuse to run against a non-test target")

    monkeypatch.setattr(js.housekeeping_job_task, "delay", _refuse_broker)
    monkeypatch.setattr(celery, "send_task", _refuse_broker)

    with Session(engine) as session:
        before = {
            row.id
            for row in session.exec(select(Job).where(Job.job_type == "herder_backup")).all()
        }
        user = User(
            email="isolation@test.local",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        token = create_user_access_token(user)

    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("access_token", token)
    response = client.post(
        "/herder-backups/run",
        data={"backup_mode": "config_only"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text[:300]

    with Session(engine) as session:
        rows = session.exec(select(Job).where(Job.job_type == "herder_backup")).all()
    new_rows = [row for row in rows if row.id not in before]
    assert len(new_rows) == 1
    details = json.loads(new_rows[0].details or "{}")
    assert details.get("config_only") is True
    assert details.get("client_ip") == "testclient"
    assert broker_calls == []
    assert str(engine.url).startswith("sqlite")
    assert "test" in str(engine.url)

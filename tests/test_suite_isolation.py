"""The suite must not touch a live database, broker, or settings directory."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.celery_app import celery
from app.database import _create_app_engine, engine
from app.models import Job, User
from app.security.auth import create_user_access_token, get_password_hash
from app.services import jobs as js
from app.tasks import housekeeping_job
from celery.app.task import Task
from tests.conftest import (
    _TEST_BROKER_URL,
    _TEST_DATABASE_URL,
    _TEST_SETTINGS_PATH,
    _is_test_target,
    assert_test_targets,
)

_SKIP_DIRS = {
    ".git",
    ".venv",
    ".venv-docs",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    "htmlcov",
    "dist",
    "build",
}


def _tracked_text_files():
    root = Path(__file__).resolve().parents[1]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS and not name.endswith(".egg-info")]
        for name in filenames:
            if name.endswith((".py", ".md", ".yml", ".yaml", ".toml", ".txt", ".html", ".sh")):
                yield root, Path(dirpath) / name


@pytest.mark.parametrize(
    ("database_url", "broker_url", "settings_path", "variable"),
    [
        (
            "postgresql://piherder:piherder@127.0.0.1:5432/piherder",
            _TEST_BROKER_URL,
            _TEST_SETTINGS_PATH,
            "DATABASE_URL",
        ),
        (
            _TEST_DATABASE_URL,
            "redis://localhost:6379/0",
            _TEST_SETTINGS_PATH,
            "CELERY_BROKER_URL",
        ),
        (
            _TEST_DATABASE_URL,
            _TEST_BROKER_URL,
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


def test_latest_is_not_a_test_target():
    assert _is_test_target("latest") is False
    with pytest.raises(RuntimeError, match="DATABASE_URL") as raised:
        assert_test_targets(
            database_url="latest",
            broker_url=_TEST_BROKER_URL,
            settings_path=_TEST_SETTINGS_PATH,
        )
    assert "tests refuse to run against a non-test target" in str(raised.value)


def test_testing_flag_only_permits_the_sqlite_engine():
    """PIHERDER_TESTING is the sqlite gate, and the scheduler flag is not in app/."""
    testing_hits = []
    scheduler_hits = []
    for root, path in _tracked_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(root).as_posix()
        if "PIHERDER_TESTING" in text:
            testing_hits.append(rel)
        if rel.startswith("app/") and "PIHERDER_DISABLE_SCHEDULER" in text:
            scheduler_hits.append(rel)
    assert testing_hits
    for rel in testing_hits:
        assert rel == "app/database.py" or rel.startswith("tests/"), rel
    assert scheduler_hits == []


def test_sqlite_url_refuses_to_start_without_the_testing_flag(monkeypatch):
    monkeypatch.delenv("PIHERDER_TESTING", raising=False)
    with pytest.raises(RuntimeError, match="refuse to start"):
        _create_app_engine("sqlite://")


def test_testing_flag_permits_a_sqlite_engine(monkeypatch):
    monkeypatch.setenv("PIHERDER_TESTING", "1")
    opened = _create_app_engine("sqlite://")
    try:
        assert str(opened.url).startswith("sqlite")
    finally:
        opened.dispose()


def test_unmocked_celery_enqueue_is_refused_and_recorded():
    recorded = Task._piherder_enqueue_calls
    before = len(recorded)
    with pytest.raises(RuntimeError, match="refuse to enqueue"):
        housekeeping_job.delay(1, 0, 1, "herder_backup")
    with pytest.raises(RuntimeError, match="refuse to enqueue"):
        housekeeping_job.apply_async(args=(1, 0, 1, "herder_backup"))
    assert len(recorded) == before + 2
    assert recorded[-1][0] == "app.tasks.housekeeping_job"
    assert celery.conf.task_always_eager is False


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

"""About lists each scheduled job's last fire and next fire."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.database import engine
from app.main import app
from app.models import User
from app.security.auth import create_user_access_token, get_password_hash
from app.services import scheduler_visibility as vis
from app.services.app_settings import format_datetime_in_app_tz


def _job(job_id: str, name: str, nxt):
    return SimpleNamespace(id=job_id, name=name, next_run_time=nxt)


def _event(code: int, job_id: str, when):
    return SimpleNamespace(code=code, job_id=job_id, scheduled_run_time=when)


def test_snapshot_records_runs_and_skips_misses():
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
    from apscheduler.schedulers.background import BackgroundScheduler

    vis.clear_last_fires()
    ran = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
    failed = datetime(2026, 10, 19, 3, 15, tzinfo=timezone.utc)
    missed = datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc)
    early = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)
    later = datetime(2026, 10, 20, 5, 0, tzinfo=timezone.utc)

    sched = BackgroundScheduler(timezone="UTC")
    vis.attach_fire_listener(sched)
    vis.attach_fire_listener(sched)
    assert len(sched._listeners) == 1

    vis.note_scheduler_event(_event(EVENT_JOB_EXECUTED, "backup_1", ran))
    vis.note_scheduler_event(_event(EVENT_JOB_ERROR, "os_check_1", failed))
    vis.note_scheduler_event(_event(EVENT_JOB_MISSED, "backup_1", missed))

    class _Sched:
        running = True

        def get_jobs(self):
            return [
                _job("host_facts_fleet", "Host facts fleet refresh", later),
                _job("backup_1", "Backup host-a", early),
                _job("os_check_1", "OS check host-a", None),
                _job("container_check_1", "", early),
            ]

    snap = vis.scheduler_snapshot(_Sched(), True)
    assert snap["running"] is True
    names = [row["name"] for row in snap["jobs"]]
    assert names == [
        "Backup host-a",
        "container_check_1",
        "Host facts fleet refresh",
        "OS check host-a",
    ]
    by_id = {row["id"]: row for row in snap["jobs"]}
    assert by_id["backup_1"]["last_display"] == format_datetime_in_app_tz(ran)
    assert by_id["backup_1"]["next_display"] == format_datetime_in_app_tz(early)
    assert by_id["os_check_1"]["last_display"] == format_datetime_in_app_tz(failed)
    assert by_id["os_check_1"]["next_display"] == vis.NOT_SCHEDULED
    assert by_id["container_check_1"]["last_display"] == vis.NONE_YET
    assert by_id["host_facts_fleet"]["last_display"] == vis.NONE_YET


def test_snapshot_when_the_scheduler_is_down():
    vis.clear_last_fires()

    class _Down:
        running = False

        def get_jobs(self):
            raise AssertionError("do not list jobs while the scheduler is down")

    down = vis.scheduler_snapshot(_Down(), True)
    assert down == {
        "available": True,
        "running": False,
        "unreadable": False,
        "jobs": [],
    }
    missing = vis.scheduler_snapshot(None, False)
    assert missing["available"] is False
    assert missing["jobs"] == []


def test_about_page_renders_the_job_table(monkeypatch):
    early = datetime(2026, 10, 20, 4, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(
        vis,
        "scheduler_snapshot",
        lambda *a, **k: {
            "available": True,
            "running": True,
            "unreadable": False,
            "jobs": [
                {
                    "id": "backup_1",
                    "name": "Backup host-a",
                    "last_display": "None yet",
                    "next_display": format_datetime_in_app_tz(early),
                }
            ],
        },
    )
    with Session(engine) as session:
        user = User(
            email="schedvis@test.local",
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

    client = TestClient(app)
    client.cookies.set("access_token", token)
    response = client.get("/about")
    assert response.status_code == 200
    body = response.text
    assert 'data-testid="scheduler-jobs"' in body
    assert "Backup host-a" in body
    assert "None yet" in body
    assert format_datetime_in_app_tz(early) in body
    assert "Last fire (since restart) is the last time this web process ran the job." in body

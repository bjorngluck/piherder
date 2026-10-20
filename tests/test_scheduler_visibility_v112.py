"""About opens scheduled jobs by type, with each host's last run and next fire."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.database import engine
from app.main import app
from app.models import Job, Server, User
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
    assert by_id["backup_1"]["last_display"] == vis.NONE_YET
    assert by_id["backup_1"]["status_label"] == "None"
    assert by_id["backup_1"]["next_display"] == format_datetime_in_app_tz(early)
    assert by_id["os_check_1"]["last_display"] == vis.NONE_YET
    assert by_id["os_check_1"]["next_display"] == vis.NOT_SCHEDULED
    assert by_id["container_check_1"]["last_display"] == vis.NONE_YET
    assert by_id["host_facts_fleet"]["last_display"] == vis.NONE_YET
    del ran, failed, missed


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
        "groups": [],
    }
    missing = vis.scheduler_snapshot(None, False)
    assert missing["available"] is False
    assert missing["jobs"] == []
    assert missing["groups"] == []


def test_snapshot_groups_jobs_by_type():
    vis.clear_last_fires()
    early = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)
    later = datetime(2026, 10, 20, 5, 0, tzinfo=timezone.utc)
    from apscheduler.events import EVENT_JOB_EXECUTED

    vis.note_scheduler_event(_event(EVENT_JOB_EXECUTED, "backup_1", early))

    class _Sched:
        running = True

        def get_jobs(self):
            return [
                _job("backup_2", "Backup host-b", early),
                _job("backup_1", "Backup host-a", later),
                _job("os_check_1", "OS check host-a", early),
                _job("backup_copy_3", "Backup copy Drive", early),
                _job("host_facts_fleet", "Host facts fleet refresh", later),
                _job("nmap_scan_9", "Nmap: nightly", early),
                _job("custom_x", "Custom watch", None),
            ]

    snap = vis.scheduler_snapshot(_Sched(), True)
    assert [group["key"] for group in snap["groups"]] == [
        "backup",
        "os_check",
        "backup_copy",
        "nmap",
        "instance",
    ]
    backup = snap["groups"][0]
    assert backup["heading"] == "Host"
    assert [row["subject"] for row in backup["jobs"]] == ["host-a", "host-b"]
    assert backup["jobs"][0]["status_label"] == "None"
    assert backup["jobs"][1]["status_label"] == "None"
    assert snap["groups"][1]["jobs"][0]["subject"] == "host-a"
    copy = snap["groups"][2]
    assert copy["heading"] == "Destination"
    assert copy["jobs"][0]["subject"] == "Drive"
    assert snap["groups"][3]["heading"] == "Schedule"
    assert snap["groups"][3]["jobs"][0]["subject"] == "nightly"
    instance = snap["groups"][4]
    assert instance["heading"] == "Job"
    assert [row["subject"] for row in instance["jobs"]] == [
        "Custom watch",
        "Host facts fleet refresh",
    ]
    assert instance["jobs"][0]["status_label"] == "None"
    assert instance["jobs"][0]["next_display"] == vis.NOT_SCHEDULED


def test_last_run_reads_the_job_table():
    finished = datetime(2026, 10, 18, 6, 30, tzinfo=timezone.utc)
    older = datetime(2026, 10, 17, 6, 30, tzinfo=timezone.utc)
    with Session(engine) as session:
        server = Server(name="host-a", hostname="host-a.example.com")
        session.add(server)
        session.commit()
        session.refresh(server)
        sid = server.id
        session.add(
            Job(
                server_id=sid,
                job_type="backup",
                status="success",
                started_at=finished.replace(tzinfo=None),
                finished_at=finished.replace(tzinfo=None),
            )
        )
        session.add(Job(server_id=sid, job_type="backup", status="pending"))
        session.add(
            Job(
                server_id=sid,
                job_type="os_update_check",
                status="failed",
                finished_at=older.replace(tzinfo=None),
            )
        )
        session.add(
            Job(
                server_id=None,
                job_type="backup_replicate",
                status="success",
                finished_at=finished.replace(tzinfo=None),
                details='{"destination_id": 7}',
            )
        )
        session.add(
            Job(
                server_id=sid,
                job_type="host_facts",
                status="success",
                finished_at=older.replace(tzinfo=None),
            )
        )
        session.add(
            Job(
                server_id=None,
                job_type="host_facts",
                status="success",
                finished_at=finished.replace(tzinfo=None),
            )
        )
        session.commit()

    class _Sched:
        running = True

        def get_jobs(self):
            return [
                _job(f"backup_{sid}", "Backup host-a", None),
                _job(f"os_check_{sid}", "OS check host-a", None),
                _job("backup_copy_7", "Backup copy Drive", None),
                _job("host_facts_fleet", "Host facts fleet refresh", None),
                _job("stack_health_check", "PiHerder stack health", None),
            ]

    snap = vis.scheduler_snapshot(_Sched(), True)
    by_id = {row["id"]: row for row in snap["jobs"]}
    assert by_id[f"backup_{sid}"]["last_display"] == format_datetime_in_app_tz(finished)
    assert by_id[f"backup_{sid}"]["status_label"] == "OK"
    assert by_id[f"os_check_{sid}"]["status_label"] == "Failed"
    assert by_id[f"os_check_{sid}"]["last_display"] == format_datetime_in_app_tz(older)
    assert by_id["backup_copy_7"]["status_label"] == "OK"
    assert by_id["host_facts_fleet"]["last_display"] == format_datetime_in_app_tz(finished)
    assert by_id["stack_health_check"]["status_label"] == "None"
    assert by_id["stack_health_check"]["last_display"] == "Never"


def test_about_page_renders_the_job_table(monkeypatch):
    early = datetime(2026, 10, 20, 4, 15, tzinfo=timezone.utc)
    row = {
        "id": "backup_1",
        "name": "Backup host-a",
        "subject": "host-a",
        "last_display": format_datetime_in_app_tz(early),
        "next_display": format_datetime_in_app_tz(early),
        "status": "success",
        "status_label": "OK",
        "status_class": "status-success",
        "group_key": "backup",
    }
    monkeypatch.setattr(
        vis,
        "scheduler_snapshot",
        lambda *a, **k: {
            "available": True,
            "running": True,
            "unreadable": False,
            "jobs": [row],
            "groups": [
                {
                    "key": "backup",
                    "label": "Backup",
                    "heading": "Host",
                    "count": 1,
                    "jobs": [row],
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
    assert 'data-testid="scheduler-jobs-open"' in body
    assert 'data-testid="scheduler-job-type"' in body
    assert "ph-modal-brand" in body
    assert "ph-detail-kicker" in body
    assert "About · scheduler" in body
    assert "host-a" in body
    assert ">OK</span>" in body
    assert "since restart" not in body
    assert "Last run is the latest matching job." in body
    assert format_datetime_in_app_tz(early) in body
    assert 'data-scheduler-group="backup"' in body
    assert ">Backup (1)</option>" in body
    assert body.index("scheduler-job-type") < body.index("scheduler-jobs-scroll")

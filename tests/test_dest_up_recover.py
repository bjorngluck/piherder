"""Undo-2: dest_up worker death inspects dest, then stop dest and start source."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import AuditLog, Job, Server, User
from app.security.auth import create_access_token, get_password_hash
from app.services import jobs as job_service
from app.services.api_tokens import JOB_FEATURE_KEY
from app.services.service_migrate import host_lock as hl
from app.services.service_migrate.dest_up_recover import (
    JOB_TYPE,
    DestUpRecoverError,
    dest_ps_shows_running,
    dest_up_recover_details,
    eligible_dest_up_recover,
    run_dest_up_recover,
)


def test_ps_running_and_stopped():
    assert dest_ps_shows_running('{"State":"running","Name":"graf"}\n') is True
    assert dest_ps_shows_running('{"State":"exited","Name":"graf"}\n') is False
    assert dest_ps_shows_running("NAME   STATUS\ngraf   Up 2 hours\n") is True
    assert dest_ps_shows_running("NAME   STATUS\ngraf   Exited (1)\n") is False


def test_eligible_only_worker_death_during_dest_up():
    payload = dest_up_recover_details(
        source_id=1, dest_id=2, project="grafana", dest_project="grafana"
    )
    assert payload["project"] == "grafana"
    green = Job(
        server_id=1,
        job_type="service_migrate",
        status="success",
        details=json.dumps(
            {
                "failed_step": "worker_restart",
                "migrate_step": "dest_up",
                "dest_up_recover": payload,
            }
        ),
    )
    assert eligible_dest_up_recover(green) is None
    cutover = Job(
        server_id=1,
        job_type="service_migrate",
        status="failed",
        details=json.dumps(
            {"failed_step": "cutover", "migrate_step": "cutover", "dest_up_recover": payload}
        ),
    )
    assert eligible_dest_up_recover(cutover) is None
    hole = Job(
        server_id=1,
        job_type="service_migrate",
        status="failed",
        details=json.dumps(
            {
                "failed_step": "worker_restart",
                "migrate_step": "dest_up",
                "dest_up_recover": payload,
            }
        ),
    )
    assert eligible_dest_up_recover(hole)["dest_id"] == 2
    assert JOB_TYPE not in JOB_FEATURE_KEY


def test_stop_failure_does_not_start_source_and_down_is_refused():
    src = Server(name="src", hostname="src.local", ssh_username="pi")
    dst = Server(name="dst", hostname="dst.local", ssh_username="pi")
    started = {"n": 0}

    def _stop(_server, _path):
        return {"success": False, "action": "stop", "error": "stop failed"}

    def _start(_server, _path):
        started["n"] += 1
        return {"success": True, "action": "start"}

    with pytest.raises(DestUpRecoverError):
        run_dest_up_recover(
            source=src,
            dest=dst,
            project="grafana",
            dest_project="grafana",
            stop_fn=_stop,
            start_fn=_start,
        )
    assert started["n"] == 0

    def _down(_server, _path):
        return {"success": True, "action": "down"}

    with pytest.raises(DestUpRecoverError, match="down"):
        run_dest_up_recover(
            source=src,
            dest=dst,
            project="grafana",
            dest_project="grafana",
            stop_fn=_down,
            start_fn=_start,
        )
    assert started["n"] == 0


def test_stop_then_start_leaves_dns_alone():
    src = Server(name="src", hostname="src.local", ssh_username="pi")
    dst = Server(name="dst", hostname="dst.local", ssh_username="pi")
    calls = []

    def _stop(server, path):
        calls.append(("stop", server.name, path))
        return {"success": True, "action": "stop", "output": "stopped"}

    def _start(server, path):
        calls.append(("start", server.name, path))
        return {"success": True, "action": "start", "output": "started"}

    out = run_dest_up_recover(
        source=src,
        dest=dst,
        project="grafana",
        dest_project="graf-b",
        stop_fn=_stop,
        start_fn=_start,
    )
    assert out["dns_changed"] is False
    assert out["volumes_removed"] is False
    assert calls[0][0] == "stop" and calls[0][1] == "dst"
    assert calls[1][0] == "start" and calls[1][1] == "src"
    assert "down" not in calls[0][2]


def test_worker_restart_during_dest_up_stores_inspect_payload():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        src = Server(name="a", hostname="a.local")
        dst = Server(name="b", hostname="b.local")
        s.add(src)
        s.add(dst)
        s.commit()
        s.refresh(src)
        s.refresh(dst)
        job = Job(
            server_id=src.id,
            job_type="service_migrate",
            status="running",
            details=json.dumps(
                {
                    "project": "grafana",
                    "dest_project": "grafana",
                    "dest_server_id": dst.id,
                    "migrate_step": "dest_up",
                }
            ),
        )
        audit = AuditLog(
            server_id=src.id, action="service_migrate", status="running", details=""
        )
        s.add(job)
        s.add(audit)
        s.commit()
        s.refresh(job)
        s.refresh(audit)
        jid, aid, did = job.id, audit.id, dst.id

    def _fresh():
        return Session(engine)

    with patch.object(job_service, "_get_fresh_session", _fresh):
        job_service.fail_migrate_worker_restart(jid, aid)

    with Session(engine) as s:
        row = s.get(Job, jid)
        assert row.status == "failed"
        det = json.loads(row.details or "{}")
        assert not det.get("recover_source")
        assert not det.get("undo_move")
        assert det.get("migrate_step") == "dest_up"
        assert det.get("dest_up_recover", {}).get("dest_id") == did
        assert eligible_dest_up_recover(row)["project"] == "grafana"


@pytest.fixture()
def recover_client(tmp_path, monkeypatch):
    monkeypatch.setattr(hl, "migrate_enabled", lambda: True)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr("app.security.auth.force_2fa_required", lambda: False)
    monkeypatch.setattr(
        "app.services.account_stepup.force_2fa_applies", lambda *a, **k: False,
    )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'recover.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    with Session(engine) as s:
        admin = User(
            email="admin@recover.test",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        viewer = User(
            email="viewer@recover.test",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="viewer",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        s.add(admin)
        s.add(viewer)
        src = Server(name="src", hostname="src.local", ssh_username="pi")
        dst = Server(name="dst", hostname="dst.local", ssh_username="pi")
        s.add(src)
        s.add(dst)
        s.commit()
        s.refresh(admin)
        s.refresh(viewer)
        s.refresh(src)
        s.refresh(dst)
        payload = dest_up_recover_details(
            source_id=src.id, dest_id=dst.id, project="grafana", dest_project="grafana"
        )
        hole = Job(
            server_id=src.id,
            job_type="service_migrate",
            status="failed",
            details=json.dumps(
                {
                    "project": "grafana",
                    "failed_step": "worker_restart",
                    "migrate_step": "dest_up",
                    "dest_up_recover": payload,
                }
            ),
        )
        green = Job(
            server_id=src.id,
            job_type="service_migrate",
            status="success",
            details=json.dumps({"project": "grafana", "dest_up_recover": payload}),
        )
        s.add(hole)
        s.add(green)
        s.commit()
        s.refresh(hole)
        s.refresh(green)
        ids = {
            "admin": admin.id,
            "viewer": viewer.id,
            "src": src.id,
            "dst": dst.id,
            "hole": hole.id,
            "green": green.id,
        }
    try:
        yield client, ids
    finally:
        app.dependency_overrides.clear()


def _cookie(client: TestClient, uid: int) -> None:
    client.cookies.set("access_token", create_access_token({"sub": str(uid)}))


def test_http_dest_up_recover_gates(recover_client, monkeypatch):
    client, ids = recover_client
    _cookie(client, ids["viewer"])
    denied = client.get(
        f"/servers/{ids['src']}/docker/migrate/dest-up/inspect",
        params={"parent_job_id": ids["hole"]},
    )
    assert denied.status_code == 403

    _cookie(client, ids["admin"])
    monkeypatch.setattr(hl, "migrate_enabled", lambda: False)
    off = client.post(
        f"/servers/{ids['src']}/docker/migrate/dest-up/recover",
        data={"parent_job_id": str(ids["hole"]), "confirm": "1"},
    )
    assert off.status_code == 404
    monkeypatch.setattr(hl, "migrate_enabled", lambda: True)

    green = client.post(
        f"/servers/{ids['src']}/docker/migrate/dest-up/recover",
        data={"parent_job_id": str(ids["green"]), "confirm": "1"},
        headers={"X-PiHerder-Async": "1"},
    )
    assert green.status_code == 400

    monkeypatch.setattr(
        "app.services.service_migrate.dest_up_recover.inspect_dest",
        lambda source, dest, payload: {
            "project": "grafana",
            "dest_running": True,
            "dns_changed": False,
            "volumes_removed": False,
            "output": "graf Up",
        },
    )
    seen = client.get(
        f"/servers/{ids['src']}/docker/migrate/dest-up/inspect",
        params={"parent_job_id": ids["hole"]},
    )
    assert seen.status_code == 200, seen.text[:400]
    body = seen.json()
    assert body["dest_running"] is True
    assert body["dns_changed"] is False

    class FakeJob:
        id = 44
        status = "pending"

    monkeypatch.setattr(
        "app.services.jobs.enqueue_service_migrate_dest_recover",
        lambda parent_job_id, **kwargs: FakeJob(),
    )
    queued = client.post(
        f"/servers/{ids['src']}/docker/migrate/dest-up/recover",
        data={"parent_job_id": str(ids["hole"]), "confirm": "1"},
        headers={"X-PiHerder-Async": "1"},
    )
    assert queued.status_code == 200, queued.text[:400]
    assert queued.json()["job_type"] == JOB_TYPE

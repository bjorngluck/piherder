"""Fleet health bugs pulled into v1.11 from issues #36–#44."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import BackupDestination, Job, Server
from app.services import backup_replicate as copies
from app.services import docker_inventory as inv
from app.services import docker_management as docker_svc
from app.services import host_facts as facts
from app.services import insights
from app.services import os_patching
import app.services.jobs as jobs


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_failed_facts_keep_the_previous_os_line():
    engine = _engine()
    with Session(engine) as session:
        server = Server(
            name="RPI4-1",
            hostname="rpi4-1.local",
            os_pretty="Debian GNU/Linux 12",
            os_id="debian",
            hardware="Raspberry Pi 4",
            arch="aarch64",
            host_facts_status="ok",
        )
        session.add(server)
        session.commit()
        session.refresh(server)
        facts.apply_snapshot(
            session,
            server,
            {"error": "SSH connect failed to rpi4-1.local: timed out"},
        )
        session.refresh(server)
        assert server.os_pretty == "Debian GNU/Linux 12"
        assert server.hardware == "Raspberry Pi 4"
        assert server.arch == "aarch64"
        assert server.host_facts_status == "error"
        assert "timed out" in (server.host_facts_error or "")


def test_stale_host_facts_job_clears_refreshing():
    engine = _engine()
    with Session(engine) as session:
        server = Server(
            name="RPIZ2-1",
            hostname="rpiz2-1.local",
            host_facts_status="refreshing",
        )
        session.add(server)
        session.commit()
        session.refresh(server)
        job = Job(
            server_id=server.id,
            job_type="host_facts",
            status="running",
            details="{}",
            created_at=datetime.utcnow() - timedelta(minutes=45),
        )
        fresh = Job(
            server_id=server.id,
            job_type="host_facts",
            status="pending",
            details="{}",
            created_at=datetime.utcnow() - timedelta(minutes=5),
        )
        session.add(job)
        session.add(fresh)
        session.commit()
        n = jobs.cleanup_stale_backup_jobs(session, max_age_minutes=120)
        session.refresh(job)
        session.refresh(fresh)
        session.refresh(server)
        assert n == 1
        assert job.status == "failed"
        assert fresh.status == "pending"
        assert server.host_facts_status == "stale"


def test_drive_followup_skips_a_host_that_targets_smb_only():
    host = Server(
        id=3,
        name="rpi5-3",
        hostname="rpi5-3.local",
        backup_direct=True,
        backup_direct_targets='["smb"]',
    )
    dest = BackupDestination(provider="drive", name="Drive")
    kept = copies._groups_for_destination(dest, [(host, ["rpi5-3.local"])])
    assert kept == []
    host.backup_direct_targets = '["drive","smb"]'
    kept = copies._groups_for_destination(dest, [(host, ["rpi5-3.local"])])
    assert kept == [(host, ["rpi5-3.local"])]


def test_nothing_to_send_is_not_a_copy_error(monkeypatch):
    server = Server(id=3, name="rpi5-3", hostname="rpi5-3.local")
    dest = BackupDestination(provider="drive", name="Drive")

    def _rows(*_args, **_kwargs):
        return [{"source": "rpi5-3", "rel": "rpi5-3", "skipped": True}]

    monkeypatch.setattr("app.services.backup_direct.push_server", _rows)
    copied, errors = __import__(
        "app.services.backup_direct", fromlist=["push_groups"]
    ).push_groups(dest, [(server, ["rpi5-3"])], [])
    assert copied == []
    assert errors == []


def test_copy_auth_failure_opens_one_alert(monkeypatch):
    monkeypatch.setattr("app.services.alert_policy.raw_policy", lambda: {})
    engine = _engine()
    with Session(engine) as session:
        dest = BackupDestination(provider="drive", name="Drive", enabled=True)
        session.add(dest)
        session.commit()
        session.refresh(dest)
        copies.note_copy_auth_failure(session, dest, "rclone: invalid_grant")
        copies.note_copy_auth_failure(session, dest, "invalid_grant again")
        from sqlmodel import select
        from app.models import Notification

        rows = list(session.exec(select(Notification)).all())
        assert len(rows) == 1
        assert rows[0].type == "backup_destination_auth_failed"
        assert rows[0].severity == "critical"
        assert "Connect again" in rows[0].title
        copies.resolve_copy_auth_failure(session, dest.id)
        session.refresh(rows[0])
        assert rows[0].status == "resolved"
        assert copies.copy_auth_failure("disk full") is False


def test_stale_backup_opens_and_resolves():
    engine = _engine()
    with Session(engine) as session:
        old = Server(
            name="Nomad",
            hostname="nomad.local",
            backup_enabled=True,
            last_backup_at=datetime.utcnow() - timedelta(days=10),
        )
        fresh = Server(
            name="rpi5-1",
            hostname="rpi5-1.local",
            backup_enabled=True,
            last_backup_at=datetime.utcnow(),
        )
        session.add(old)
        session.add(fresh)
        session.commit()
        assert insights.sync_stale_backup_alerts(session) == 1
        from sqlmodel import select
        from app.models import Notification

        rows = list(session.exec(select(Notification).where(Notification.status == "open")).all())
        assert len(rows) == 1
        assert rows[0].type == "backup_stale"
        old.last_backup_at = datetime.utcnow()
        session.add(old)
        session.commit()
        assert insights.sync_stale_backup_alerts(session) == 0
        session.refresh(rows[0])
        assert rows[0].status == "resolved"


def test_docker_off_does_not_refresh(monkeypatch):
    engine = _engine()
    called = []
    monkeypatch.setattr(
        inv,
        "build_inventory_l1",
        lambda server: called.append(server.id) or {"meta": {}, "projects": []},
    )
    monkeypatch.setattr(inv, "engine", engine)
    with Session(engine) as session:
        server = Server(
            name="3DPRINT",
            hostname="3dprint.local",
            container_patch_enabled=False,
            docker_inventory_status="error",
            docker_inventory_error="bash: line 1: docker: command not found",
        )
        session.add(server)
        session.commit()
        session.refresh(server)
        assert inv.park_when_feature_off(session, server) is True
        session.refresh(server)
        assert server.docker_inventory_status == "off"
        assert server.docker_inventory_error is None
        assert inv.is_stale(server) is False
        assert inv.refresh_server_inventory(server.id, force=True) is False
        assert called == []


def test_apt_error_line_survives_the_next_real_line():
    os_patching.clear_os_patch_progress("rpi4-1")
    os_patching._append_os_log("rpi4-1", "[update] $ sudo /usr/bin/apt update")
    os_patching._append_os_log("rpi4-1", "\rE: Failed to fetch http://deb.debian.org/ 404", replace_progress=True)
    os_patching._append_os_log("rpi4-1", "[update] exit=100")
    lines = os_patching.get_os_patch_log_tail("rpi4-1", n=20)
    assert any(line.startswith("E: Failed to fetch") for line in lines)
    assert os_patching.apt_failure_reason(lines).startswith("E: Failed to fetch")


def test_stopped_container_from_this_project_is_removed(monkeypatch):
    calls = []

    def _run(_client, cmd, timeout=30):
        calls.append(cmd)
        if cmd.startswith("docker inspect"):
            return 0, "hailo-frigate-standalone|exited|/home/bjorn/docker/hailo-frigate-standalone", ""
        if cmd.startswith("docker rm"):
            return 0, "863934a2a33f", ""
        return 1, "", "unexpected"

    monkeypatch.setattr(docker_svc, "run_command", _run)
    note = docker_svc._clear_stopped_name_conflict(
        object(),
        "/home/bjorn/docker/hailo-frigate-standalone",
        'Error: container name "/hailo-vlm" is already in use by container "863934a2a33f"',
    )
    assert note.startswith("removed stopped container")
    assert any(cmd.startswith("docker rm ") for cmd in calls)


def test_other_project_container_is_left_in_place(monkeypatch):
    def _run(_client, cmd, timeout=30):
        if cmd.startswith("docker inspect"):
            return 0, "other-stack|exited|/home/bjorn/docker/other", ""
        raise AssertionError(cmd)

    monkeypatch.setattr(docker_svc, "run_command", _run)
    note = docker_svc._clear_stopped_name_conflict(
        object(),
        "/home/bjorn/docker/hailo-frigate-standalone",
        'container name "/hailo-vlm" is already in use by container "abc123"',
    )
    assert "other-stack" in note
    assert "left in place" in note

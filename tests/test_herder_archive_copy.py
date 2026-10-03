"""Copy one herder self-backup archive. The local file stays if rclone fails."""
from __future__ import annotations

import json

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import BackupDestination, Job
from app.services import backup_replicate as copies


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'herder-copy.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _oauth(provider: str, *, copy: bool) -> BackupDestination:
    row = BackupDestination(provider=provider, name=provider, enabled=True)
    copies.store_oauth_client(
        row,
        client_id="app-id",
        client_secret="secret-value",
        folder="PiHerder",
        schedule=None,
        after_host_backup=False,
        copy_herder_backup=copy,
    )
    row.credentials_encrypted = copies.encrypt_str(
        copies.pack_oauth_token(
            client_id="app-id",
            client_secret="secret-value",
            refresh_token="refresh-value",
            access_token="access",
            email="owner@example.com",
            expires_in=3600,
        )
    )
    return row


def test_archive_name_stays_in_a_known_root(tmp_path, monkeypatch):
    from app.services import herder_backup as hb

    monkeypatch.setattr(hb, "archive_dir_candidates", lambda: [tmp_path])
    name = "piherder-20261003-120000-full.tar.gz"
    archive = tmp_path / name
    archive.write_bytes(b"keep")
    assert hb.resolve_archive_in_roots(name=name) == archive.resolve()
    assert hb.resolve_archive_in_roots(name="../" + name) is None
    assert hb.resolve_archive_in_roots(name=name + "/../../etc/passwd") is None
    assert hb.resolve_archive_in_roots(name="piherder.tar.gz") is None
    smb = BackupDestination(provider="smb")
    copies.store_smb(
        smb,
        host="nas.local",
        share="backups",
        path="PiHerder",
        username="backup",
        password="smb-secret",
        domain="",
        schedule=None,
        after_host_backup=False,
        copy_herder_backup=True,
    )
    assert copies.copies_herder_backup(smb) is True
    assert copies.herder_archive_remote(smb, name) == f"backups/PiHerder/herder/{name}"
    assert "smb-secret" not in (smb.config_json or "")


def test_failed_copy_keeps_the_local_archive(tmp_path, monkeypatch):
    from app.services import herder_backup as hb

    name = "piherder-20261003-120000-full.tar.gz"
    archive = tmp_path / name
    archive.write_bytes(b"keep-me")
    monkeypatch.setattr(hb, "archive_dir_candidates", lambda: [tmp_path])
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    dest = _oauth("onedrive", copy=True)
    seen = {}

    class Proc:
        returncode = 1
        stderr = "secret-value and refresh-value"
        stdout = ""

    def run(cmd, **_k):
        seen["cmd"] = list(cmd)
        return Proc()

    monkeypatch.setattr(copies.subprocess, "run", run)
    result = copies.execute_herder_archive(dest, name)
    assert result["ok"] is False
    assert archive.read_bytes() == b"keep-me"
    assert "secret-value" not in result["error"]
    assert "refresh-value" not in result["error"]
    assert seen["cmd"][1] == "copyto"
    assert "move" not in seen["cmd"]
    assert "--drive-use-trash" not in seen["cmd"]
    assert seen["cmd"][3].endswith("PiHerder/herder/" + name)


def test_drive_copy_uses_trash_and_demo_does_not_upload(tmp_path, monkeypatch):
    from app.services import herder_backup as hb

    name = "piherder-20261003-120000-full.tar.gz"
    archive = tmp_path / name
    archive.write_bytes(b"keep-me")
    monkeypatch.setattr(hb, "archive_dir_candidates", lambda: [tmp_path])
    seen = {"n": 0}

    def run(cmd, **_k):
        seen["n"] += 1
        seen["cmd"] = list(cmd)

        class Proc:
            returncode = 0
            stderr = ""
            stdout = ""

        return Proc()

    monkeypatch.setattr(copies.subprocess, "run", run)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    refused = copies.execute_herder_archive(_oauth("drive", copy=True), name)
    assert refused["ok"] is False
    assert refused["error"] == "Demo does not upload"
    assert seen["n"] == 0
    assert archive.read_bytes() == b"keep-me"

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    result = copies.execute_herder_archive(_oauth("drive", copy=True), name)
    assert result["ok"] is True
    assert "--drive-use-trash" in seen["cmd"]
    assert seen["cmd"][1] == "copyto"
    assert archive.read_bytes() == b"keep-me"


def test_follow_up_queues_only_opted_in_destinations(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    queued: list[int] = []

    def delay(job_id):
        queued.append(job_id)

        class Result:
            id = "task-herder"

        return Result()

    monkeypatch.setattr("app.tasks.replicate_backup.delay", delay)
    engine = _engine(tmp_path)
    name = "piherder-20261003-120000-full.tar.gz"
    with Session(engine) as session:
        drive = _oauth("drive", copy=True)
        other = _oauth("onedrive", copy=False)
        session.add(drive)
        session.add(other)
        session.commit()
        session.refresh(drive)
        host_copy = Job(
            job_type="backup_replicate",
            status="running",
            details=json.dumps({"destination_id": drive.id, "scope": "pi"}),
        )
        session.add(host_copy)
        session.commit()
        jobs = copies.enqueue_after_herder_backup(session, f"/herder_backups/{name}")
        assert len(jobs) == 1
        assert jobs[0].id != host_copy.id
        details = json.loads(jobs[0].details or "{}")
        assert details["herder_archive"] == name
        assert details["destination_id"] == drive.id
        assert "secret-value" not in (jobs[0].details or "")
        assert "refresh-value" not in (jobs[0].details or "")
        rows = list(session.exec(select(Job).where(Job.job_type == "backup_replicate")).all())
        assert len(rows) == 2
    assert queued == [jobs[0].id]


def test_host_copy_and_herder_copy_do_not_share_a_slot(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    queued: list[int] = []

    def delay(job_id):
        queued.append(job_id)

        class Result:
            id = f"task-{job_id}"

        return Result()

    monkeypatch.setattr("app.tasks.replicate_backup.delay", delay)
    engine = _engine(tmp_path)
    name = "piherder-20261003-120000-full.tar.gz"
    with Session(engine) as session:
        drive = _oauth("drive", copy=True)
        session.add(drive)
        session.commit()
        session.refresh(drive)
        herder = copies.enqueue_herder_archive(session, drive, name)
        host = copies.enqueue(session, drive, scope="pi")
        assert host.id != herder.id
        assert json.loads(host.details or "{}").get("herder_archive") in (None, "")
        again = copies.enqueue(session, drive, scope="pi")
        assert again.id == host.id
        same_archive = copies.enqueue_herder_archive(session, drive, name)
        assert same_archive.id == herder.id
    assert queued == [herder.id, host.id]


def test_demo_enqueue_does_not_start_rclone(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    called = {"n": 0}

    def delay(_job_id):
        called["n"] += 1
        raise AssertionError("demo must not enqueue rclone")

    monkeypatch.setattr("app.tasks.replicate_backup.delay", delay)
    engine = _engine(tmp_path)
    name = "piherder-20261003-120000-config-only.tar.gz"
    with Session(engine) as session:
        session.add(_oauth("drive", copy=True))
        session.commit()
        jobs = copies.enqueue_after_herder_backup(session, name)
        assert len(jobs) == 1
        assert jobs[0].status == "failed"
        assert json.loads(jobs[0].details)["error"] == "Demo does not upload"
        assert called["n"] == 0

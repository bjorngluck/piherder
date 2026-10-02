"""SMB destination config and worker wiring. No live rclone or NAS."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

import pytest

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import BackupDestination, Job, Server
from app.security.encryption import decrypt_str
from app.services import backup_replicate as copies
from app.services.api_tokens import JOB_FEATURE_KEY


def _dest(**kwargs) -> BackupDestination:
    row = BackupDestination(provider="smb", name="LAN NAS / SMB", selection_json='{"checked":[],"skipped":[]}')
    copies.store_smb(
        row,
        host=kwargs.get("host", "nas.local"),
        share=kwargs.get("share", "backups"),
        path=kwargs.get("path", "PiHerder"),
        username=kwargs.get("username", "backup"),
        password=kwargs.get("password", "smb-secret-9f3c1a"),
        domain=kwargs.get("domain", "WORKGROUP"),
        schedule=kwargs.get("schedule"),
        after_host_backup=kwargs.get("after_host_backup", False),
    )
    return row


def test_blank_password_keeps_saved_secret():
    dest = _dest()
    first = decrypt_str(dest.credentials_encrypted)
    copies.store_smb(
        dest,
        host="nas.local",
        share="backups",
        path="PiHerder",
        username="other",
        password="",
        domain="",
        schedule="30 4 * * *",
        after_host_backup=True,
    )
    secret = json.loads(decrypt_str(dest.credentials_encrypted))
    assert secret["password"] == "smb-secret-9f3c1a"
    assert secret["username"] == "other"
    assert secret["password"] not in (dest.config_json or "")
    assert "smb-secret-9f3c1a" not in dest.credentials_encrypted
    assert json.loads(first)["password"] == secret["password"]
    public = copies.smb_public(dest)
    assert public["username"] == "other"
    assert public["password_saved"] is True
    assert "password" not in public
    assert public["domain"] == ""


def test_guest_is_both_empty_and_half_filled_is_refused():
    dest = BackupDestination(provider="smb", name="LAN NAS / SMB")
    with pytest.raises(ValueError, match="user"):
        copies.store_smb(
            dest, host="nas.local", share="backups", path="", username="",
            password="x", domain="", schedule=None, after_host_backup=False,
        )
    with pytest.raises(ValueError, match="password"):
        copies.store_smb(
            dest, host="nas.local", share="backups", path="", username="backup",
            password="", domain="", schedule=None, after_host_backup=False,
        )
    copies.store_smb(
        dest, host="nas.local", share="backups", path="PiHerder", username="",
        password="", domain="", schedule=None, after_host_backup=False,
    )
    secret = json.loads(decrypt_str(dest.credentials_encrypted))
    assert secret == {"username": "", "password": ""}
    assert "Guest" not in (dest.credentials_encrypted or "")
    assert "Guest" not in (dest.config_json or "")
    public = copies.smb_public(dest)
    assert public["guest"] is True
    assert public["password_saved"] is False
    assert public["username"] == ""
    with pytest.raises(ValueError, match="path"):
        copies.store_smb(
            dest, host="nas.local", share="backups", path="../etc", username="backup",
            password="x", domain="", schedule=None, after_host_backup=False,
        )
    assert copies.normalize_provider("onedrive") == "onedrive"
    with pytest.raises(ValueError, match="provider"):
        copies.normalize_provider("s3")


def test_both_empty_replaces_a_saved_password():
    dest = _dest()
    copies.store_smb(
        dest, host="nas.local", share="backups", path="", username="",
        password="", domain="", schedule=None, after_host_backup=False,
    )
    secret = json.loads(decrypt_str(dest.credentials_encrypted))
    assert secret["password"] == ""
    assert secret["username"] == ""
    assert copies.smb_public(dest)["guest"] is True


def test_rclone_config_is_private_and_password_is_obscured():
    dest = _dest()
    path = copies.write_smb_rclone_config(dest)
    try:
        text = open(path, encoding="utf-8").read()
        mode = os.stat(path).st_mode & 0o777
    finally:
        copies.discard_rclone_config(path)
    assert mode == 0o600
    assert not os.path.exists(path)
    assert "type = smb" in text
    assert "host = nas.local" in text
    assert "user = backup" in text
    assert "domain = WORKGROUP" in text
    assert "use_kerberos" not in text
    assert "smb-secret-9f3c1a" not in text
    obscured = next(line.split(" = ", 1)[1] for line in text.splitlines() if line.startswith("pass = "))
    assert copies._rclone_reveal(obscured) == "smb-secret-9f3c1a"


def test_guest_rclone_config_uses_guest_user_and_no_password():
    dest = BackupDestination(provider="smb", name="LAN NAS / SMB")
    copies.store_smb(
        dest, host="nas.local", share="public", path="", username="",
        password="", domain="", schedule=None, after_host_backup=False,
    )
    path = copies.write_smb_rclone_config(dest)
    try:
        text = open(path, encoding="utf-8").read()
    finally:
        copies.discard_rclone_config(path)
    assert "user = Guest" in text
    assert "pass = " not in text
    assert "type = smb" in text
    assert "host = nas.local" in text
    assert "use_kerberos" not in text


def test_guest_probe_and_copy_do_not_log_credentials(tmp_path, monkeypatch, caplog):
    dest = BackupDestination(provider="smb", name="LAN NAS / SMB")
    copies.store_smb(
        dest, host="nas.local", share="public", path="", username="",
        password="", domain="", schedule=None, after_host_backup=False,
    )
    dest.selection_json = '{"checked":["host"],"skipped":[]}'
    root = tmp_path / "backups"
    (root / "host").mkdir(parents=True)
    monkeypatch.setattr(copies, "backup_root", lambda: root)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    seen = []

    def run(cmd, **_kwargs):
        seen.append(list(cmd))
        config = cmd[cmd.index("--config") + 1]
        text = open(config, encoding="utf-8").read()
        assert "user = Guest" in text
        assert "pass = " not in text
        assert "Guest" not in " ".join(cmd)

        class Proc:
            returncode = 0
            stderr = "ok"
            stdout = ""

        return Proc()

    monkeypatch.setattr(copies.subprocess, "run", run)
    assert copies.probe(dest) == {"ok": "1", "code": "ok"}
    with caplog.at_level(logging.DEBUG):
        result = copies.execute(dest)
    assert result["ok"] is True
    assert seen[0][1] == "lsd"
    assert seen[1][1] == "sync"
    assert "Guest" not in caplog.text
    assert not os.path.exists(seen[0][seen[0].index("--config") + 1])


def test_probe_lists_only_and_deletes_config(monkeypatch):
    dest = _dest()
    cmds = []
    seen = {}

    def run(cmd, **_kwargs):
        cmds.append(list(cmd))
        seen["config"] = cmd[cmd.index("--config") + 1]
        assert os.stat(seen["config"]).st_mode & 0o777 == 0o600
        assert "smb-secret-9f3c1a" not in " ".join(cmd)

        class Proc:
            returncode = 0
            stderr = ""
            stdout = ""

        return Proc()

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(copies.subprocess, "run", run)
    assert copies.probe(dest) == {"ok": "1", "code": "ok"}
    assert cmds[0][0:2] == ["rclone", "lsd"]
    assert "sync" not in cmds[0]
    assert "copy" not in cmds[0]
    assert "copyto" not in cmds[0]
    assert cmds[0][2] == "dest:backups/PiHerder"
    assert not os.path.exists(seen["config"])


def test_execute_syncs_without_trash_and_redacts_the_password(tmp_path, monkeypatch, caplog):
    root = tmp_path / "backups"
    (root / "host").mkdir(parents=True)
    monkeypatch.setattr(copies, "backup_root", lambda: root)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    dest = _dest(path="")
    dest.selection_json = '{"checked":["host"],"skipped":[]}'
    cmds = []
    seen = {}

    def run(cmd, **_kwargs):
        cmds.append(list(cmd))
        assert "smb-secret-9f3c1a" not in " ".join(cmd)
        config = cmd[cmd.index("--config") + 1]
        obscured = next(
            line.split(" = ", 1)[1].strip()
            for line in open(config, encoding="utf-8")
            if line.startswith("pass = ")
        )
        seen["obscured"] = obscured

        class Proc:
            returncode = 1
            stderr = f"NT_STATUS_LOGON_FAILURE smb-secret-9f3c1a {obscured}"
            stdout = ""

        return Proc()

    monkeypatch.setattr(copies.subprocess, "run", run)
    with caplog.at_level(logging.DEBUG):
        result = copies.execute(dest)
    assert result["ok"] is False
    assert "smb-secret-9f3c1a" not in result["error"]
    assert "[redacted]" in result["error"]
    assert "pass = " not in result["error"]
    assert seen["obscured"] not in result["error"]
    assert "smb-secret-9f3c1a" not in caplog.text
    assert cmds[0][1] == "sync"
    assert "--drive-use-trash" not in cmds[0]
    assert "dest:backups/host" in cmds[0]
    config = cmds[0][cmds[0].index("--config") + 1]
    assert not os.path.exists(config)


def test_execute_success_remote_and_demo_refusal(tmp_path, monkeypatch):
    root = tmp_path / "backups"
    (root / "host").mkdir(parents=True)
    monkeypatch.setattr(copies, "backup_root", lambda: root)
    dest = _dest(path="PiHerder")
    dest.selection_json = '{"checked":["host"],"skipped":[]}'
    cmds = []

    def run(cmd, **_kwargs):
        cmds.append(list(cmd))

        class Proc:
            returncode = 0
            stderr = ""
            stdout = ""

        return Proc()

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(copies.subprocess, "run", run)
    result = copies.execute(dest)
    assert result["ok"] is True
    assert result["copied"] == ["host"]
    assert cmds[0][1] == "sync"
    assert "dest:backups/PiHerder/host" in cmds[0]
    assert "--drive-use-trash" not in cmds[0]
    assert not os.path.exists(cmds[0][cmds[0].index("--config") + 1])

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)

    def boom(*_a, **_k):
        raise AssertionError("rclone must not run")

    monkeypatch.setattr(copies.subprocess, "run", boom)
    refused = copies.execute(dest)
    assert refused == {"ok": False, "error": "Demo does not upload"}
    assert copies.probe(dest)["code"] == "demo"


def test_smb_copy_is_not_an_api_or_mcp_job():
    assert "backup_replicate" not in JOB_FEATURE_KEY


def test_worker_failure_marks_only_the_copy_job(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'copy.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.tasks.engine", engine)
    when = datetime(2026, 1, 2, 3, 4, 5)
    with Session(engine) as session:
        server = Server(name="pi", hostname="pi.local", last_backup_at=when)
        dest = BackupDestination(
            name="LAN NAS / SMB",
            provider="smb",
            selection_json='{"checked":["pi"],"skipped":[]}',
            credentials_encrypted="stored",
            config_json='{"host":"nas.local","share":"backups"}',
        )
        session.add(server)
        session.add(dest)
        session.commit()
        session.refresh(server)
        session.refresh(dest)
        job = Job(
            server_id=server.id,
            job_type="backup_replicate",
            status="pending",
            details=json.dumps({"destination_id": dest.id, "current": "queued"}),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id
        server_id = server.id

    def fake_execute(destination, scope=None):
        assert destination.provider == "smb"
        assert scope is None
        return {"ok": False, "error": "share down"}

    monkeypatch.setattr("app.services.backup_replicate.execute", fake_execute)
    from app.tasks import replicate_backup

    out = replicate_backup.run(job_id)
    assert out["status"] == "failed"
    with Session(engine) as session:
        job = session.get(Job, job_id)
        server = session.get(Server, server_id)
        assert job.status == "failed"
        assert json.loads(job.details)["error"] == "share down"
        assert server.last_backup_at == when


def test_enqueue_wires_backup_replicate(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'enq.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    called = {}

    def delay(job_id):
        called["id"] = job_id

        class Result:
            id = "task-smb"

        return Result()

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr("app.tasks.replicate_backup.delay", delay)
    with Session(engine) as session:
        dest = _dest()
        session.add(dest)
        session.commit()
        session.refresh(dest)
        job = copies.enqueue(session, dest)
        assert job.job_type == "backup_replicate"
        assert job.id == called["id"]
        assert job.celery_task_id == "task-smb"
        assert "SMB copy queued" in (job.details or "")
        assert "smb-secret-9f3c1a" not in (job.details or "")

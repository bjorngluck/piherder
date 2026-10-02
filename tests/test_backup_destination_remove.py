"""Remove one backup destination. The other provider, and remote files, stay."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import AuditLog, BackupDestination, Job, Server, User
from app.security.auth import get_admin_user, get_current_user, get_password_hash
from app.security.encryption import decrypt_str
from app.services import backup_replicate as copies
from app.services.audit_format import format_audit_entry
from app.services.demo import DemoBlocked
from app.services.scheduler import schedule_backup_copy_job, sync_backup_copy_schedule

SMB_PASSWORD = "smb-secret-9f3c1a"
DRIVE_REFRESH = "1//refresh-secret-aa91"
CLIENT_SECRET = "GOCSPX-secret-value"


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'dest.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _drive() -> BackupDestination:
    row = BackupDestination(
        provider="drive",
        name="Google Drive",
        enabled=True,
        selection_json='{"checked":["pi"],"skipped":[]}',
    )
    copies.store_oauth_client(
        row,
        client_id="123-abc.apps.googleusercontent.com",
        client_secret=CLIENT_SECRET,
        folder="PiHerder",
        schedule="0 4 * * *",
        after_host_backup=True,
    )
    row.credentials_encrypted = copies.encrypt_str(
        copies.pack_oauth_token(
            client_id="123-abc.apps.googleusercontent.com",
            client_secret=CLIENT_SECRET,
            refresh_token=DRIVE_REFRESH,
            access_token="ya29.access",
            email="owner@example.com",
            expires_in=3600,
        )
    )
    return row


def _smb() -> BackupDestination:
    row = BackupDestination(
        provider="smb",
        name="LAN NAS / SMB",
        selection_json='{"checked":["pi"],"skipped":[]}',
    )
    copies.store_smb(
        row,
        host="nas.local",
        share="backups",
        path="PiHerder",
        username="backup",
        password=SMB_PASSWORD,
        domain="WORKGROUP",
        schedule="30 4 * * *",
        after_host_backup=True,
    )
    return row


def _seed(engine) -> tuple[str, str]:
    with Session(engine) as session:
        drive = _drive()
        smb = _smb()
        session.add(drive)
        session.add(smb)
        session.commit()
        session.refresh(drive)
        session.refresh(smb)
        return drive.credentials_encrypted or "", smb.credentials_encrypted or ""


def _patch_schedule(monkeypatch):
    monkeypatch.setattr(copies, "refresh_copy_schedule", lambda: None)


def test_remove_one_destination_leaves_the_other_and_drops_fernet(tmp_path, monkeypatch):
    _patch_schedule(monkeypatch)
    engine = _engine(tmp_path)
    drive_cipher, smb_cipher = _seed(engine)
    assert SMB_PASSWORD not in smb_cipher
    assert DRIVE_REFRESH not in drive_cipher
    with Session(engine) as session:
        summary = copies.remove_destination(session, "smb", confirm="remove")
    assert summary["provider"] == "smb"
    assert summary["remote_files"] == "kept"
    blob = json.dumps(summary)
    assert SMB_PASSWORD not in blob
    assert DRIVE_REFRESH not in blob
    assert CLIENT_SECRET not in blob
    with Session(engine) as session:
        rows = list(session.exec(select(BackupDestination)).all())
        assert [row.provider for row in rows] == ["drive"]
        drive = rows[0]
        assert drive.schedule == "0 4 * * *"
        assert drive.after_host_backup is True
        token = json.loads(copies.decrypt_token(drive))
        assert token["refresh_token"] == DRIVE_REFRESH
        assert copies.oauth_client(drive)["client_secret"] == CLIENT_SECRET
        stored = " ".join(
            (row.credentials_encrypted or "") + (row.config_json or "") for row in rows
        )
        assert smb_cipher not in stored
        assert SMB_PASSWORD not in stored
        assert drive.credentials_encrypted == drive_cipher


def test_remove_drive_leaves_smb(tmp_path, monkeypatch):
    _patch_schedule(monkeypatch)
    engine = _engine(tmp_path)
    _seed(engine)
    with Session(engine) as session:
        copies.remove_destination(session, "drive", confirm="remove")
        smb = copies.find_destination(session, "smb")
        assert smb is not None
        assert copies.find_destination(session, "drive") is None
        secret = json.loads(decrypt_str(smb.credentials_encrypted))
        assert secret["password"] == SMB_PASSWORD
        assert smb.schedule == "30 4 * * *"
        assert smb.after_host_backup is True


def test_confirm_is_required(tmp_path, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(copies, "refresh_copy_schedule", lambda: called.__setitem__("n", 1))
    engine = _engine(tmp_path)
    _seed(engine)
    with Session(engine) as session:
        with pytest.raises(ValueError, match="confirm"):
            copies.remove_destination(session, "smb", confirm="")
        assert copies.find_destination(session, "smb") is not None
        assert copies.find_destination(session, "drive") is not None
    assert called["n"] == 0


def test_demo_refuses_remove(tmp_path, monkeypatch):
    _patch_schedule(monkeypatch)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    engine = _engine(tmp_path)
    _seed(engine)
    with Session(engine) as session:
        with pytest.raises(DemoBlocked):
            copies.remove_destination(session, "drive", confirm="remove")
        assert copies.find_destination(session, "drive") is not None
        assert copies.find_destination(session, "smb") is not None


def test_copy_now_and_follow_up_refuse_for_the_removed_destination(tmp_path, monkeypatch):
    _patch_schedule(monkeypatch)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    queued = []

    def delay(job_id):
        queued.append(job_id)

        class Result:
            id = "task-copy"

        return Result()

    monkeypatch.setattr("app.tasks.replicate_backup.delay", delay)
    engine = _engine(tmp_path)
    _seed(engine)
    with Session(engine) as session:
        copies.remove_destination(session, "smb", confirm="remove")
        blank = copies.get_or_create(session, "smb")
        refused = copies.enqueue(session, blank, user_id=1)
        assert refused.status == "failed"
        assert refused.id is None
        assert json.loads(refused.details)["error"] == "removed"
        assert SMB_PASSWORD not in (refused.details or "")
        assert queued == []
        server = Server(name="pi", hostname="pi.local", backup_folder_name="pi")
        session.add(server)
        session.commit()
        session.refresh(server)
        copies.enqueue_after_host_backup(session, server)
        jobs = list(session.exec(select(Job)).all())
        assert len(jobs) == 1
        assert jobs[0].job_type == "backup_replicate"
        details = json.loads(jobs[0].details or "{}")
        drive = copies.find_destination(session, "drive")
        assert details["destination_id"] == drive.id
        assert SMB_PASSWORD not in jobs[0].details
        assert CLIENT_SECRET not in jobs[0].details
        assert queued == [jobs[0].id]


def test_sync_drops_only_the_removed_cron(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        drive = _drive()
        smb = _smb()
        session.add(drive)
        session.add(smb)
        session.commit()
        session.refresh(drive)
        session.refresh(smb)
        drive_id = drive.id
        smb_id = smb.id
    monkeypatch.setattr("app.database.engine", engine)

    class Fake:
        def __init__(self):
            self.jobs = {}

        def get_jobs(self):
            return [SimpleNamespace(id=job_id) for job_id in list(self.jobs)]

        def add_job(self, func, trigger, args, id, replace_existing, name):
            del func, trigger, replace_existing, name
            self.jobs[id] = args

        def remove_job(self, job_id):
            self.jobs.pop(job_id, None)

    sched = Fake()
    sync_backup_copy_schedule(sched, True)
    assert f"backup_copy_{drive_id}" in sched.jobs
    assert f"backup_copy_{smb_id}" in sched.jobs
    with Session(engine) as session:
        session.delete(session.get(BackupDestination, smb_id))
        session.commit()
    sync_backup_copy_schedule(sched, True)
    assert f"backup_copy_{drive_id}" in sched.jobs
    assert f"backup_copy_{smb_id}" not in sched.jobs


def test_scheduled_tick_skips_a_destination_without_credentials(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        dest = BackupDestination(
            provider="smb",
            name="LAN NAS / SMB",
            enabled=True,
            schedule="30 4 * * *",
        )
        session.add(dest)
        session.commit()
        session.refresh(dest)
        dest_id = dest.id
    monkeypatch.setattr("app.database.engine", engine)
    called = []
    monkeypatch.setattr(copies, "enqueue", lambda *args, **kwargs: called.append(1))
    schedule_backup_copy_job(dest_id)
    assert called == []


def test_settings_remove_requires_confirm_and_audits_without_secrets(tmp_path, monkeypatch):
    _patch_schedule(monkeypatch)
    engine = _engine(tmp_path)
    _seed(engine)
    with Session(engine) as session:
        user = User(
            email="admin@example.com",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        admin = user

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_admin_user] = lambda: admin
    try:
        client = TestClient(app, raise_server_exceptions=False)
        denied = client.post(
            "/backup-copies/remove",
            data={"provider": "smb"},
            follow_redirects=False,
        )
        assert denied.status_code == 303
        assert "copy_error=confirm" in denied.headers["location"]
        assert "copy_provider=smb" in denied.headers["location"]
        with Session(engine) as session:
            assert copies.find_destination(session, "smb") is not None
        removed = client.post(
            "/backup-copies/remove",
            data={"provider": "smb", "confirm": "remove"},
            follow_redirects=False,
        )
        assert removed.status_code == 303
        assert "copy_removed=1" in removed.headers["location"]
        with Session(engine) as session:
            assert copies.find_destination(session, "smb") is None
            assert copies.find_destination(session, "drive") is not None
            audit = session.exec(select(AuditLog)).all()
            assert len(audit) == 1
            assert audit[0].action == "backup_destination_remove"
            details = audit[0].details or ""
            assert SMB_PASSWORD not in details
            assert DRIVE_REFRESH not in details
            assert CLIENT_SECRET not in details
            shown = format_audit_entry(
                {"action": audit[0].action, "status": "success", "details": details}
            )
            assert "smb" in shown["summary"]
            assert "left in place" in shown["summary"]
            assert SMB_PASSWORD not in shown["summary"]
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_admin_user, None)


def test_demo_http_refuses_remove(tmp_path, monkeypatch):
    _patch_schedule(monkeypatch)
    from app.services import demo as demo_svc

    monkeypatch.setattr(demo_svc.settings, "PIHERDER_DEMO_MODE", True)
    engine = _engine(tmp_path)
    _seed(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_admin_user] = lambda: SimpleNamespace(id=1, role="admin")
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/backup-copies/remove",
            data={"provider": "drive", "confirm": "remove"},
            follow_redirects=False,
        )
        assert response.status_code == 403
        with Session(engine) as session:
            assert copies.find_destination(session, "drive") is not None
            assert copies.find_destination(session, "smb") is not None
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_admin_user, None)


def test_remove_form_uses_the_settings_confirm_bar():
    text = open("app/templates/partials/settings_backup.html", encoding="utf-8").read()
    assert 'action="/backup-copies/remove"' in text
    assert 'name="confirm" value="remove"' in text
    assert 'name="provider" value="{{ row.provider }}"' in text
    assert "data-confirm-danger" in text
    assert "Files already on Drive stay" in text
    assert "Files already on the share stay" in text
    assert "copy_form_error" in text
    assert "OneDrive. Not available yet." in text
    assert "data-copy-add" in text
    assert 'id="copy-provider"' in text
    assert 'for="copy-provider"' not in text
    assert "<select id=\"copy-provider\"" not in text
    assert "data-copy-edit" in text
    api = open("app/routers/api_v1.py", encoding="utf-8").read()
    assert "remove_destination" not in api
    assert "/backup-copies/remove" not in api


def _admin_client(engine):
    with Session(engine) as session:
        user = User(
            email="admin@example.com",
            hashed_password=get_password_hash("SmokeTest1ok"),
            role="admin",
            is_active=True,
            must_change_password=False,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        admin = user

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_admin_user] = lambda: admin
    app.dependency_overrides[get_current_user] = lambda: admin
    return TestClient(app, raise_server_exceptions=False), admin


def test_failed_save_keeps_the_edit_form(tmp_path, monkeypatch):
    _patch_schedule(monkeypatch)
    monkeypatch.setattr("app.security.auth.cookie_secure", lambda: False)
    engine = _engine(tmp_path)
    client, _admin = _admin_client(engine)
    secret = "typed-secret-keep"
    try:
        failed = client.post(
            "/backup-copies/config",
            data={
                "provider": "smb",
                "host": "",
                "share": "media",
                "smb_path": "PiHerder",
                "username": "ada",
                "password": secret,
                "domain": "WORKGROUP",
                "schedule_cron": "0 4 * * *",
                "after_host_backup": "1",
            },
            follow_redirects=False,
        )
        assert failed.status_code == 303
        location = failed.headers["location"]
        assert "copy_error=host" in location
        assert secret not in location
        assert "ada" not in location
        with Session(engine) as session:
            assert copies.find_destination(session, "smb") is None
        page = client.get(location)
        assert page.status_code == 200
        text = page.text
        assert "Enter the NAS hostname or IP." in text
        assert 'value="media"' in text
        assert 'value="ada"' in text
        assert f'value="{secret}"' in text
        assert "settings-drive-copy-modal" in text
        assert "flex" in text
        again = client.get("/herder-backups?tab=backup&copy_error=host&copy_provider=smb")
        assert secret not in again.text
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_admin_user, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_settings_lists_each_dest_and_remove_drops_only_that_row(tmp_path, monkeypatch):
    _patch_schedule(monkeypatch)
    engine = _engine(tmp_path)
    _seed(engine)
    client, _admin = _admin_client(engine)
    try:
        page = client.get("/herder-backups?tab=backup")
        assert page.status_code == 200
        text = page.text
        assert "Google Drive" in text
        assert "LAN NAS / SMB" in text
        assert "owner@example.com" in text
        assert "nas.local/backups/PiHerder" in text
        assert text.count('action="/backup-copies/remove"') == 2
        assert 'name="provider" value="drive"' in text
        assert 'name="provider" value="smb"' in text
        removed = client.post(
            "/backup-copies/remove",
            data={"provider": "smb", "confirm": "remove"},
            follow_redirects=False,
        )
        assert "copy_removed=1" in removed.headers["location"]
        after = client.get(removed.headers["location"])
        assert after.status_code == 200
        body = after.text
        assert "LAN share removed" in body
        assert "nas.local/backups/PiHerder" not in body
        assert "owner@example.com" in body
        assert 'name="provider" value="smb"' not in body
        assert 'name="provider" value="drive"' in body
        with Session(engine) as session:
            assert copies.find_destination(session, "smb") is None
            assert copies.find_destination(session, "drive") is not None
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_admin_user, None)
        app.dependency_overrides.pop(get_current_user, None)

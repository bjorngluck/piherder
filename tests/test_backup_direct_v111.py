"""v1.11 Path C — a host can opt in to send files straight to the copy."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models import BackupDestination, Job, Server, User
from app.security.auth import create_user_access_token, get_password_hash
from app.services import backup as backup_mod
from app.services import backup_replicate as copies
from app.services.backup_direct import (
    NO_DEST_MSG,
    followup_destinations,
    merge_directory,
    parse_direct_targets,
    partition_checked,
    planned_syncs,
    rels_for_push,
    remote_sync_command,
    rewrite_service_account,
    run_direct_backup,
    SUDO_RCLONE,
    root_read_required_message,
    sudo_rclone_cleanup_command,
    sudo_rclone_install_command,
)
from app.services.backup_replicate import destination_place, enqueue_after_host_backup
from app.services.ssh_onboarding import build_sudoers_content


def _input_tag(html: str, testid: str) -> str:
    marker = f'data-testid="{testid}"'
    idx = html.find(marker)
    assert idx != -1, testid
    open_at = html.rfind("<input", 0, idx)
    close_at = html.find(">", idx)
    return html[open_at:close_at]


def _host(**kwargs) -> Server:
    data = dict(
        id=4,
        name="Pi",
        hostname="pi.local",
        backup_enabled=True,
        backup_direct=True,
        backup_paths='[{"source":"/home/pi/docker/","enabled":true}]',
    )
    data.update(kwargs)
    return Server(**data)


def test_destination_place_names_the_share_and_hides_the_sign_in():
    smb = BackupDestination(
        provider="smb",
        config_json=json.dumps(
            {
                "host": "192.168.86.42",
                "share": "piherder-test",
                "remote_dir": "PiHerder",
                "username": "piherder",
                "password": "smb-secret-9f3c1a",
            }
        ),
    )
    place = destination_place(smb)
    assert place == "LAN NAS / SMB · 192.168.86.42/piherder-test/PiHerder"
    assert "smb-secret" not in place
    assert "piherder@" not in place
    drive = BackupDestination(
        provider="drive",
        config_json=json.dumps(
            {
                "remote_dir": "Backup_PiHerder",
                "oauth_client_id": "client-id.apps.googleusercontent.com",
                "oauth_client_secret_encrypted": "enc-secret",
            }
        ),
    )
    drive_place = destination_place(drive)
    assert drive_place == "Google Drive · Backup_PiHerder"
    assert "client-id" not in drive_place
    assert "enc-secret" not in drive_place


def test_audit_names_the_direct_destination():
    from app.services.audit_format import _backup_summary
    from app.services.backup_audit import compact_backup_snippet

    summary = {
        "server": "rpi5-3.hacknow.info",
        "direct": True,
        "where": "LAN NAS / SMB · 192.168.86.42/piherder-test/PiHerder",
        "results": [
            {
                "source": "/home/bjorn/docker/",
                "rel": "rpi5-3.hacknow.info/docker",
                "rc": 0,
                "direct": True,
                "destination": "LAN NAS / SMB",
                "where": (
                    "LAN NAS / SMB · 192.168.86.42/piherder-test/PiHerder/"
                    "rpi5-3.hacknow.info/docker"
                ),
            }
        ],
    }
    snip = compact_backup_snippet(summary, ok=True)
    assert snip["where"] == summary["where"]
    assert snip["direct"] is True
    assert snip["results"][0]["destination"] == "LAN NAS / SMB"
    assert "rpi5-3.hacknow.info/docker" in snip["results"][0]["where"]
    line = _backup_summary(snip)
    assert "LAN NAS / SMB" in line
    assert "192.168.86.42/piherder-test/PiHerder" in line
    assert "smb-secret" not in line


def test_sudoers_allows_the_root_rclone():
    text = build_sudoers_content("ph", backup=True)
    assert "/usr/bin/rsync" in text
    assert SUDO_RCLONE in text


def test_rclone_is_installed_root_owned_and_reads_in_place():
    command = sudo_rclone_install_command("/tmp/piherder-rclone-abc")
    assert "--chown=root:root" in command
    assert command.endswith(" /var/lib/piherder/rclone")
    assert "/tmp/ph-stage" not in command
    assert "--chmod=D700" not in command
    message = root_read_required_message()
    assert "second copy" in message
    assert SUDO_RCLONE in message
    assert len(message) < 220


def test_cleanup_deletes_only_the_staged_binary():
    command = sudo_rclone_cleanup_command("/tmp/ph-empty-abc")
    assert command.startswith("sudo -n rsync")
    assert "--delete" in command
    assert "--include=rclone" in command
    assert command.endswith(" /var/lib/piherder/")
    assert "/tmp/" not in command.split()[-1]


def test_remote_command_points_at_the_temp_config():
    command = remote_sync_command(
        "/var/lib/piherder/rclone",
        config_path="/tmp/ph-rclone-abc.conf",
        source="/home/pi/docker",
        remote_path="PiHerder/pi.local/docker",
        excludes=["skip"],
        use_trash=True,
        use_sudo=True,
    )
    assert "sudo -n /var/lib/piherder/rclone" in command
    assert "/tmp/ph-rclone-abc.conf" in command
    assert "sync /home/pi/docker dest:PiHerder/pi.local/docker" in command
    assert "--local-no-check-updated" in command
    assert "--drive-use-trash" in command
    assert "secret-value" not in command


def test_service_account_path_is_rewritten_for_the_host():
    text, local = rewrite_service_account(
        "[dest]\nservice_account_file = /tmp/ph-rclone-old.conf.sa.json\ntype = drive\n",
        "/tmp/ph-rclone-new.sa.json",
    )
    assert local == "/tmp/ph-rclone-old.conf.sa.json"
    assert "service_account_file = /tmp/ph-rclone-new.sa.json" in text
    assert "old.conf" not in text


def test_partition_leaves_a_normal_host_on_the_pull():
    direct = _host()
    normal = _host(id=5, hostname="other.local", backup_direct=False)
    opted_out = _host(id=6, hostname="off.local", backup_direct=True, backup_enabled=False)
    local, groups = partition_checked(
        ["pi.local", "other.local/docker", "off.local"],
        hosts=[direct, normal, opted_out],
    )
    assert local == ["other.local/docker", "off.local"]
    assert len(groups) == 1
    assert groups[0][0].hostname == "pi.local"
    assert groups[0][1] == ["pi.local"]


def test_planned_syncs_follow_the_ticked_folder():
    server = _host(
        backup_paths=(
            '[{"source":"/home/pi/docker/","enabled":true},'
            '{"source":"/var/lib/docker/volumes/","enabled":true}]'
        )
    )
    sources = server.get_backup_sources()
    whole = planned_syncs(server, sources, ["pi.local"], [])
    assert {item["rel"] for item in whole} == {"pi.local/docker", "pi.local/volumes"}
    child = planned_syncs(server, sources, ["pi.local/docker/compose"], [])
    assert len(child) == 1
    assert child[0]["local"] == "/home/pi/docker/compose"
    assert child[0]["rel"] == "pi.local/docker/compose"


def test_direct_folder_is_listed_without_a_local_tree():
    server = _host()

    class FakeSession:
        def exec(self, _query):
            return self

        def all(self):
            return [server]

    rows = merge_directory(FakeSession(), "", None)
    assert rows is not None
    assert rows[0]["path"] == "pi.local"
    assert rows[0]["direct"] is True
    children = merge_directory(FakeSession(), "pi.local", None)
    assert [row["name"] for row in children] == ["docker"]
    assert merge_directory(FakeSession(), "missing", None) is None
    on_disk = merge_directory(
        FakeSession(),
        "pi.local",
        [{"name": "docker", "path": "pi.local/docker", "is_dir": True, "size": 4, "size_h": "4 B", "mtime": 1}],
    )
    assert len(on_disk) == 1
    assert on_disk[0].get("direct") is not True
    assert on_disk[0]["size"] == 4


def test_backup_now_uses_the_ticked_destinations():
    drive = BackupDestination(provider="drive", enabled=True, credentials_encrypted="tok")
    nas = BackupDestination(provider="smb", enabled=True, credentials_encrypted="tok")
    unsigned = BackupDestination(provider="onedrive", enabled=True, credentials_encrypted="")

    class Fake:
        def exec(self, _query):
            return self

        def all(self):
            return [nas, unsigned, drive]

    only_nas = _host(backup_direct_targets='["smb"]')
    assert [row.provider for row in followup_destinations(Fake(), only_nas)] == ["smb"]
    unset = _host(backup_direct_targets=None)
    assert followup_destinations(Fake(), unset) == []
    none = _host(backup_direct_targets="[]")
    assert followup_destinations(Fake(), none) == []
    assert parse_direct_targets("") == []
    assert parse_direct_targets('["drive","nope","drive"]') == ["drive"]


def test_unticked_folder_still_sends_the_whole_host():
    server = _host()
    dest = BackupDestination(provider="drive", selection_json=None)
    rels, skipped = rels_for_push(dest, server)
    assert rels == ["pi.local"]
    assert skipped == []


def test_host_backup_does_not_create_a_herder_tree(monkeypatch):
    seen = {}

    def fake_direct(server, sources_override=None, job_id=None):
        seen["hostname"] = server.hostname
        return {"server": server.hostname, "direct": True, "results": [{"source": "/home/pi/docker", "rc": 0}]}

    monkeypatch.setattr("app.services.backup_direct.run_direct_backup", fake_direct)
    monkeypatch.setattr(
        backup_mod,
        "get_backup_root_for_server",
        lambda _server: (_ for _ in ()).throw(AssertionError("herder tree")),
    )
    result = backup_mod.run_backup(_host())
    assert seen["hostname"] == "pi.local"
    assert result["direct"] is True
    assert backup_mod.backup_succeeded(result) is True


def test_direct_backup_has_nowhere_to_send(monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)

    class FakeSession:
        def __init__(self, *_args, **_kwargs):
            self.expire_on_commit = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def expunge(self, _row):
            return None

    monkeypatch.setattr("app.services.backup_direct.Session", FakeSession)
    monkeypatch.setattr("app.services.backup_direct.followup_destinations", lambda *_a, **_k: [])
    result = run_direct_backup(_host())
    assert result["error"] == NO_DEST_MSG
    assert result["results"] == []


def test_demo_does_not_upload_a_direct_host(monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)

    def boom(*_args, **_kwargs):
        raise AssertionError("session")

    monkeypatch.setattr("app.services.backup_direct.Session", boom)
    result = run_direct_backup(_host())
    assert result["error"] == "Demo does not upload"


def test_follow_up_copy_skips_a_direct_host():
    session = MagicMock()
    enqueue_after_host_backup(session, _host())
    session.exec.assert_not_called()


def test_folder_copy_leaves_a_direct_host_alone(monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    server = _host()

    def partition(checked, hosts=None):
        return [], [(server, list(checked))]

    def boom(*_args, **_kwargs):
        raise AssertionError("herder rclone")

    monkeypatch.setattr("app.services.backup_direct.partition_checked", partition)
    monkeypatch.setattr("app.services.backup_direct.push_groups", boom)
    monkeypatch.setattr(copies.subprocess, "run", boom)
    dest = BackupDestination(
        id=8,
        provider="drive",
        selection_json='{"checked":["pi.local"],"skipped":[]}',
        credentials_encrypted="x",
        config_json='{"remote_dir":"PiHerder"}',
    )
    result = copies.execute(dest)
    assert result["ok"] is False
    assert result["error"] == "Nothing selected on the backup drive"


def test_direct_job_starts_the_host_push(monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    server = _host()
    dest = BackupDestination(
        id=8,
        provider="drive",
        selection_json='{"checked":["pi.local"],"skipped":[]}',
        credentials_encrypted="x",
        config_json='{"remote_dir":"PiHerder"}',
    )

    def partition(checked, hosts=None):
        return [], [(server, list(checked))]

    def push(destination, groups, skipped):
        assert destination is dest
        assert groups[0][0] is server
        assert skipped == []
        return ["pi.local/docker"], []

    def boom(*_args, **_kwargs):
        raise AssertionError("herder rclone")

    monkeypatch.setattr("app.services.backup_direct.partition_checked", partition)
    monkeypatch.setattr("app.services.backup_direct.push_groups", push)
    monkeypatch.setattr(copies.subprocess, "run", boom)
    result = copies.execute_direct(dest)
    assert result["ok"] is True
    assert result["copied"] == ["pi.local/docker"]
    assert "ya29." not in json.dumps(result)


def _delay_recorder(monkeypatch):
    queued: list[int] = []

    def delay(job_id):
        queued.append(job_id)

        class Result:
            id = f"task-{job_id}"

        return Result()

    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr("app.tasks.replicate_backup.delay", delay)
    return queued


def _sqlite(tmp_path, name):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_copy_now_of_a_direct_host_is_its_own_row(tmp_path, monkeypatch):
    queued = _delay_recorder(monkeypatch)
    server = _host()
    secret = "smb-secret-9f3c1a"
    token = "ya29.SUPERSECRETACCESSTOKEN"

    def partition(checked, hosts=None):
        return [], [(server, list(checked))]

    monkeypatch.setattr("app.services.backup_direct.partition_checked", partition)
    engine = _sqlite(tmp_path, "direct-job.db")
    with Session(engine) as session:
        dest = BackupDestination(
            provider="smb",
            name="LAN",
            enabled=True,
            credentials_encrypted=secret,
            selection_json='{"checked":["pi.local"],"skipped":[]}',
            config_json='{"password":"plain-pass"}',
        )
        session.add(dest)
        session.commit()
        session.refresh(dest)
        job = copies.enqueue(session, dest, user_id=3)
        details = json.loads(job.details or "{}")
        assert job.job_type == "backup_replicate"
        assert details["direct_host"] is True
        assert "direct_after" not in details
        assert details["log_lines"] == ["Direct copy to the LAN share queued…"]
        assert secret not in (job.details or "")
        assert "plain-pass" not in (job.details or "")
        assert token not in (job.details or "")
        again = copies.enqueue(session, dest)
        assert again.id == job.id
        folder = copies.enqueue(session, dest, scope="other.local")
        assert folder.id == job.id
    assert queued == [job.id]


def test_mixed_copy_keeps_the_folder_row_and_queues_direct_after(tmp_path, monkeypatch):
    queued = _delay_recorder(monkeypatch)
    server = _host()

    def partition(checked, hosts=None):
        local = [path for path in checked if path != "pi.local"]
        groups = [(server, ["pi.local"])] if "pi.local" in checked else []
        return local, groups

    monkeypatch.setattr("app.services.backup_direct.partition_checked", partition)
    engine = _sqlite(tmp_path, "mixed-job.db")
    monkeypatch.setattr("app.tasks.engine", engine)

    def folder_only(destination, scope=None):
        assert scope is None
        return {"ok": True, "copied": ["other.local"]}

    def no_push(*_args, **_kwargs):
        raise AssertionError("direct push stays out of the folder hop")

    monkeypatch.setattr("app.services.backup_replicate.execute", folder_only)
    monkeypatch.setattr("app.services.backup_direct.push_groups", no_push)
    with Session(engine) as session:
        dest = BackupDestination(
            provider="drive",
            name="Drive",
            enabled=True,
            credentials_encrypted="tok-value",
            selection_json='{"checked":["other.local","pi.local"],"skipped":[]}',
        )
        session.add(dest)
        session.commit()
        session.refresh(dest)
        folder = copies.enqueue(session, dest)
        folder_id = folder.id
        details = json.loads(folder.details or "{}")
        assert details["direct_after"] is True
        assert details.get("direct_host") is not True
        assert details["log_lines"] == ["Drive copy queued…"]
        assert "tok-value" not in (folder.details or "")

    from app.tasks import replicate_backup

    out = replicate_backup.run(folder_id)
    assert out["status"] == "success"
    with Session(engine) as session:
        rows = list(session.exec(select(Job).where(Job.job_type == "backup_replicate")).all())
        assert len(rows) == 2
        direct = next(row for row in rows if row.id != folder_id)
        direct_details = json.loads(direct.details or "{}")
        folder_details = json.loads(session.get(Job, folder_id).details or "{}")
        assert direct_details["direct_host"] is True
        assert direct_details.get("direct_after") is not True
        assert "Direct copy to Drive queued" in direct_details["log_lines"][0]
        assert folder_details.get("direct_host") is not True
        assert "tok-value" not in (direct.details or "")
        assert "ya29." not in (direct.details or "")
        direct_id = direct.id
    assert queued == [folder_id, direct_id]


def test_direct_worker_stores_a_scrubbed_error(tmp_path, monkeypatch):
    engine = _sqlite(tmp_path, "direct-err.db")
    monkeypatch.setattr("app.tasks.engine", engine)
    with Session(engine) as session:
        dest = BackupDestination(
            provider="drive",
            name="Drive",
            enabled=True,
            credentials_encrypted="tok-value",
        )
        session.add(dest)
        session.commit()
        session.refresh(dest)
        job = Job(
            job_type="backup_replicate",
            status="pending",
            details=json.dumps(
                {
                    "destination_id": dest.id,
                    "direct_host": True,
                    "log_lines": ["Direct copy to Drive queued…"],
                }
            ),
        )
        session.add(job)
        session.commit()
        job_id = job.id

    def failed(_destination, scope=None):
        return {"ok": False, "error": "rclone said ya29.SUPERSECRETACCESSTOKEN"}

    def boom(*_args, **_kwargs):
        raise AssertionError("folder hop")

    monkeypatch.setattr("app.services.backup_replicate.execute_direct", failed)
    monkeypatch.setattr("app.services.backup_replicate.execute", boom)
    from app.tasks import replicate_backup

    out = replicate_backup.run(job_id)
    assert out["status"] == "failed"
    assert "ya29." not in out["error"]
    assert "[redacted]" in out["error"]
    with Session(engine) as session:
        stored = session.get(Job, job_id)
        assert "ya29." not in (stored.details or "")
        assert json.loads(stored.details)["direct_host"] is True


def test_worker_death_line_names_a_direct_copy(tmp_path, monkeypatch):
    engine = _sqlite(tmp_path, "direct-stop.db")
    monkeypatch.setattr("app.tasks.engine", engine)
    with Session(engine) as session:
        job = Job(
            job_type="backup_replicate",
            status="running",
            details=json.dumps(
                {"direct_host": True, "current": "copying", "log_lines": ["Direct copy to Drive queued…"]}
            ),
        )
        session.add(job)
        session.commit()
        job_id = job.id
    from app.tasks import fail_replicate_job

    assert fail_replicate_job(
        job_id, "Backup copy hit the worker time limit and was stopped."
    )
    with Session(engine) as session:
        details = json.loads(session.get(Job, job_id).details or "{}")
    assert details["error"].startswith("Direct copy hit")
    assert "Backup copy" not in details["error"]
    assert details["log_lines"][-1] == details["error"]


def test_normal_host_still_errors_when_the_mirror_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(copies, "backup_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(
        "app.services.backup_direct.partition_checked",
        lambda checked, hosts=None: (list(checked), []),
    )
    dest = BackupDestination(
        provider="onedrive",
        selection_json='{"checked":["pi"],"skipped":[]}',
        credentials_encrypted="x",
        config_json='{"remote_dir":"PiHerder"}',
    )
    monkeypatch.setattr(copies, "onedrive_default_drive", lambda _token: ("drive-1", "personal"))
    monkeypatch.setattr(
        copies,
        "decrypt_token",
        lambda _dest: copies.pack_oauth_token(
            client_id="app-id",
            client_secret="secret-value",
            refresh_token="refresh-value",
            access_token="access",
            email="bjorn@outlook.com",
            expires_in=None,
        ),
    )
    result = copies.execute(dest)
    assert result["ok"] is False
    assert "not on the backup drive" in result["error"]
    assert "secret-value" not in result["error"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.account_stepup.force_2fa_applies", lambda *_a, **_k: False)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'direct-backup.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    test_client = TestClient(app, raise_server_exceptions=False)
    try:
        yield test_client, engine
    finally:
        app.dependency_overrides.clear()


def test_configure_opts_the_host_in_and_retention_leaves_it(client):
    test_client, engine = client
    with Session(engine) as session:
        user = User(
            email="direct@test.local",
            hashed_password=get_password_hash("SmokeTest1ok!"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        server = Server(name="Pi", hostname="pi.local", backup_enabled=True, backup_direct=False)
        session.add(user)
        session.add(server)
        session.add(BackupDestination(provider="drive", enabled=True, credentials_encrypted="tok", name="Google Drive"))
        session.add(BackupDestination(provider="smb", enabled=True, credentials_encrypted="tok", name="LAN"))
        session.commit()
        session.refresh(user)
        session.refresh(server)
        server_id = server.id
    test_client.cookies.set("access_token", create_user_access_token(user))
    page = test_client.get(f"/servers/{server_id}/backups")
    assert page.status_code == 200
    assert 'data-testid="backup-direct"' in page.text
    assert 'data-testid="backup-direct-all"' not in page.text
    assert 'data-testid="backup-direct-target-drive"' in page.text
    assert 'data-testid="backup-direct-target-smb"' in page.text
    assert 'data-testid="backup-direct-target-onedrive"' not in page.text
    assert "disabled" not in _input_tag(page.text, "backup-direct")
    assert "checked" not in _input_tag(page.text, "backup-direct-target-drive")
    assert "checked" not in _input_tag(page.text, "backup-direct-target-smb")
    refused = test_client.post(
        f"/servers/{server_id}/backup-config",
        data={
            "backup_paths": "/tmp/should-not-save",
            "retention_days": "7",
            "backup_schedule": "",
            "dest_root": "/backups",
            "folder_name": "pi.local",
            "scope": "this_host",
            "backup_direct_set": "1",
            "backup_direct": "1",
        },
        follow_redirects=False,
    )
    assert refused.status_code == 400
    assert "Select a destination" in refused.text
    with Session(engine) as session:
        row = session.get(Server, server_id)
        assert row.backup_direct is False
        assert "/tmp/should-not-save" not in (row.backup_paths or "")
    saved = test_client.post(
        f"/servers/{server_id}/backup-config",
        data={
            "backup_paths": "/home/pi/docker",
            "retention_days": "7",
            "backup_schedule": "",
            "dest_root": "/backups",
            "folder_name": "pi.local",
            "scope": "this_host",
            "backup_direct_set": "1",
            "backup_direct": "1",
            "backup_direct_targets_set": "1",
            "backup_direct_target": "drive",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    kept = test_client.post(
        f"/servers/{server_id}/backup-config",
        data={"retention_days": "9"},
        follow_redirects=False,
    )
    assert kept.status_code == 303
    with Session(engine) as session:
        row = session.get(Server, server_id)
        assert row.backup_direct is True
        assert row.backup_direct_targets == '["drive"]'
        assert row.retention_days == 9
    again = test_client.get(f"/servers/{server_id}/backups")
    assert again.status_code == 200
    assert "straight to Google Drive" in again.text
    assert "checked" in _input_tag(again.text, "backup-direct")
    assert "checked" in _input_tag(again.text, "backup-direct-target-drive")
    assert "checked" not in _input_tag(again.text, "backup-direct-target-smb")


def test_opt_in_stays_unavailable_until_a_destination_is_saved(client):
    test_client, engine = client
    with Session(engine) as session:
        user = User(
            email="empty@test.local",
            hashed_password=get_password_hash("SmokeTest1ok!"),
            role="admin",
            is_active=True,
            must_change_password=False,
            totp_enabled=True,
        )
        server = Server(name="Pi", hostname="pi.local", backup_enabled=True, backup_direct=False)
        session.add(user)
        session.add(server)
        session.commit()
        session.refresh(user)
        session.refresh(server)
        server_id = server.id
    test_client.cookies.set("access_token", create_user_access_token(user))
    page = test_client.get(f"/servers/{server_id}/backups")
    assert page.status_code == 200
    assert "disabled" in _input_tag(page.text, "backup-direct")
    assert 'data-testid="backup-direct-none"' in page.text
    assert 'data-testid="backup-direct-target-drive"' not in page.text
    refused = test_client.post(
        f"/servers/{server_id}/backup-config",
        data={
            "scope": "this_host",
            "backup_direct_set": "1",
            "backup_direct": "1",
            "backup_direct_target": "drive",
        },
        follow_redirects=False,
    )
    assert refused.status_code == 400
    assert "Select a destination" in refused.text
    with Session(engine) as session:
        row = session.get(Server, server_id)
        assert row.backup_direct is False

"""v1.11 Path C — a host can opt in to send files straight to the copy."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import BackupDestination, Server, User
from app.security.auth import create_user_access_token, get_password_hash
from app.services import backup as backup_mod
from app.services import backup_replicate as copies
from app.services.backup_direct import (
    NO_DEST_MSG,
    SUDO_RCLONE,
    followup_destinations,
    merge_directory,
    parse_direct_targets,
    partition_checked,
    planned_syncs,
    rels_for_push,
    remote_sync_command,
    rewrite_service_account,
    run_direct_backup,
    sudo_rclone_cleanup_command,
)
from app.services.backup_replicate import enqueue_after_host_backup
from app.services.ssh_onboarding import build_sudoers_content


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


def test_sudoers_allows_the_staged_rclone():
    text = build_sudoers_content("ph", backup=True)
    assert "/usr/bin/rsync" in text
    assert SUDO_RCLONE in text
    assert "/var/lib/piherder/rclone" in text


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
    every = _host(backup_direct_targets=None)
    assert [row.provider for row in followup_destinations(Fake(), every)] == ["drive", "smb"]
    none = _host(backup_direct_targets="[]")
    assert followup_destinations(Fake(), none) == []
    assert parse_direct_targets("") is None
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


def test_copy_now_starts_the_host_push(monkeypatch):
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
        return [], [(server, checked)]

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
    result = copies.execute(dest)
    assert result["ok"] is True
    assert result["copied"] == ["pi.local/docker"]


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
    assert 'data-testid="backup-direct-all"' in page.text
    assert 'data-testid="backup-direct-target-drive"' in page.text
    assert 'data-testid="backup-direct-target-smb"' in page.text
    assert 'data-testid="backup-direct-target-onedrive"' not in page.text
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
    assert 'data-testid="backup-direct"' in again.text
    assert "checked" in again.text

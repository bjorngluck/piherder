"""Selection and rclone argv for the fleet Drive copy. No live rclone or Google."""
import json
import os
from urllib.parse import parse_qs, urlparse

import pytest

from app.models import BackupDestination
from app.services import backup_replicate as copies


def test_path_jail_rejects_parent():
    with pytest.raises(ValueError):
        copies.resolve_under_root("../etc")
    with pytest.raises(ValueError):
        copies.resolve_under_root("host/../../etc")


def test_toggle_folder_then_skip_child():
    checked, skipped = copies.apply_toggle([], [], "pi/config", True)
    assert checked == ["pi/config"]
    assert skipped == []
    checked, skipped = copies.apply_toggle(checked, skipped, "pi/config/data", False)
    assert checked == ["pi/config"]
    assert skipped == ["pi/config/data"]
    assert copies.path_included("pi/config/app", checked, skipped)
    assert not copies.path_included("pi/config/data", checked, skipped)
    assert not copies.path_included("pi/config/data/huge", checked, skipped)


def test_tick_child_of_checked_folder_clears_skip():
    checked, skipped = copies.apply_toggle([], [], "pi", True)
    checked, skipped = copies.apply_toggle(checked, skipped, "pi/data", False)
    checked, skipped = copies.apply_toggle(checked, skipped, "pi/data", True)
    assert "pi" in checked
    assert "pi/data" not in skipped
    assert copies.path_included("pi/data", checked, skipped)


def test_rclone_exclude_from_skipped_child():
    plan = copies.sync_plan(["pi"], ["pi/data"])
    assert plan == [("pi", ["data"], True)]
    cmd = copies.rclone_sync_cmd("/tmp/ph.conf", "/backups/pi", "PiHerder/pi", plan[0][1])
    assert cmd[0:2] == ["rclone", "sync"]
    assert "--drive-use-trash" in cmd
    assert "--exclude" in cmd
    assert "data/**" in cmd
    assert ".." not in " ".join(cmd)


def test_open_checked_folder_keeps_children_on(tmp_path, monkeypatch):
    monkeypatch.setattr(copies, "backup_root", lambda: tmp_path)
    (tmp_path / "host" / "keep").mkdir(parents=True)
    (tmp_path / "host" / "skipme").mkdir()
    checked, skipped = copies.apply_toggle([], [], "host", True)
    states = {
        row["name"]: copies.selection_state(row["path"], checked, skipped)
        for row in copies.list_directory("host")
    }
    assert states == {"keep": "on", "skipme": "on"}
    checked, skipped = copies.apply_toggle(checked, skipped, "host/skipme", False)
    assert copies.selection_state("host/skipme", checked, skipped) == "off"
    assert copies.selection_state("host/keep", checked, skipped) == "on"
    assert copies.selection_state("host", checked, skipped) == "partial"


def test_service_account_fields_not_a_token_blob():
    raw = copies.pack_service_account(
        "backup@example.iam.gserviceaccount.com",
        "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
    )
    path = copies.write_rclone_config(raw, shared_with_me=True)
    try:
        text = open(path, encoding="utf-8").read()
        sa = json.loads(open(path + ".sa.json", encoding="utf-8").read())
    finally:
        copies.discard_rclone_config(path)
    assert "service_account_file = " in text
    assert "token =" not in text
    assert "shared_with_me = true" in text
    assert sa["client_email"] == "backup@example.iam.gserviceaccount.com"
    assert "\n" in sa["private_key"]
    assert "\\n" not in sa["private_key"]


def test_google_auth_url_asks_for_offline_drive_access():
    url = copies.google_auth_url(
        "client.apps.googleusercontent.com",
        "https://herder.example/backup-copies/google/callback",
        "state-1",
    )
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=client.apps.googleusercontent.com" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive" in url
    assert "redirect_uri=https%3A%2F%2Fherder.example%2Fbackup-copies%2Fgoogle%2Fcallback" in url


def test_oauth_rclone_config_uses_the_user_token_not_a_shared_drive():
    raw = copies.pack_oauth_token(
        client_id="client.apps.googleusercontent.com",
        client_secret="secret",
        refresh_token="refresh",
        access_token="access",
        email="bjorn@gmail.com",
        expires_in=3600,
    )
    path = copies.write_rclone_config(raw, shared_with_me=False)
    try:
        text = open(path, encoding="utf-8").read()
    finally:
        copies.discard_rclone_config(path)
    assert "client_id = client.apps.googleusercontent.com" in text
    assert "client_secret = secret" in text
    assert "refresh" in text
    assert "shared_with_me" not in text
    assert "service_account_file" not in text


def test_literal_backslash_n_in_pasted_key_becomes_pem_newlines():
    pasted = "-----BEGIN PRIVATE KEY-----\\nMIIE\\n-----END PRIVATE KEY-----\\n"
    packed = json.loads(
        copies.pack_service_account("backup@example.iam.gserviceaccount.com", pasted)
    )
    assert packed["private_key"] == "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----"
    assert "\\n" not in packed["private_key"]


def test_service_account_config_uses_shared_drive_even_when_flag_off():
    raw = copies.pack_service_account(
        "backup@example.iam.gserviceaccount.com",
        "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
    )
    path = copies.write_rclone_config(raw, shared_with_me=True)
    try:
        text = open(path, encoding="utf-8").read()
    finally:
        os.remove(path)
    assert "shared_with_me = true" in text


def test_probe_classifies_folder_miss(monkeypatch):
    dest = BackupDestination(provider="drive", credentials_encrypted="x", config_json='{"remote_dir":"Backup"}')
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(copies, "decrypt_token", lambda _d: "{}")
    monkeypatch.setattr(copies, "write_rclone_config", lambda *_a, **_k: "/tmp/ph-rclone-test.conf")
    monkeypatch.setattr(copies, "shared_with_me", lambda _d: True)

    class Proc:
        returncode = 1
        stderr = "directory not found"
        stdout = ""

    monkeypatch.setattr(copies.subprocess, "run", lambda *a, **k: Proc())
    monkeypatch.setattr(os, "remove", lambda _p: None)
    assert copies.probe(dest)["code"] == "folder"


def test_bad_key_rejected():
    with pytest.raises(ValueError):
        copies.pack_service_account("not-an-email", "-----BEGIN PRIVATE KEY-----\n-----END PRIVATE KEY-----\n")
    with pytest.raises(ValueError):
        copies.pack_service_account("backup@example.iam.gserviceaccount.com", "nope")


def test_demo_refuses_without_rclone(monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: True)
    dest = BackupDestination(
        provider="drive",
        selection_json='{"checked":["pi"],"skipped":[]}',
        credentials_encrypted="unused",
    )
    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("rclone must not run")

    monkeypatch.setattr(copies.subprocess, "run", boom)
    result = copies.execute(dest)
    assert result["ok"] is False
    assert result["error"] == "Demo does not upload"
    assert called["n"] == 0


def test_onedrive_auth_url_uses_the_microsoft_callback():
    url = copies.microsoft_auth_url(
        "app-id",
        copies.onedrive_redirect_uri("https://herder.example"),
        "state-1",
    )
    assert url.startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize?")
    assert "client_id=app-id" in url
    assert "prompt=consent" in url
    assert "redirect_uri=https%3A%2F%2Fherder.example%2Fbackup-copies%2Fonedrive%2Fcallback" in url
    scope = parse_qs(urlparse(url).query)["scope"][0].split()
    assert scope == ["offline_access", "User.Read", "Files.ReadWrite"]


def test_onedrive_default_drive_reads_graph(monkeypatch):
    class _Body:
        def read(self):
            return b'{"id":"drive-1","driveType":"personal"}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def _open(request, timeout=20):
        assert request.full_url.endswith("/me/drive")
        assert request.headers["Authorization"] == "Bearer access"
        assert timeout == 20
        return _Body()

    monkeypatch.setattr(copies.urllib.request, "urlopen", _open)
    assert copies.onedrive_default_drive("access") == ("drive-1", "personal")


def test_onedrive_rclone_config_is_the_default_drive():
    raw = copies.pack_oauth_token(
        client_id="app-id",
        client_secret="secret-value",
        refresh_token="refresh-value",
        access_token="access",
        email="bjorn@outlook.com",
        expires_in=3600,
    )
    path = copies.write_onedrive_rclone_config(
        raw, drive_id="drive-1", drive_type="personal"
    )
    try:
        text = open(path, encoding="utf-8").read()
        mode = os.stat(path).st_mode & 0o777
    finally:
        copies.discard_rclone_config(path)
    assert "type = onedrive" in text
    assert "client_id = app-id" in text
    assert "client_secret = secret-value" in text
    assert "refresh-value" in text
    assert "drive_id = drive-1" in text
    assert "drive_type = personal" in text
    assert "drive-use-trash" not in text
    assert mode == 0o600


def test_onedrive_copy_does_not_use_drive_trash(monkeypatch, tmp_path):
    local = tmp_path / "pi"
    local.mkdir()
    monkeypatch.setattr(copies, "backup_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    dest = BackupDestination(
        provider="onedrive",
        selection_json='{"checked":["pi"],"skipped":[]}',
        credentials_encrypted="x",
        config_json='{"remote_dir":"PiHerder"}',
    )
    monkeypatch.setattr(
        copies, "onedrive_default_drive", lambda _token: ("drive-1", "personal")
    )
    monkeypatch.setattr(copies, "decrypt_token", lambda _d: copies.pack_oauth_token(
        client_id="app-id",
        client_secret="secret-value",
        refresh_token="refresh-value",
        access_token="access",
        email="bjorn@outlook.com",
        expires_in=None,
    ))
    seen = {}

    class Proc:
        returncode = 0
        stderr = ""
        stdout = ""

    def run(cmd, **_k):
        seen["cmd"] = cmd
        config = cmd[cmd.index("--config") + 1]
        seen["config"] = open(config, encoding="utf-8").read()
        return Proc()

    monkeypatch.setattr(copies.subprocess, "run", run)
    result = copies.execute(dest)
    assert result["ok"] is True
    assert "--drive-use-trash" not in seen["cmd"]
    assert "type = onedrive" in seen["config"]
    assert "drive_id = drive-1" in seen["config"]
    assert "drive_type = personal" in seen["config"]
    assert "secret-value" not in (result.get("error") or "")

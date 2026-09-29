"""Selection and rclone argv for the fleet Drive copy. No live rclone or Google."""
import os

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
    finally:
        os.remove(path)
    assert "service_account_credentials" in text
    assert "token =" not in text
    assert "shared_with_me = true" in text
    assert "backup@example.iam.gserviceaccount.com" in text


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

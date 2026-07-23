"""Pure status/error helpers from backup.py (no live rsync/SSH)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.services import backup as backup_svc


def test_rsync_error_detail_branches():
    d = backup_svc._rsync_error_detail
    assert "sudo" in d("sudo: not found", "sudo -n rsync").lower() or "HAOS" in d(
        "sudo: not found", "sudo -n rsync"
    )
    assert "rsync" in d("bash: rsync: command not found", "sudo -n rsync").lower()
    assert "Install rsync" in d("rsync: command not found", "rsync")
    assert "Permission" in d("Permission denied", "rsync")
    assert "passwordless" in d("sudo: a password is required", "sudo -n rsync").lower()
    assert "boom" in d("boom line one\nboom line two", "rsync")
    assert d("", "rsync") == "rsync non-zero"


def test_backup_source_ok_and_succeeded():
    assert backup_svc.backup_source_ok({"error": "x", "skipped": True}) is False
    assert backup_svc.backup_source_ok({"skipped": True}) is True
    assert backup_svc.backup_source_ok({"rc": 0}) is True
    assert backup_svc.backup_source_ok({"rc": 1}) is False

    assert backup_svc.backup_succeeded({"error": "x", "results": []}) is False
    assert backup_svc.backup_succeeded({"results": []}) is False
    assert backup_svc.backup_succeeded(
        {"results": [{"rc": 0}, {"rc": 0, "skipped": True}]}
    )
    assert not backup_svc.backup_succeeded({"results": [{"rc": 0}, {"rc": 1}]})


def test_backup_failure_message():
    assert "top" in backup_svc.backup_failure_message({"error": "top-level"})
    assert "/etc" in backup_svc.backup_failure_message(
        {"results": [{"source": "/etc", "error": "denied"}]}
    )
    assert "rsync exited" in backup_svc.backup_failure_message(
        {"results": [{"source": "/data", "rc": 12}]}
    )
    assert "failed" in backup_svc.backup_failure_message(
        {"results": [{"source": "/x", "rc": 0}]}
    ).lower() or backup_svc.backup_failure_message(
        {"results": [{"source": "/x", "rc": 0}]}
    )


def test_effective_backup_status():
    assert backup_svc.effective_backup_status("running", None) == "running"
    assert backup_svc.effective_backup_status("success", None) == "success"
    assert (
        backup_svc.effective_backup_status(
            "success", {"results": [{"rc": 1, "error": "x"}]}
        )
        == "failed"
    )
    assert (
        backup_svc.effective_backup_status(
            "success", json.dumps({"results": [{"rc": 0}]})
        )
        == "success"
    )
    assert backup_svc.effective_backup_status("success", "not-json") == "success"


def test_build_rsync_ssh_cmd_and_running():
    cmd = backup_svc._build_rsync_ssh_cmd("/tmp/key")
    assert "/tmp/key" in cmd and "ssh" in cmd

    assert backup_svc.is_backup_running("nope") is False
    proc = MagicMock()
    backup_svc._active_backup_procs["host-x"] = proc
    try:
        assert backup_svc.is_backup_running("host-x") is True
        with patch.object(backup_svc, "_set_progress"):
            backup_svc.stop_backup("host-x")
        proc.terminate.assert_called()
        with patch.object(backup_svc, "_set_progress"):
            proc.terminate.side_effect = RuntimeError("gone")
            backup_svc.stop_backup("host-x")  # swallow
    finally:
        backup_svc._active_backup_procs.pop("host-x", None)
    backup_svc.stop_backup("absent")


def test_global_defaults_roundtrip(tmp_path, monkeypatch):
    f = tmp_path / "defaults.json"
    monkeypatch.setattr(backup_svc, "GLOBAL_BACKUP_DEFAULTS_FILE", f)
    assert backup_svc.get_global_backup_defaults() == {}
    backup_svc.save_global_backup_defaults({"retention_days": 7})
    assert backup_svc.get_global_backup_defaults()["retention_days"] == 7
    # corrupt file
    f.write_text("not-json")
    assert backup_svc.get_global_backup_defaults() == {}


def test_remote_rsync_path_root_and_sudo():
    client = MagicMock()

    def run_root(client, cmd, timeout=10):
        if "command -v rsync" in cmd or "which rsync" in cmd:
            return 0, "/usr/bin/rsync\n", ""
        return 1, "", ""

    with patch("app.services.backup.run_command", side_effect=run_root):
        assert backup_svc._remote_rsync_path(client, "root") == "/usr/bin/rsync"

    def run_sudo(client, cmd, timeout=12):
        c = str(cmd)
        if "sudo -n" in c and "rsync --version" in c:
            return 0, "", ""
        if "command -v rsync" in c:
            return 0, "/usr/bin/rsync\n", ""
        return 1, "", ""

    with patch("app.services.backup.run_command", side_effect=run_sudo):
        path = backup_svc._remote_rsync_path(client, "pi")
    assert path.startswith("sudo -n") and "rsync" in path

    def run_plain(client, cmd, timeout=12):
        c = str(cmd)
        if "sudo" in c:
            return 1, "", "need password"
        if "command -v rsync" in c:
            return 0, "/bin/rsync\n", ""
        return 1, "", ""

    with patch("app.services.backup.run_command", side_effect=run_plain):
        assert backup_svc._remote_rsync_path(client, "pi") == "/bin/rsync"

    with patch("app.services.backup.run_command", side_effect=RuntimeError("ssh")):
        # falls through to default sudo -n rsync
        out = backup_svc._remote_rsync_path(client, "pi")
    assert "rsync" in out


def test_folder_exists_via_ssh():
    client = MagicMock()

    def run_ok(client, cmd, timeout=15):
        return 0, "ok\n", ""

    with patch("app.services.backup.run_command", side_effect=run_ok):
        assert backup_svc._folder_exists_via_ssh(client, "/data", "pi") is True

    def run_sudo_fail_plain_ok(client, cmd, timeout=15):
        if "sudo" in cmd:
            return 1, "missing\n", ""
        return 0, "ok\n", ""

    with patch("app.services.backup.run_command", side_effect=run_sudo_fail_plain_ok):
        assert backup_svc._folder_exists_via_ssh(client, "/data", "root") is True

    with patch("app.services.backup.run_command", side_effect=RuntimeError("x")):
        assert backup_svc._folder_exists_via_ssh(client, "/data", "pi") is False


def test_source_dir_exists_local(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    # sudo path may fail in container; falls back to os.path.isdir
    with patch("app.services.backup.subprocess.run", side_effect=RuntimeError("no sudo")):
        assert backup_svc._source_dir_exists_local(str(d)) is True
    assert backup_svc._source_dir_exists_local(str(tmp_path / "missing")) is False


def test_get_backup_lock():
    a = backup_svc._get_backup_lock(42)
    b = backup_svc._get_backup_lock(42)
    assert a is b


def test_send_webhook_paths(monkeypatch):
    monkeypatch.setattr(backup_svc.settings, "WEBHOOK_URL", "")
    backup_svc._send_webhook("hi")  # no-op

    monkeypatch.setattr(backup_svc.settings, "WEBHOOK_URL", "http://hook.example")
    monkeypatch.setattr(backup_svc.settings, "WEBHOOK_NUMBER", "1")
    monkeypatch.setattr(backup_svc.settings, "WEBHOOK_RECIPIENTS", '["a"]')
    with patch("app.services.backup.httpx.post") as post:
        backup_svc._send_webhook("msg")
        post.assert_called_once()
    with patch("app.services.backup.httpx.post", side_effect=RuntimeError("net")):
        backup_svc._send_webhook("msg")  # never raises

"""Unit tests for restore path policy gate and early returns (no SSH)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.services.backup_restore import restore_backup_source
from app.services.backup_path_policy import validate_backup_path


def _server(**kwargs):
    base = dict(
        backup_path_rules=None,
        hostname="host",
        ssh_port=22,
        ssh_username="pi",
        os_type="debian",
        backup_dest_root=None,
        backup_folder_name=None,
        get_backup_sources=lambda: [],
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_validate_blocks_etc():
    ok, reason = validate_backup_path("/etc/passwd", None)
    assert not ok
    assert reason


def test_restore_blocked_by_policy():
    # Policy fails before SSH
    res = restore_backup_source(_server(), "/etc/something", dry_run=True)
    assert res.get("rc") == 1
    assert "policy" in (res.get("error") or "").lower() or "blocked" in (res.get("error") or "").lower()
    assert res.get("dry_run") is True


def test_restore_empty_source():
    res = restore_backup_source(_server(), "  ", dry_run=True)
    assert res.get("rc") == 1
    assert "source" in (res.get("error") or "").lower()
    assert res.get("dry_run") is True


def test_restore_defaults_to_dry_run():
    """Keyword default is dry_run=True (safe default for the wizard)."""
    import inspect
    from app.services.backup_restore import restore_backup_source as fn

    sig = inspect.signature(fn)
    assert sig.parameters["dry_run"].default is True


def test_rclone_pull_reads_remote_into_local():
    from app.services.backup_replicate import rclone_pull_cmd

    cmd = rclone_pull_cmd("/tmp/c", "/data/host/src", "PiHerder/host/src", dry_run=True)
    assert cmd[1] == "sync"
    assert cmd[2].endswith(":PiHerder/host/src")
    assert cmd[3] == "/data/host/src"
    assert "--dry-run" in cmd
    assert "--drive-use-trash" not in cmd


def test_pull_rel_rejects_a_folder_that_is_not_selected():
    from app.services.backup_replicate import pull_rel

    dest = SimpleNamespace(
        provider="drive",
        selection_json='{"checked":["other"],"skipped":[]}',
        id=1,
        config_json="{}",
    )
    res = pull_rel(dest, "host/src", dry_run=True)
    assert res["ok"] is False
    assert "not selected" in res["error"]


def test_source_copy_rel_matches_the_copy_layout():
    from app.services.backup_restore import source_copy_rel

    assert source_copy_rel(_server(hostname="host", backup_folder_name="host"), "/var/lib/docker") == "host/docker"


def test_restore_dry_run_pull_does_not_need_a_local_tree():
    server = _server(hostname="host", backup_folder_name="host")
    pull_from = SimpleNamespace(id=3, provider="drive")
    with patch(
        "app.services.backup_restore.backup_profiles.get_backup_profiles_db",
        return_value=[{"source": "/data", "destination": "/nonexistent/dest/xyz"}],
    ):
        with patch(
            "app.services.backup_replicate.pull_rel",
            return_value={"ok": True, "place": "Google Drive · PiHerder", "dry_run": True},
        ) as pulled:
            res = restore_backup_source(server, "/data", dry_run=True, pull_from=pull_from)
    pulled.assert_called_once()
    assert pulled.call_args.kwargs["dry_run"] is True
    assert res["rc"] == 0
    assert "Dry-run pull ok" in res["summary"]


def test_restore_pull_failure_does_not_rsync():
    server = _server(hostname="host", backup_folder_name="host")
    pull_from = SimpleNamespace(id=3, provider="drive")
    with patch(
        "app.services.backup_restore.backup_profiles.get_backup_profiles_db",
        return_value=[{"source": "/data", "destination": "/nonexistent/dest/xyz"}],
    ):
        with patch(
            "app.services.backup_replicate.pull_rel",
            return_value={"ok": False, "error": "Reconnect Google Drive", "auth_failed": True},
        ):
            res = restore_backup_source(server, "/data", dry_run=False, pull_from=pull_from)
    assert res["rc"] == 1
    assert res["auth_failed"] is True
    assert "Reconnect" in res["error"]


def test_restore_apply_pulls_then_rsyncs(tmp_path):
    dest_dir = tmp_path / "host" / "docker"
    dest_dir.mkdir(parents=True)
    server = _server(hostname="host", backup_folder_name="host")
    pull_from = SimpleNamespace(id=4, provider="drive")
    with patch(
        "app.services.backup_restore.backup_profiles.get_backup_profiles_db",
        return_value=[{"source": "/data", "destination": str(dest_dir)}],
    ):
        with patch(
            "app.services.backup_replicate.pull_rel",
            return_value={"ok": True, "place": "Google Drive · PiHerder", "dry_run": False},
        ) as pulled:
            with patch("app.services.backup_restore.get_private_key_plain", return_value="KEY"):
                with patch("app.services.backup_restore.temp_key_file") as key:
                    key.return_value.__enter__.return_value = str(tmp_path / "k")
                    with patch(
                        "app.services.backup_restore.subprocess.run",
                        return_value=SimpleNamespace(returncode=0, stdout="ok", stderr=""),
                    ):
                        res = restore_backup_source(
                            server, "/data", dry_run=False, pull_from=pull_from
                        )
    assert pulled.call_args.kwargs["dry_run"] is False
    assert pulled.call_args.kwargs["local_dir"] == dest_dir
    assert res["rc"] == 0
    assert "Google Drive" in res["summary"]
    assert res["destination_id"] == 4
    assert res["ownership"]["skipped"] is True
    assert res["ownership"]["reason"] == "no manifest"


def test_restore_applies_ownership_and_keeps_old_archives_working(tmp_path):
    from app.services.backup_ownership import write_manifest

    dest_dir = tmp_path / "tree"
    dest_dir.mkdir()
    write_manifest(
        dest_dir,
        [{"path": "data/app.db", "uid": 1000, "gid": 1000, "mode": "4755"}],
    )
    server = _server(hostname="host-a.example.com", backup_folder_name="host-a")
    calls = []

    def _run(cmd, input=None, **kwargs):  # noqa: A002
        calls.append((cmd, input))
        return SimpleNamespace(returncode=0, stdout="applied 1\n", stderr="")

    with patch(
        "app.services.backup_restore.backup_profiles.get_backup_profiles_db",
        return_value=[{"source": "/data/app", "destination": str(dest_dir)}],
    ):
        with patch("app.services.backup_restore.get_private_key_plain", return_value="KEY"):
            with patch("app.services.backup_restore.temp_key_file") as key:
                key.return_value.__enter__.return_value = str(tmp_path / "k")
                with patch("app.services.backup_restore.subprocess.run", side_effect=_run):
                    res = restore_backup_source(server, "/data/app", dry_run=False)
    assert res["rc"] == 0
    assert res["ownership"]["applied"] is True
    assert calls[0][0][0] == "rsync"
    assert ".piherder-ownership.json" in calls[0][0]
    assert calls[1][0][0] == "ssh"
    assert calls[1][0][-1] == "sh -s -- /data/app"
    assert "0755" in calls[1][1]
    assert "4755" not in calls[1][1]
    assert "sudo -n /usr/bin/chown" in calls[1][1]
    assert "sudo -n /usr/bin/test -e" in calls[1][1]


def test_restore_missing_local_dest():
    server = _server()
    with patch(
        "app.services.backup_restore.backup_profiles.get_backup_profiles_db",
        return_value=[{"source": "/home/pi/docker", "destination": "/nonexistent/dest/xyz"}],
    ):
        with patch(
            "app.services.backup_restore.backup_profiles.get_backup_root_for_server",
        ) as _:
            res = restore_backup_source(server, "/home/pi/docker", dry_run=True)
    assert res.get("rc") == 1
    assert "no local backup" in (res.get("error") or "").lower()
    assert res.get("dry_run") is True
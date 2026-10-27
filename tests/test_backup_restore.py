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
                        side_effect=_snapshot_proc,
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
        return _snapshot_proc(cmd, input=input)

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
    assert res["rollback"]["saved"] is True
    assert calls[0][0][0] == "rsync"
    assert "--delete" in calls[0][0]
    assert calls[0][0][-2].endswith(":/data/app/")
    assert calls[1][0][0] == "ssh"
    assert "find " in calls[1][0][-1]
    restore = next(call for call in calls if call[0][0] == "rsync" and "--delete" not in call[0])
    assert ".piherder-ownership.json" in restore[0]
    own = next(call for call in calls if call[0][0] == "ssh" and str(call[0][-1]).startswith("sh -s"))
    assert own[0][-1] == "sh -s -- /data/app"
    assert "0755" in own[1]
    assert "4755" not in own[1]
    assert "sudo -n /usr/bin/chown" in own[1]
    assert "sudo -n /usr/bin/test -e" in own[1]


def _snapshot_proc(cmd, input=None, **kwargs):  # noqa: A002
    if cmd and cmd[0] == "ssh" and "find " in str(cmd[-1]):
        return SimpleNamespace(returncode=0, stdout=b"\x001000\x001000\x00755\x00", stderr=b"")
    if cmd and cmd[0] == "ssh":
        return SimpleNamespace(returncode=0, stdout="applied 1\n", stderr="")
    return SimpleNamespace(returncode=0, stdout="ok", stderr="")


def test_dry_run_does_not_take_a_rollback_snapshot(tmp_path):
    dest_dir = tmp_path / "docker"
    dest_dir.mkdir()
    server = _server(hostname="host-a.example.com", backup_folder_name="host-a")
    calls = []

    def _run(cmd, input=None, **kwargs):  # noqa: A002
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="preview", stderr="")

    with patch(
        "app.services.backup_restore.backup_profiles.get_backup_profiles_db",
        return_value=[{"source": "/data/app", "destination": str(dest_dir)}],
    ):
        with patch("app.services.backup_restore.get_private_key_plain", return_value="KEY"):
            with patch("app.services.backup_restore.temp_key_file") as key:
                key.return_value.__enter__.return_value = str(tmp_path / "k")
                with patch("app.services.backup_restore.subprocess.run", side_effect=_run):
                    res = restore_backup_source(server, "/data/app", dry_run=True)
    assert res["rc"] == 0
    assert res["rollback"]["reason"] == "dry-run"
    assert len(calls) == 1
    assert "--dry-run" in calls[0]
    assert not (dest_dir.parent / "_rollback").exists()


def test_snapshot_failure_does_not_start_the_restore(tmp_path):
    dest_dir = tmp_path / "docker"
    dest_dir.mkdir()
    server = _server(hostname="host-a.example.com", backup_folder_name="host-a")
    calls = []

    def _run(cmd, input=None, **kwargs):  # noqa: A002
        calls.append(cmd)
        return SimpleNamespace(returncode=1, stdout="", stderr="denied")

    with patch(
        "app.services.backup_restore.backup_profiles.get_backup_profiles_db",
        return_value=[{"source": "/data/app", "destination": str(dest_dir)}],
    ):
        with patch("app.services.backup_restore.get_private_key_plain", return_value="KEY"):
            with patch("app.services.backup_restore.temp_key_file") as key:
                key.return_value.__enter__.return_value = str(tmp_path / "k")
                with patch("app.services.backup_restore.subprocess.run", side_effect=_run):
                    res = restore_backup_source(server, "/data/app", dry_run=False)
    assert res["rc"] == 1
    assert res["error"] == "Rollback snapshot failed"
    assert len(calls) == 1
    assert calls[0][0] == "rsync"
    assert not (dest_dir.parent / "_rollback" / "docker" / "meta.json").exists()


def test_undo_writes_the_snapshot_back(tmp_path):
    from app.services.backup_ownership import write_manifest
    from app.services.backup_restore import snapshot_root_for, undo_host_restore

    dest_dir = tmp_path / "docker"
    dest_dir.mkdir()
    root = snapshot_root_for(dest_dir)
    tree = root / "tree"
    tree.mkdir(parents=True)
    write_manifest(tree, [{"path": "", "uid": 1000, "gid": 1000, "mode": "0755"}])
    (root / "meta.json").write_text('{"source":"/data/app"}', encoding="utf-8")
    server = _server(hostname="host-a.example.com", backup_folder_name="host-a")
    calls = []

    def _run(cmd, input=None, **kwargs):  # noqa: A002
        calls.append((cmd, input))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    with patch(
        "app.services.backup_restore.backup_profiles.get_backup_profiles_db",
        return_value=[{"source": "/data/app", "destination": str(dest_dir)}],
    ):
        with patch("app.services.backup_restore.get_private_key_plain", return_value="KEY"):
            with patch("app.services.backup_restore.temp_key_file") as key:
                key.return_value.__enter__.return_value = str(tmp_path / "k")
                with patch("app.services.backup_restore.subprocess.run", side_effect=_run):
                    res = undo_host_restore(server, "/data/app")
    assert res["rc"] == 0
    assert res["summary"].startswith("Undo ok")
    assert len(calls) == 2
    assert calls[0][0][0] == "rsync"
    assert calls[0][0][-2].rstrip("/").endswith("/tree")
    assert calls[0][0][-1].endswith(":/data/app/")
    assert "find " not in str(calls[1][0][-1])
    assert calls[1][0][-1] == "sh -s -- /data/app"


def _manifest_with_counts(dest):
    from app.services.backup_ownership import write_manifest

    write_manifest(
        dest,
        [
            {"path": "", "uid": 1000, "gid": 1000, "mode": "0755", "kind": "dir"},
            {"path": "app.db", "uid": 1000, "gid": 1000, "mode": "0640", "kind": "file", "bytes": 4},
        ],
    )


def test_restore_is_good_when_the_host_matches_the_manifest(tmp_path):
    dest_dir = tmp_path / "tree"
    dest_dir.mkdir()
    _manifest_with_counts(dest_dir)
    server = _server(hostname="host-a.example.com", backup_folder_name="host-a")
    calls = []

    def _run(cmd, input=None, **kwargs):  # noqa: A002
        calls.append(cmd)
        if cmd and cmd[0] == "ssh" and "%s" in str(cmd[-1]):
            return SimpleNamespace(returncode=0, stdout=b"4\x00", stderr=b"")
        return _snapshot_proc(cmd, input=input)

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
    assert res["error"] is None
    assert res["manifest"]["ok"] is True
    assert res["manifest"]["file_count"] == 1
    assert res["manifest"]["byte_count"] == 4
    assert "Restore ok" in res["summary"]
    size_calls = [cmd for cmd in calls if cmd and cmd[0] == "ssh" and "%s" in str(cmd[-1])]
    assert len(size_calls) == 1
    assert "sudo -n find " in size_calls[0][-1]


def test_restore_is_not_good_when_the_host_counts_differ(tmp_path):
    dest_dir = tmp_path / "tree"
    dest_dir.mkdir()
    _manifest_with_counts(dest_dir)
    server = _server(hostname="host-a.example.com", backup_folder_name="host-a")

    def _run(cmd, input=None, **kwargs):  # noqa: A002
        if cmd and cmd[0] == "ssh" and "%s" in str(cmd[-1]):
            return SimpleNamespace(returncode=0, stdout=b"4\x004\x00", stderr=b"")
        return _snapshot_proc(cmd, input=input)

    with patch(
        "app.services.backup_restore.backup_profiles.get_backup_profiles_db",
        return_value=[{"source": "/data/app", "destination": str(dest_dir)}],
    ):
        with patch("app.services.backup_restore.get_private_key_plain", return_value="KEY"):
            with patch("app.services.backup_restore.temp_key_file") as key:
                key.return_value.__enter__.return_value = str(tmp_path / "k")
                with patch("app.services.backup_restore.subprocess.run", side_effect=_run):
                    res = restore_backup_source(server, "/data/app", dry_run=False)
    assert res["rc"] == 1
    assert res["manifest"]["ok"] is False
    assert "2 files, 8 bytes" in res["error"]
    assert "1 file, 4 bytes" in res["error"]
    assert res["summary"].startswith("Restore failed")
    assert "Restore ok" not in res["summary"]
    assert res["rollback"]["saved"] is True


def test_restore_is_not_good_when_the_host_count_cannot_be_read(tmp_path):
    dest_dir = tmp_path / "tree"
    dest_dir.mkdir()
    _manifest_with_counts(dest_dir)
    server = _server(hostname="host-a.example.com", backup_folder_name="host-a")

    def _run(cmd, input=None, **kwargs):  # noqa: A002
        if cmd and cmd[0] == "ssh" and "%s" in str(cmd[-1]):
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"denied")
        return _snapshot_proc(cmd, input=input)

    with patch(
        "app.services.backup_restore.backup_profiles.get_backup_profiles_db",
        return_value=[{"source": "/data/app", "destination": str(dest_dir)}],
    ):
        with patch("app.services.backup_restore.get_private_key_plain", return_value="KEY"):
            with patch("app.services.backup_restore.temp_key_file") as key:
                key.return_value.__enter__.return_value = str(tmp_path / "k")
                with patch("app.services.backup_restore.subprocess.run", side_effect=_run):
                    res = restore_backup_source(server, "/data/app", dry_run=False)
    assert res["rc"] == 1
    assert "Manifest check failed" in res["error"]
    assert res["summary"].startswith("Restore failed")


def test_dry_run_does_not_tally_the_host(tmp_path):
    dest_dir = tmp_path / "tree"
    dest_dir.mkdir()
    _manifest_with_counts(dest_dir)
    server = _server(hostname="host-a.example.com", backup_folder_name="host-a")
    calls = []

    def _run(cmd, input=None, **kwargs):  # noqa: A002
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="preview", stderr="")

    with patch(
        "app.services.backup_restore.backup_profiles.get_backup_profiles_db",
        return_value=[{"source": "/data/app", "destination": str(dest_dir)}],
    ):
        with patch("app.services.backup_restore.get_private_key_plain", return_value="KEY"):
            with patch("app.services.backup_restore.temp_key_file") as key:
                key.return_value.__enter__.return_value = str(tmp_path / "k")
                with patch("app.services.backup_restore.subprocess.run", side_effect=_run):
                    res = restore_backup_source(server, "/data/app", dry_run=True)
    assert res["rc"] == 0
    assert res["manifest"]["reason"] == "dry-run"
    assert len(calls) == 1
    assert calls[0][0] == "rsync"
    assert "--dry-run" in calls[0]


def test_parse_rsync_changes_keeps_creates_and_updates():
    from app.services.backup_restore import parse_rsync_changes

    text = "\n".join(
        [
            "<f+++++++++ new.db",
            "<f.st...... app.db",
            ".d..t...... data",
            "*deleting   gone",
            "Number of files: 4",
        ]
    )
    parsed = parse_rsync_changes(text)
    assert parsed["changes"] == [
        {"path": "new.db", "action": "create"},
        {"path": "app.db", "action": "update"},
        {"path": "gone", "action": "delete"},
    ]
    assert parsed["truncated"] is False
    many = "\n".join(f"<f+++++++++ f{i}" for i in range(201))
    capped = parse_rsync_changes(many)
    assert capped["change_count"] == 201
    assert len(capped["changes"]) == 200
    assert capped["truncated"] is True


def test_preview_lists_changes_and_does_not_write(tmp_path):
    import json

    from app.services.backup_restore import preview_restore

    dest_dir = tmp_path / "tree"
    dest_dir.mkdir()
    _manifest_with_counts(dest_dir)
    server = _server(hostname="host-a.example.com", backup_folder_name="host-a")
    calls = []

    def _run(cmd, input=None, **kwargs):  # noqa: A002
        calls.append(cmd)
        return SimpleNamespace(
            returncode=0,
            stdout="<f+++++++++ new.db\n<f.st...... app.db\n.d..t...... data\n",
            stderr="",
        )

    with patch(
        "app.services.backup_restore.backup_profiles.get_backup_profiles_db",
        return_value=[{"source": "/data/app", "destination": str(dest_dir)}],
    ):
        with patch("app.services.backup_restore.get_private_key_plain", return_value="KEY"):
            with patch("app.services.backup_restore.temp_key_file") as key:
                key.return_value.__enter__.return_value = str(tmp_path / "k")
                with patch("app.services.backup_restore.subprocess.run", side_effect=_run):
                    res = preview_restore(server, "/data/app")
    assert res["wrote"] is False
    assert res["dry_run"] is True
    assert res["ok"] is True
    assert "remote" not in res
    assert "output" not in res
    assert res["changes"] == [
        {"path": "new.db", "action": "create"},
        {"path": "app.db", "action": "update"},
    ]
    assert len(calls) == 1
    assert calls[0][0] == "rsync"
    assert "--dry-run" in calls[0]
    assert "--itemize-changes" in calls[0]
    assert "--delete" not in calls[0]
    assert not (dest_dir.parent / "_rollback").exists()
    assert "host-a.example.com" not in json.dumps(res)


def test_itemize_cannot_turn_into_a_real_restore(tmp_path):
    from app.services.backup_restore import restore_backup_source

    dest_dir = tmp_path / "tree"
    dest_dir.mkdir()
    server = _server(hostname="host-a.example.com", backup_folder_name="host-a")
    calls = []

    def _run(cmd, input=None, **kwargs):  # noqa: A002
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch(
        "app.services.backup_restore.backup_profiles.get_backup_profiles_db",
        return_value=[{"source": "/data/app", "destination": str(dest_dir)}],
    ):
        with patch("app.services.backup_restore.get_private_key_plain", return_value="KEY"):
            with patch("app.services.backup_restore.temp_key_file") as key:
                key.return_value.__enter__.return_value = str(tmp_path / "k")
                with patch("app.services.backup_restore.subprocess.run", side_effect=_run):
                    res = restore_backup_source(server, "/data/app", dry_run=False, itemize=True)
    assert res["dry_run"] is True
    assert res["wrote"] is False
    assert "--dry-run" in calls[0]
    assert not (dest_dir.parent / "_rollback").exists()


def test_preview_hides_the_local_backup_path(tmp_path):
    from app.services.backup_restore import preview_restore

    server = _server(hostname="host-a.example.com", backup_folder_name="host-a")
    secret = tmp_path / "secret-tree"
    with patch(
        "app.services.backup_restore.backup_profiles.get_backup_profiles_db",
        return_value=[{"source": "/data/app", "destination": str(secret)}],
    ):
        res = preview_restore(server, "/data/app")
    assert res["error"] == "No local backup for this source"
    assert str(secret) not in res["error"]


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
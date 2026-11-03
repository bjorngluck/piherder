"""Restore preview diffs the chosen copy and lists permission and owner changes."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.services.backup_ownership import MANIFEST_NAME, write_manifest
from app.services.backup_restore import parse_rsync_changes, preview_restore


def _server():
    return SimpleNamespace(
        backup_path_rules=None,
        hostname="host-a.example.com",
        ssh_port=22,
        ssh_username="pi",
        os_type="debian",
        backup_dest_root=None,
        backup_folder_name="host-a",
        get_backup_sources=lambda: [],
    )


def test_parse_lists_permission_and_owner_only_lines():
    text = "\n".join(
        [
            "<f+++++++++ new.db",
            ".f...p..... app.db",
            ".f....o.... owner.db",
            ".f...pog... both.db",
            ".d..t...... data",
        ]
    )
    parsed = parse_rsync_changes(text)
    assert parsed["changes"] == [
        {"path": "new.db", "action": "create"},
        {"path": "app.db", "action": "permission"},
        {"path": "owner.db", "action": "owner"},
        {"path": "both.db", "action": "owner"},
    ]


def test_preview_diffs_the_chosen_copy_and_removes_the_temp_dir(tmp_path):
    local = tmp_path / "local-backup"
    local.mkdir()
    (local / "local-only.txt").write_text("stay")
    seen = {}

    def _pull(destination, rel, *, dry_run, local_dir):
        seen["dry_run"] = dry_run
        seen["rel"] = rel
        seen["staged"] = str(local_dir)
        write_manifest(
            local_dir,
            [{"path": "app.db", "uid": 1000, "gid": 1000, "mode": "644"}],
        )
        (local_dir / "from-copy.txt").write_text("copy")
        return {"ok": True, "place": "Google Drive · example", "dry_run": False}

    def _run(cmd, **kwargs):
        seen["cmd"] = cmd
        return SimpleNamespace(
            returncode=0,
            stdout=".f...p..... app.db\n",
            stderr="",
        )

    with patch(
        "app.services.backup_restore.backup_profiles.get_backup_profiles_db",
        return_value=[{"source": "/data/app", "destination": str(local)}],
    ):
        with patch("app.services.backup_replicate.pull_rel", side_effect=_pull):
            with patch("app.services.backup_restore.get_private_key_plain", return_value="KEY"):
                with patch("app.services.backup_restore.temp_key_file") as key:
                    key.return_value.__enter__.return_value = str(tmp_path / "k")
                    with patch("app.services.backup_restore.rsync_e_command", return_value="ssh"):
                        with patch("app.services.backup_restore.subprocess.run", side_effect=_run):
                            result = preview_restore(
                                _server(),
                                "/data/app",
                                pull_from=SimpleNamespace(id=7, provider="drive"),
                            )
    assert seen["dry_run"] is False
    assert seen["rel"] == "host-a/app"
    assert seen["cmd"][-2].startswith(seen["staged"])
    assert str(local) not in seen["cmd"][-2]
    assert not __import__("pathlib").Path(seen["staged"]).exists()
    assert (local / "local-only.txt").is_file()
    assert result["wrote"] is False
    assert result["dry_run"] is True
    assert result["destination_id"] == 7
    assert result["ownership_step"] is True
    assert result["changes"] == [{"path": "app.db", "action": "permission"}]
    assert MANIFEST_NAME not in str(result.get("note") or "")


def test_preview_copy_failure_does_not_rsync(tmp_path):
    def _run(*args, **kwargs):
        raise AssertionError("rsync should not run")

    with patch(
        "app.services.backup_replicate.pull_rel",
        return_value={"ok": False, "error": "Reconnect Google Drive", "auth_failed": True},
    ):
        with patch("app.services.backup_restore.subprocess.run", side_effect=_run):
            result = preview_restore(
                _server(),
                "/data/app",
                pull_from=SimpleNamespace(id=7, provider="drive"),
            )
    assert result["rc"] == 1
    assert result["wrote"] is False
    assert result["auth_failed"] is True
    assert result["changes"] == []

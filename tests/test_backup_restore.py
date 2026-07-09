"""Unit tests for restore path policy gate (no SSH)."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.backup_restore import restore_backup_source
from app.services.backup_path_policy import validate_backup_path


def test_validate_blocks_etc():
    ok, reason = validate_backup_path("/etc/passwd", None)
    assert not ok
    assert reason


def test_restore_blocked_by_policy():
    server = SimpleNamespace(
        backup_path_rules=None,
        hostname="host",
        ssh_port=22,
        ssh_username="pi",
        os_type="debian",
        backup_dest_root=None,
        backup_folder_name=None,
        get_backup_sources=lambda: [],
    )
    # Monkey: restore_backup_source needs Server-like; policy fails before SSH
    res = restore_backup_source(server, "/etc/something", dry_run=True)
    assert res.get("rc") == 1
    assert "policy" in (res.get("error") or "").lower() or "blocked" in (res.get("error") or "").lower()

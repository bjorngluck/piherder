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
"""Selection and rclone argv for the fleet Drive copy. No live rclone or Google."""
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

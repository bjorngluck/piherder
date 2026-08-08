"""v1.2 B-retry — vanished-file rsync classification and soft-OK."""
from __future__ import annotations

from app.services import backup as backup_svc


def test_is_vanished_code_24():
    assert backup_svc._is_vanished_rsync_failure(24, "")
    assert backup_svc._is_vanished_rsync_failure(24, "file has vanished")


def test_is_vanished_code_23_only_with_vanished_text():
    assert backup_svc._is_vanished_rsync_failure(23, "file has vanished: /media/x.mp4")
    assert not backup_svc._is_vanished_rsync_failure(23, "permission denied")
    assert not backup_svc._is_vanished_rsync_failure(23, "")


def test_is_vanished_not_other_codes():
    assert not backup_svc._is_vanished_rsync_failure(1, "vanished")
    assert not backup_svc._is_vanished_rsync_failure(0, "")
    assert not backup_svc._is_vanished_rsync_failure(12, "io error")


def test_backup_source_ok_soft_vanished():
    assert backup_svc.backup_source_ok(
        {"source": "/data", "rc": 24, "vanished_soft_ok": True, "warning": "busy"}
    )
    assert not backup_svc.backup_source_ok(
        {"source": "/data", "rc": 24, "error": "rsync failed"}
    )
    assert backup_svc.backup_source_ok({"source": "/data", "rc": 0})


def test_backup_succeeded_with_soft_ok_among_results():
    payload = {
        "results": [
            {"source": "/a", "rc": 0},
            {"source": "/nvr", "rc": 24, "vanished_soft_ok": True},
        ]
    }
    assert backup_svc.backup_succeeded(payload) is True


def test_backup_still_fails_hard_error_with_soft_ok_sibling():
    payload = {
        "results": [
            {"source": "/nvr", "rc": 24, "vanished_soft_ok": True},
            {"source": "/secret", "rc": 23, "error": "permission denied"},
        ]
    }
    assert backup_svc.backup_succeeded(payload) is False

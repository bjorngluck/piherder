"""Unit tests for stack health aggregation (mocked components)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.stack_health import (
    _fmt_bytes,
    _overall,
    _tree_usage_component,
    celery_pool_slots_from_report,
    celery_worker_count_from_report,
    check_celery,
    check_disks,
    collect_backup_tree_usage,
)


def test_overall_fail_beats_warn():
    comps = [
        {"status": "ok"},
        {"status": "warn"},
        {"status": "fail"},
    ]
    assert _overall(comps) == "fail"


def test_overall_warn_without_fail():
    comps = [{"status": "ok"}, {"status": "warn"}]
    assert _overall(comps) == "warn"


def test_overall_ok():
    assert _overall([{"status": "ok"}, {"status": "ok"}]) == "ok"


def test_celery_worker_count_from_report():
    report = {
        "components": [
            {"id": "db", "status": "ok"},
            {"id": "celery", "status": "ok", "detail": {"workers": 2, "names": ["a", "b"]}},
        ]
    }
    assert celery_worker_count_from_report(report) == 2
    assert celery_worker_count_from_report(None) == 0
    assert celery_worker_count_from_report({}) == 0


def test_celery_pool_slots_from_report():
    report = {
        "components": [
            {
                "id": "celery",
                "detail": {"workers": 1, "pool_slots": 4},
            }
        ]
    }
    assert celery_pool_slots_from_report(report) == 4
    assert celery_pool_slots_from_report(None) == 0
    # Older report without pool_slots → fall back to nodes
    assert celery_pool_slots_from_report(
        {"components": [{"id": "celery", "detail": {"workers": 2}}]}
    ) == 2


def test_check_celery_reports_pool_slots():
    inspector = MagicMock()
    inspector.ping.return_value = {"celery@host": {"ok": "pong"}}
    inspector.stats.return_value = {
        "celery@host": {
            "pool": {"max-concurrency": 2, "processes": [1, 2]},
        }
    }
    mock_celery = MagicMock()
    mock_celery.control.inspect.return_value = inspector

    # check_celery does: from ..celery_app import celery
    with patch("app.celery_app.celery", mock_celery):
        comp = check_celery()

    assert comp["status"] == "ok"
    assert comp["detail"]["workers"] == 1
    assert comp["detail"]["pool_slots"] == 2
    assert "pool slot" in (comp.get("message") or "").lower()


def test_check_disks_skips_tree_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "app.services.stack_health.settings.BACKUP_ROOT", str(tmp_path / "b")
    )
    monkeypatch.setattr(
        "app.services.stack_health.settings.DATA_ROOT", str(tmp_path / "d")
    )
    monkeypatch.setattr(
        "app.services.stack_health.settings.HERDER_BACKUP_ROOT", str(tmp_path / "h")
    )
    for name in ("b", "d", "h"):
        (tmp_path / name).mkdir()
    comps = check_disks()
    ids = [c["id"] for c in comps]
    assert all(not i.startswith("disk_used_") for i in ids)
    assert any(i.startswith("disk_mount_") for i in ids)


def test_collect_backup_tree_usage(tmp_path: Path, monkeypatch):
    root = tmp_path / "backups"
    host = root / "pi1"
    host.mkdir(parents=True)
    (host / "f.bin").write_bytes(b"x" * 500)
    monkeypatch.setattr("app.services.stack_health.settings.BACKUP_ROOT", str(root))
    data = collect_backup_tree_usage(limit=5)
    assert data["ok"] is True
    assert data["tree_bytes"] is not None and data["tree_bytes"] >= 500
    names = [c["name"] for c in data.get("children") or []]
    assert "pi1" in names


def test_fmt_bytes():
    assert _fmt_bytes(0) == "0 B"
    assert "KB" in _fmt_bytes(2048) or "MB" in _fmt_bytes(2048) or "B" in _fmt_bytes(2048)


def test_tree_usage_on_tmp(tmp_path: Path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"x" * 1000)
    comp = _tree_usage_component("disk_used_test", "Storage used · test", tmp_path)
    assert comp["status"] == "ok"
    assert comp["detail"].get("tree_bytes", 0) >= 1000
    assert "used under" in (comp.get("message") or "")

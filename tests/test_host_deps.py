"""Unit tests for host dependency overall scoring (no live SSH)."""
from __future__ import annotations

from app.services.host_deps import overall_from_checks


def test_overall_ok_when_all_ok():
    checks = [
        {"id": "ssh", "status": "ok", "required": True},
        {"id": "rsync", "status": "ok", "required": True},
        {"id": "docker", "status": "skip", "required": False},
    ]
    assert overall_from_checks(checks) == "ok"


def test_overall_fail_on_required_fail():
    checks = [
        {"id": "ssh", "status": "ok", "required": True},
        {"id": "rsync", "status": "fail", "required": True},
    ]
    assert overall_from_checks(checks) == "fail"


def test_overall_warn_on_required_warn():
    checks = [
        {"id": "ssh", "status": "ok", "required": True},
        {"id": "rsync_path", "status": "warn", "required": True},
    ]
    assert overall_from_checks(checks) == "warn"


def test_optional_fail_is_warn_not_fail():
    checks = [
        {"id": "ssh", "status": "ok", "required": True},
        {"id": "extra", "status": "fail", "required": False},
    ]
    assert overall_from_checks(checks) == "warn"


def test_skips_do_not_affect_overall():
    checks = [
        {"id": "ssh", "status": "ok", "required": True},
        {"id": "docker", "status": "skip", "required": False},
        {"id": "apt", "status": "skip", "required": False},
    ]
    assert overall_from_checks(checks) == "ok"

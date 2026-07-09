"""Unit tests for patch-apply schedule skip decisions (no APScheduler / DB)."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.scheduler import (
    container_apply_skip_reason,
    os_apply_skip_reason,
    _cron_trigger,
)


def _server(**kwargs):
    defaults = dict(
        os_patch_enabled=True,
        os_apply_enabled=True,
        os_apply_only_if_updates=True,
        os_updates_count=3,
        container_patch_enabled=True,
        container_apply_enabled=True,
        container_apply_only_if_updates=True,
        container_updates_count=2,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_os_apply_runs_when_enabled_and_updates():
    assert os_apply_skip_reason(_server()) is None


def test_os_apply_skip_disabled_feature():
    assert os_apply_skip_reason(_server(os_patch_enabled=False)) == "disabled"
    assert os_apply_skip_reason(_server(os_apply_enabled=False)) == "disabled"


def test_os_apply_skip_no_updates():
    assert os_apply_skip_reason(_server(os_updates_count=0)) == "no_updates"
    assert os_apply_skip_reason(_server(os_updates_count=None)) is None  # unknown → allow
    # only_if_updates off → run even with zero
    assert (
        os_apply_skip_reason(
            _server(os_updates_count=0, os_apply_only_if_updates=False)
        )
        is None
    )


def test_container_apply_skip_paths():
    assert container_apply_skip_reason(_server()) is None
    assert (
        container_apply_skip_reason(_server(container_patch_enabled=False)) == "disabled"
    )
    assert (
        container_apply_skip_reason(_server(container_apply_enabled=False)) == "disabled"
    )
    assert (
        container_apply_skip_reason(_server(container_updates_count=0)) == "no_updates"
    )


def test_cron_trigger_valid():
    t = _cron_trigger("0 2 * * *")
    assert t is not None


def test_cron_trigger_invalid_fields():
    try:
        _cron_trigger("0 2 *")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "5" in str(e)

"""Unit tests for fleet server delete + host cleanup script (no DB/SSH)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.server_lifecycle import (
    ServerDeleteError,
    delete_server_from_fleet,
)
from app.services.ssh_onboarding import build_piherder_user_cleanup_script
from app.services.scheduler import server_cron_job_ids, unregister_server_cron_jobs


def test_server_cron_job_ids():
    ids = server_cron_job_ids(7)
    assert "backup_7" in ids
    assert "os_check_7" in ids
    assert "container_apply_7" in ids
    assert len(ids) == 5


def test_unregister_server_cron_jobs():
    sched = MagicMock()
    unregister_server_cron_jobs(sched, True, 3)
    assert sched.remove_job.call_count == 5
    unregister_server_cron_jobs(None, True, 3)  # no-op


def test_cleanup_script_defaults():
    script = build_piherder_user_cleanup_script("piherder")
    assert "#!/bin/bash" in script
    assert "piherder" in script
    assert "sudoers.d/piherder-" in script
    assert "Does not stop/remove Docker" in script or "does NOT do" in script.lower() or "Does not stop" in script
    assert "REMOVE_USER" in script
    assert "userdel" in script or "deluser" in script


def test_cleanup_script_refuses_root_username_fallback():
    # root is remapped to piherder for safety
    script = build_piherder_user_cleanup_script("root")
    assert 'USER_NAME="${USER_NAME:-piherder}"' in script or "piherder" in script
    assert "refusing to clean protected" in script


def test_delete_requires_exact_name():
    server = SimpleNamespace(
        id=1,
        name="RPI5-2",
        hostname="rpi5-2.example",
        ssh_username="piherder",
        ssh_port=22,
    )
    session = MagicMock()
    with pytest.raises(ServerDeleteError) as ei:
        delete_server_from_fleet(session, server, confirm_name="rpi5-2", user_id=1)
    assert ei.value.code == "confirm_name"


def test_delete_server_from_fleet_happy_path():
    server = SimpleNamespace(
        id=42,
        name="RPI5-2",
        hostname="rpi5-2.example",
        ssh_username="piherder",
        ssh_port=22,
    )

    # Chain of session.exec(...).all() for cancel, docker versions, jobs, audits, notes
    empty = MagicMock()
    empty.all.return_value = []
    session = MagicMock()
    session.exec.return_value = empty

    with (
        patch("app.services.server_lifecycle._cancel_active_jobs", return_value=1),
        patch("app.services.server_lifecycle._unregister_schedules") as unreg,
    ):
        snap = delete_server_from_fleet(
            session, server, confirm_name="RPI5-2", user_id=9
        )

    assert snap["former_server_id"] == 42
    assert snap["name"] == "RPI5-2"
    assert snap["host_left_intact"] is True
    assert snap["jobs_cancelled"] == 1
    unreg.assert_called_once_with(42)
    session.delete.assert_called_once_with(server)
    session.commit.assert_called()
    # Audit row added
    assert session.add.called

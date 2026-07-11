"""Unit tests for compose redeploy (pull + up -d)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services import docker_management as dm


def test_redeploy_empty_path():
    r = dm.redeploy_project(MagicMock(), "", pull=True)
    assert r["success"] is False
    assert "empty" in (r.get("error") or "")


def test_redeploy_pull_and_up_success():
    server = MagicMock()
    client = MagicMock()
    calls = []

    def run_cmd(c, cmd, timeout=120):
        calls.append(cmd)
        if "compose pull" in cmd:
            return 0, "web Pulled\n", ""
        if "compose up -d" in cmd:
            return 0, "Container recreated\n", ""
        return 1, "", "unexpected"

    with patch.object(dm, "get_ssh_client", return_value=client):
        with patch.object(dm, "run_command", side_effect=run_cmd):
            r = dm.redeploy_project(server, "/home/pi/docker/app", pull=True)

    assert r["success"] is True
    assert r["pull_ok"] is True
    assert r["up_ok"] is True
    assert r["pull_status"] == 0
    assert r["up_status"] == 0
    assert "compose pull" in calls[0]
    assert "compose up -d" in calls[1]
    assert "/home/pi/docker/app" in calls[0]
    assert "=== docker compose pull" in r["output"]
    client.close.assert_called()


def test_redeploy_pull_fail_marked():
    server = MagicMock()
    client = MagicMock()

    def run_cmd(c, cmd, timeout=120):
        if "compose pull" in cmd:
            return 1, "Error response from daemon: unauthorized", ""
        return 0, "up ok", ""

    with patch.object(dm, "get_ssh_client", return_value=client):
        with patch.object(dm, "run_command", side_effect=run_cmd):
            r = dm.redeploy_project(server, "/opt/stack", pull=True)

    assert r["success"] is False
    assert r["pull_ok"] is False
    assert r["up_ok"] is True
    assert r["error"] == "pull failed"


def test_redeploy_up_only_no_pull():
    server = MagicMock()
    client = MagicMock()
    calls = []

    def run_cmd(c, cmd, timeout=120):
        calls.append(cmd)
        return 0, "Started", ""

    with patch.object(dm, "get_ssh_client", return_value=client):
        with patch.object(dm, "run_command", side_effect=run_cmd):
            r = dm.redeploy_project(server, "/opt/stack", pull=False)

    assert r["success"] is True
    assert len(calls) == 1
    assert "pull" not in calls[0]
    assert "up -d" in calls[0]

"""Docker project bulk lifecycle: compose stop/start/restart as Jobs (H2.75 P1)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import docker_management as dm
from app.services import jobs as job_service


def test_compose_action_rejects_bad_action():
    r = dm.compose_action(MagicMock(), "/opt/stack", "explode")
    assert r["success"] is False
    assert "bad" in (r.get("error") or "").lower() or r.get("error") == "bad action"


def test_compose_action_empty_path():
    r = dm.compose_action(MagicMock(), "", "stop")
    assert r["success"] is False
    assert "empty" in (r.get("error") or "")


def test_compose_action_stop_all_quotes_path():
    server = MagicMock()
    client = MagicMock()
    with (
        patch.object(dm, "get_ssh_client", return_value=client),
        patch.object(dm, "run_command", return_value=(0, "Stopping\n", "")) as run,
    ):
        r = dm.compose_action(server, "/home/pi/my stack", "stop")
    assert r["success"] is True
    assert r["action"] == "stop"
    assert r["service"] is None
    cmd = run.call_args[0][1]
    assert "docker compose stop" in cmd
    assert "cd " in cmd
    # path is shell-quoted (spaces safe)
    assert "my stack" in cmd or "my\\ stack" in cmd or "'/home/pi/my stack'" in cmd


def test_compose_action_restart_one_service():
    server = MagicMock()
    client = MagicMock()
    with (
        patch.object(dm, "get_ssh_client", return_value=client),
        patch.object(dm, "run_command", return_value=(0, "ok", "")) as run,
    ):
        r = dm.compose_action(server, "/opt/app", "restart", service="web")
    assert r["success"] is True
    assert r["service"] == "web"
    assert "restart" in run.call_args[0][1]
    assert "web" in run.call_args[0][1]


def test_compose_action_failure_returns_error():
    server = MagicMock()
    client = MagicMock()
    with (
        patch.object(dm, "get_ssh_client", return_value=client),
        patch.object(dm, "run_command", return_value=(1, "no such project", "")),
    ):
        r = dm.compose_action(server, "/opt/missing", "start")
    assert r["success"] is False
    assert r.get("error")


def _active_job(job_type="docker_stack_stop", job_id=55, status="running"):
    return SimpleNamespace(
        id=job_id,
        server_id=1,
        job_type=job_type,
        status=status,
        details='{"project_path":"/opt/foo"}',
        created_at=None,
        started_at=None,
        finished_at=None,
    )


def test_enqueue_lifecycle_raises_when_mutating_active():
    active = _active_job("docker_stack_deploy", 33, status="running")
    mock_session = MagicMock()
    mock_session.get.return_value = SimpleNamespace(id=5, name="pi")
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    with (
        patch.object(job_service, "_get_fresh_session", return_value=mock_session),
        patch.object(job_service, "_active_docker_stack_job", return_value=None),
        patch.object(job_service, "_active_stack_mutating_job", return_value=active),
        patch.object(job_service, "_update_check_pool") as pool,
    ):
        with pytest.raises(job_service.JobAlreadyActive) as ei:
            job_service.enqueue_docker_stack_lifecycle(
                5, "/opt/stacks/foo", "restart", user_id=1
            )
    assert ei.value.job.id == 33
    pool.submit.assert_not_called()


def test_compose_action_down_v():
    server = MagicMock()
    client = MagicMock()
    with (
        patch.object(dm, "get_ssh_client", return_value=client),
        patch.object(dm, "run_command", return_value=(0, "Removing\n", "")) as run,
    ):
        r = dm.compose_action(
            server, "/home/pi/docker/app", "down", remove_volumes=True
        )
    assert r["success"] is True
    cmd = run.call_args[0][1]
    assert "docker compose down -v" in cmd
    assert "cd " in cmd


def test_compose_action_down_keeps_volumes_by_default():
    server = MagicMock()
    client = MagicMock()
    with (
        patch.object(dm, "get_ssh_client", return_value=client),
        patch.object(dm, "run_command", return_value=(0, "ok", "")) as run,
    ):
        r = dm.compose_action(server, "/opt/app", "down")
    assert r["success"] is True
    cmd = run.call_args[0][1]
    assert "docker compose down" in cmd
    assert " -v" not in cmd


def test_enqueue_lifecycle_invalid_action():
    with pytest.raises(ValueError):
        job_service.enqueue_docker_stack_lifecycle(1, "/opt/x", "explode", user_id=1)


def test_enqueue_lifecycle_requires_path():
    with pytest.raises(ValueError):
        job_service.enqueue_docker_stack_lifecycle(1, "  ", "stop", user_id=1)


def test_enqueue_lifecycle_creates_job():
    mock_session = MagicMock()
    mock_session.get.return_value = SimpleNamespace(id=5, name="pi")
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    fake_job = SimpleNamespace(
        id=77, server_id=5, job_type="docker_stack_stop", status="pending", details="{}"
    )
    fake_audit = SimpleNamespace(id=88)

    with (
        patch.object(job_service, "_get_fresh_session", return_value=mock_session),
        patch.object(job_service, "_active_docker_stack_job", return_value=None),
        patch.object(job_service, "_active_stack_mutating_job", return_value=None),
        patch.object(
            job_service,
            "_create_queued_job_with_audit",
            return_value=(fake_job, fake_audit),
        ) as create,
        patch.object(job_service, "_update_check_pool") as pool,
    ):
        mock_session.get.side_effect = [
            SimpleNamespace(id=5, name="pi"),  # first with
            fake_job,  # return job after enqueue
        ]
        job = job_service.enqueue_docker_stack_lifecycle(
            5, "/opt/stacks/foo", "stop", user_id=9
        )
    assert job.id == 77
    create.assert_called_once()
    kwargs = create.call_args.kwargs
    assert kwargs["job_type"] == "docker_stack_stop"
    assert kwargs["project_path"] == "/opt/stacks/foo"
    pool.submit.assert_called_once()


def test_human_summary_lifecycle():
    snip = '{"project":"kuma","action":"restart","success":true}'
    s = job_service._human_job_summary("docker_stack_restart", "success", snip)
    assert "kuma" in s
    assert "restart" in s.lower()
    assert "ok" in s.lower()


def test_stack_mutating_includes_lifecycle():
    assert "docker_stack_stop" in job_service._STACK_MUTATING_JOB_TYPES
    assert "docker_stack_start" in job_service._STACK_MUTATING_JOB_TYPES
    assert "docker_stack_restart" in job_service._STACK_MUTATING_JOB_TYPES
    assert "docker_stack_down" in job_service._STACK_MUTATING_JOB_TYPES
    assert "docker_stack_remove" in job_service._STACK_MUTATING_JOB_TYPES
    assert "docker_stack_stop" in job_service._EXCLUSIVE_JOB_TYPES
    assert "docker_stack_down" in job_service._EXCLUSIVE_JOB_TYPES
    assert "docker_stack_remove" in job_service._EXCLUSIVE_JOB_TYPES


def test_enqueue_lifecycle_down_stores_remove_volumes():
    mock_session = MagicMock()
    mock_session.get.return_value = SimpleNamespace(id=5, name="pi")
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    fake_job = SimpleNamespace(
        id=81, server_id=5, job_type="docker_stack_down", status="pending", details="{}"
    )
    fake_audit = SimpleNamespace(id=88)

    with (
        patch.object(job_service, "_get_fresh_session", return_value=mock_session),
        patch.object(job_service, "_active_docker_stack_job", return_value=None),
        patch.object(job_service, "_active_stack_mutating_job", return_value=None),
        patch.object(
            job_service,
            "_create_queued_job_with_audit",
            return_value=(fake_job, fake_audit),
        ) as create,
        patch.object(job_service, "_update_check_pool") as pool,
    ):
        mock_session.get.side_effect = [
            SimpleNamespace(id=5, name="pi"),
            fake_job,
        ]
        job = job_service.enqueue_docker_stack_lifecycle(
            5, "/home/pi/docker/openwebui", "down", user_id=9, remove_volumes=True
        )
    assert job.id == 81
    kwargs = create.call_args.kwargs
    assert kwargs["job_type"] == "docker_stack_down"
    assert kwargs["remove_volumes"] is True
    assert kwargs["action"] == "down -v"
    pool.submit.assert_called_once()


def test_human_summary_down_and_remove():
    s = job_service._human_job_summary(
        "docker_stack_down",
        "success",
        '{"project":"openwebui","action":"down -v","success":true}',
    )
    assert "openwebui" in s
    assert "ok" in s.lower()
    s = job_service._human_job_summary(
        "docker_stack_remove",
        "success",
        '{"project":"openwebui","success":true}',
    )
    assert "deleted" in s.lower()
    s = job_service._human_job_summary(
        "docker_stack_remove",
        "failed",
        '{"project":"openwebui","error":"locked"}',
    )
    assert "delete failed" in s.lower()


def test_execute_lifecycle_success_invalidates_inventory():
    server = SimpleNamespace(id=3, name="pi", hostname="pi.local")
    result = {"success": True, "output": "Container stopped", "error": None}
    with (
        patch.object(job_service, "_load_server_for_job", return_value=(server, "pi")),
        patch.object(job_service, "_get_fresh_session") as gs,
        patch.object(job_service, "_flush_job_progress"),
        patch.object(job_service, "_append_output_log_lines"),
        patch.object(job_service, "_finish") as finish,
        patch(
            "app.services.docker_management.compose_action", return_value=result
        ) as ca,
        patch(
            "app.services.docker_inventory.invalidate_after_mutation"
        ) as inv,
    ):
        sess = MagicMock()
        job_row = SimpleNamespace(status="pending", started_at=None, details="{}")
        sess.get.side_effect = [job_row, server]
        sess.__enter__ = MagicMock(return_value=sess)
        sess.__exit__ = MagicMock(return_value=False)
        gs.return_value = sess

        job_service._execute_docker_stack_lifecycle(
            1, 3, 2, "/opt/kuma", "stop"
        )

    ca.assert_called_once()
    inv.assert_called_once()
    finish.assert_called_once()
    assert finish.call_args[0][2] == "success"
    assert finish.call_args[0][5] == "docker_stack_stop"


def test_execute_lifecycle_down_passes_remove_volumes():
    server = SimpleNamespace(id=3, name="pi", hostname="pi.local")
    result = {"success": True, "output": "Removed", "error": None}
    with (
        patch.object(job_service, "_load_server_for_job", return_value=(server, "pi")),
        patch.object(job_service, "_get_fresh_session") as gs,
        patch.object(job_service, "_flush_job_progress"),
        patch.object(job_service, "_append_output_log_lines"),
        patch.object(job_service, "_finish") as finish,
        patch(
            "app.services.docker_management.compose_action", return_value=result
        ) as ca,
        patch("app.services.docker_inventory.invalidate_after_mutation"),
    ):
        sess = MagicMock()
        job_row = SimpleNamespace(
            status="pending", started_at=None, details='{"remove_volumes": true}'
        )
        sess.get.side_effect = [job_row, server]
        sess.__enter__ = MagicMock(return_value=sess)
        sess.__exit__ = MagicMock(return_value=False)
        gs.return_value = sess

        job_service._execute_docker_stack_lifecycle(
            1, 3, 2, "/home/pi/docker/app", "down"
        )

    assert ca.call_args.kwargs.get("remove_volumes") is True
    finish.assert_called_once()
    assert finish.call_args[0][2] == "success"
    assert finish.call_args[0][5] == "docker_stack_down"


def test_enqueue_remove_requires_path():
    with pytest.raises(ValueError):
        job_service.enqueue_docker_stack_remove(1, "  ", user_id=1)


def test_enqueue_remove_creates_job():
    mock_session = MagicMock()
    srv = SimpleNamespace(id=5, name="pi", docker_base_dir="/home/pi/docker")
    mock_session.get.return_value = srv
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    fake_job = SimpleNamespace(
        id=90, server_id=5, job_type="docker_stack_remove", status="pending", details="{}"
    )
    fake_audit = SimpleNamespace(id=91)

    with (
        patch.object(job_service, "_get_fresh_session", return_value=mock_session),
        patch.object(job_service, "_active_docker_stack_job", return_value=None),
        patch.object(job_service, "_active_stack_mutating_job", return_value=None),
        patch(
            "app.services.service_migrate.leftover.assert_wipe_allowed",
            return_value=("app", "/home/pi/docker/app"),
        ),
        patch.object(
            job_service,
            "_create_queued_job_with_audit",
            return_value=(fake_job, fake_audit),
        ) as create,
        patch.object(job_service, "_update_check_pool") as pool,
    ):
        mock_session.get.side_effect = [srv, fake_job]
        job = job_service.enqueue_docker_stack_remove(
            5, "/home/pi/docker/app", user_id=9
        )
    assert job.id == 90
    assert create.call_args.kwargs["job_type"] == "docker_stack_remove"
    pool.submit.assert_called_once()

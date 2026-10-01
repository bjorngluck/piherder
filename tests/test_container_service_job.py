"""One compose service start/stop. Not a whole-project lifecycle job. On hosted MCP."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import jobs as job_service
from app.services import mcp_hosted
from app.services.api_tokens import JOB_FEATURE_KEY
from app.services.jobs_exclusive import EXCLUSIVE_CELERY_TYPES


def test_container_service_is_docker_and_on_mcp():
    assert JOB_FEATURE_KEY["container_start"] == "docker"
    assert JOB_FEATURE_KEY["container_stop"] == "docker"
    assert "container_start" in job_service._STACK_MUTATING_JOB_TYPES
    assert "container_stop" in EXCLUSIVE_CELERY_TYPES
    assert "container_restart" in EXCLUSIVE_CELERY_TYPES
    assert "container_redeploy" in EXCLUSIVE_CELERY_TYPES
    assert "container_start" not in job_service._STACK_LIFECYCLE_JOB_TYPES
    for name in mcp_hosted.MCP_CONTAINER_SERVICE_JOB_TYPES:
        assert name in mcp_hosted.MCP_JOB_TYPES
        assert JOB_FEATURE_KEY[name] == "docker"
    assert "docker_stack_down" not in mcp_hosted.MCP_JOB_TYPES
    assert "docker_stack_remove" not in mcp_hosted.MCP_JOB_TYPES
    assert "service_migrate" not in mcp_hosted.MCP_JOB_TYPES
    assert "service_migrate_undo" not in mcp_hosted.MCP_JOB_TYPES
    assert "nmap_discovery" not in mcp_hosted.MCP_JOB_TYPES


def test_container_service_target_requires_path_and_service():
    assert job_service.container_service_target("/opt/web", "web") == ("/opt/web", "web")
    with pytest.raises(ValueError):
        job_service.container_service_target("/opt/web", "  ")
    with pytest.raises(ValueError):
        job_service.container_service_target("", "web")


def test_execute_container_stop_passes_the_service():
    server = SimpleNamespace(id=3, name="pi", hostname="pi.local")
    result = {"success": True, "output": "Stopped", "error": None}
    with (
        patch.object(job_service, "_load_server_for_job", return_value=(server, "pi")),
        patch.object(job_service, "_get_fresh_session") as gs,
        patch.object(job_service, "_flush_job_progress"),
        patch.object(job_service, "_append_output_log_lines"),
        patch.object(job_service, "_finish") as finish,
        patch("app.services.docker_management.compose_action", return_value=result) as ca,
        patch("app.services.docker_inventory.invalidate_after_mutation"),
    ):
        sess = MagicMock()
        job_row = SimpleNamespace(status="pending", started_at=None, details="{}")
        sess.get.side_effect = [job_row, server]
        sess.__enter__ = MagicMock(return_value=sess)
        sess.__exit__ = MagicMock(return_value=False)
        gs.return_value = sess

        job_service._execute_container_service(1, 3, 2, "/opt/web", "web", "stop")

    ca.assert_called_once_with(server, "/opt/web", "stop", service="web")
    assert finish.call_args[0][2] == "success"
    assert finish.call_args[0][5] == "container_stop"


def test_execute_container_restart_passes_the_service():
    server = SimpleNamespace(id=3, name="pi", hostname="pi.local")
    result = {"success": True, "output": "Restarted", "error": None}
    with (
        patch.object(job_service, "_load_server_for_job", return_value=(server, "pi")),
        patch.object(job_service, "_get_fresh_session") as gs,
        patch.object(job_service, "_flush_job_progress"),
        patch.object(job_service, "_append_output_log_lines"),
        patch.object(job_service, "_finish") as finish,
        patch("app.services.docker_management.compose_action", return_value=result) as ca,
        patch("app.services.docker_inventory.invalidate_after_mutation"),
    ):
        sess = MagicMock()
        job_row = SimpleNamespace(status="pending", started_at=None, details="{}")
        sess.get.side_effect = [job_row, server]
        sess.__enter__ = MagicMock(return_value=sess)
        sess.__exit__ = MagicMock(return_value=False)
        gs.return_value = sess

        job_service._execute_container_service(1, 3, 2, "/opt/web", "web", "restart")

    ca.assert_called_once_with(server, "/opt/web", "restart", service="web")
    assert finish.call_args[0][5] == "container_restart"


def test_execute_redeploy_is_one_service():
    server = SimpleNamespace(id=3, name="pi", hostname="pi.local")
    result = {"success": True, "output": "Recreated", "error": None}
    with (
        patch.object(job_service, "_load_server_for_job", return_value=(server, "pi")),
        patch.object(job_service, "_get_fresh_session") as gs,
        patch.object(job_service, "_flush_job_progress"),
        patch.object(job_service, "_append_output_log_lines"),
        patch.object(job_service, "_finish") as finish,
        patch("app.services.docker_management.compose_action") as ca,
        patch("app.services.docker_management.compose_service_redeploy", return_value=result) as redeploy,
        patch("app.services.docker_inventory.invalidate_after_mutation"),
    ):
        sess = MagicMock()
        job_row = SimpleNamespace(status="pending", started_at=None, details="{}")
        sess.get.side_effect = [job_row, server]
        sess.__enter__ = MagicMock(return_value=sess)
        sess.__exit__ = MagicMock(return_value=False)
        gs.return_value = sess

        job_service._execute_container_service(1, 3, 2, "/opt/web", "web", "redeploy")

    ca.assert_not_called()
    redeploy.assert_called_once_with(server, "/opt/web", "web")
    assert finish.call_args[0][5] == "container_redeploy"


def test_celery_dispatch_includes_restart_and_redeploy(monkeypatch):
    from app.services.jobs_exclusive import _execute

    seen = []
    monkeypatch.setattr(job_service, "_execute_container_service", lambda *args: seen.append(args))
    _execute(
        "container_restart",
        9,
        3,
        4,
        {"project_path": "/opt/web", "service": "web", "action": "restart"},
    )
    _execute(
        "container_redeploy",
        9,
        3,
        4,
        {"project_path": "/opt/web", "service": "db", "action": "redeploy"},
    )
    assert seen[0][-1] == "restart"
    assert seen[1][-1] == "redeploy"
    assert seen[1][-2] == "db"


def test_human_summary_names_the_service():
    text = job_service._human_job_summary(
        "container_start",
        "success",
        '{"service":"web","action":"start","success":true}',
    )
    assert "web" in text
    assert "start ok" in text

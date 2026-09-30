"""One compose service start/stop. Not a whole-project lifecycle job. Not MCP."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import jobs as job_service
from app.services import mcp_hosted
from app.services.api_tokens import JOB_FEATURE_KEY
from app.services.jobs_exclusive import EXCLUSIVE_CELERY_TYPES


def test_container_service_is_docker_and_not_mcp():
    assert JOB_FEATURE_KEY["container_start"] == "docker"
    assert JOB_FEATURE_KEY["container_stop"] == "docker"
    assert "container_start" in job_service._STACK_MUTATING_JOB_TYPES
    assert "container_stop" in EXCLUSIVE_CELERY_TYPES
    assert "container_start" not in job_service._STACK_LIFECYCLE_JOB_TYPES
    assert "container_start" not in mcp_hosted.MCP_JOB_TYPES
    assert "container_stop" not in mcp_hosted.MCP_JOB_TYPES


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


def test_human_summary_names_the_service():
    text = job_service._human_job_summary(
        "container_start",
        "success",
        '{"service":"web","action":"start","success":true}',
    )
    assert "web" in text
    assert "start ok" in text

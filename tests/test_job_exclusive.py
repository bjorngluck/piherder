"""Exclusive per-server job types: no second concurrent OS/container job."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import jobs as job_service


def _active_job(job_type="container_patch", job_id=99, status="running"):
    return SimpleNamespace(
        id=job_id,
        server_id=1,
        job_type=job_type,
        status=status,
        details="{}",
        created_at=None,
        started_at=None,
        finished_at=None,
    )


def test_create_job_and_run_rejects_duplicate_container_patch():
    server = SimpleNamespace(id=1, name="pi", hostname="pi.local")
    session = MagicMock()
    bg = MagicMock()
    active = _active_job("container_patch", 42)

    with patch.object(job_service, "_active_job_of_type", return_value=active):
        with pytest.raises(job_service.JobAlreadyActive) as ei:
            job_service.create_job_and_run(bg, session, server, "container_patch", user_id=1)
    assert ei.value.job.id == 42
    session.add.assert_not_called()


def test_create_job_and_run_rejects_duplicate_os_patch():
    server = SimpleNamespace(id=1, name="pi", hostname="pi.local")
    session = MagicMock()
    bg = MagicMock()
    active = _active_job("os_patch", 7)

    with patch.object(job_service, "_active_job_of_type", return_value=active):
        with pytest.raises(job_service.JobAlreadyActive) as ei:
            job_service.create_job_and_run(bg, session, server, "os_patch", user_id=1)
    assert ei.value.job.id == 7


def test_enqueue_container_update_check_returns_existing():
    server = SimpleNamespace(id=5, name="pi")
    active = _active_job("container_update_check", 11, status="pending")
    mock_session = MagicMock()
    mock_session.get.return_value = server
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    with (
        patch.object(job_service, "_get_fresh_session", return_value=mock_session),
        patch.object(job_service, "_active_job_of_type", return_value=active),
        patch.object(job_service, "_update_check_pool") as pool,
    ):
        job = job_service.enqueue_container_update_check(5, user_id=1)
    assert job is active
    pool.submit.assert_not_called()


def test_enqueue_docker_stack_check_raises_when_active():
    active = _active_job("docker_stack_check", 21, status="running")
    mock_session = MagicMock()
    mock_session.get.return_value = SimpleNamespace(id=5, name="pi")
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    with (
        patch.object(job_service, "_get_fresh_session", return_value=mock_session),
        patch.object(job_service, "_active_docker_stack_job", return_value=active),
        patch.object(job_service, "_update_check_pool") as pool,
    ):
        with pytest.raises(job_service.JobAlreadyActive) as ei:
            job_service.enqueue_docker_stack_check(5, "/opt/stacks/foo", user_id=1)
    assert ei.value.job.id == 21
    pool.submit.assert_not_called()


def test_human_summary_docker_stack_check():
    snip = '{"project":"kuma","has_updates":true,"updated_images":["a:latest"],"success":true}'
    s = job_service._human_job_summary("docker_stack_check", "success", snip)
    assert "kuma" in s
    assert "updates" in s.lower()


def test_human_summary_docker_stack_deploy():
    snip = '{"project":"npm","success":true,"project_path":"/opt/npm"}'
    s = job_service._human_job_summary("docker_stack_deploy", "success", snip)
    assert "npm" in s
    assert "ok" in s.lower()


def test_enqueue_os_update_check_returns_existing():
    server = SimpleNamespace(id=5, name="pi")
    active = _active_job("os_update_check", 12, status="running")
    mock_session = MagicMock()
    mock_session.get.return_value = server
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    with (
        patch.object(job_service, "_get_fresh_session", return_value=mock_session),
        patch.object(job_service, "_active_job_of_type", return_value=active),
        patch.object(job_service, "_update_check_pool") as pool,
    ):
        job = job_service.enqueue_os_update_check(5, user_id=1)
    assert job is active
    pool.submit.assert_not_called()


def test_exclusive_types_do_not_include_backup():
    assert "backup" not in job_service._EXCLUSIVE_JOB_TYPES
    assert "container_patch" in job_service._EXCLUSIVE_JOB_TYPES
    assert "os_patch" in job_service._EXCLUSIVE_JOB_TYPES
    assert "template_deploy" in job_service._EXCLUSIVE_JOB_TYPES
    assert "template_redeploy" in job_service._EXCLUSIVE_JOB_TYPES
    assert "template_deploy" in job_service._STACK_MUTATING_JOB_TYPES


def test_human_summary_template_deploy():
    snip = '{"project_name":"kuma","template_slug":"uptime-kuma","success":true,"config_version":2}'
    s = job_service._human_job_summary("template_deploy", "success", snip)
    assert "kuma" in s
    assert "ok" in s.lower()


def test_enqueue_template_deploy_raises_when_stack_mutating():
    active = _active_job("docker_stack_deploy", 33, status="running")
    mock_session = MagicMock()
    mock_session.get.return_value = SimpleNamespace(id=5, name="pi")
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    with (
        patch.object(job_service, "_get_fresh_session", return_value=mock_session),
        patch.object(job_service, "_active_stack_mutating_job", return_value=active),
        patch.object(job_service, "_update_check_pool") as pool,
    ):
        with pytest.raises(job_service.JobAlreadyActive) as ei:
            job_service.enqueue_template_deploy(
                5,
                template_slug="uptime-kuma",
                values={"PROJECT_NAME": "kuma"},
                user_id=1,
            )
    assert ei.value.job.id == 33
    pool.submit.assert_not_called()


def test_enqueue_template_redeploy_raises_when_active():
    active = _active_job("template_redeploy", 44, status="pending")
    mock_session = MagicMock()
    mock_session.get.return_value = SimpleNamespace(id=5, name="pi")
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    with (
        patch.object(job_service, "_get_fresh_session", return_value=mock_session),
        patch.object(job_service, "_active_stack_mutating_job", return_value=active),
        patch.object(job_service, "_update_check_pool") as pool,
    ):
        with pytest.raises(job_service.JobAlreadyActive) as ei:
            job_service.enqueue_template_redeploy(
                5, deployment_id=9, user_id=1
            )
    assert ei.value.job.id == 44
    pool.submit.assert_not_called()

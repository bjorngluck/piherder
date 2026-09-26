"""Jr-1: exclusive host jobs on the default Celery queue.

OS patch, container patch, host reboot, update checks, compose stack jobs, and
template jobs leave the web process. nmap stays on ``-Q nmap``. ``retention`` and
``herder_backup`` stay on web. Backup, Move, and undo keep their own tasks
and are not given this dispatcher.

A single-host job does **not** take Move's dual-host backup mutex. The DB
exclusive lane (one active type, shared stack-mutation lane) is unchanged.

Host SSH down: the row stays **pending** and the task retries until a probe
succeeds or the wait limit elapses. A redelivery that finds the job already
**running** fails it. Pending waits redeliver.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

# Types moved off BackgroundTasks / in-process pools. host_facts stays on web.
EXCLUSIVE_CELERY_TYPES = frozenset(
    {
        "os_patch",
        "container_patch",
        "host_reboot",
        "os_update_check",
        "container_update_check",
        "docker_stack_check",
        "docker_stack_deploy",
        "docker_stack_stop",
        "docker_stack_start",
        "docker_stack_restart",
        "docker_stack_down",
        "docker_stack_remove",
        "template_deploy",
        "template_redeploy",
        "template_drift_check",
    }
)

# Default max SSH wait. Settings → General → Jobs stores exclusive_host_wait_sec.
# PIHERDER_EXCLUSIVE_HOST_WAIT_SEC locks that field when set.
DEFAULT_HOST_WAIT_SEC = 1800
HOST_WAIT_COUNTDOWN_SEC = 30
_HOST_WAIT_FLOOR_SEC = 30
_HOST_WAIT_CEILING_SEC = 24 * 60 * 60


def exclusive_runs_inline() -> bool:
    """Unit tests have no broker. Production always enqueues ``exclusive_job``.

    ``PIHERDER_EXCLUSIVE_INLINE=1`` is a local debug hatch only.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    flag = (os.environ.get("PIHERDER_EXCLUSIVE_INLINE") or "").strip().lower()
    return flag in ("1", "true", "yes")


# UI signal only. A Kuma SSH monitor down, or Server.last_seen older than this,
# may label a pending job "waiting on host". It does not resume or fail the job.
LAST_SEEN_SIGNAL_SEC = 15 * 60


def host_wait_env_locked() -> bool:
    return bool((os.environ.get("PIHERDER_EXCLUSIVE_HOST_WAIT_SEC") or "").strip())


def clamp_host_wait_sec(raw) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = DEFAULT_HOST_WAIT_SEC
    return max(_HOST_WAIT_FLOOR_SEC, min(n, _HOST_WAIT_CEILING_SEC))


def host_wait_limit_sec() -> int:
    """Seconds to wait for SSH. Env locks the value. Otherwise read Settings fresh.

    The worker must not use the web process cache: an admin save has to apply
    on the next probe without recycling celery-worker.
    """
    if host_wait_env_locked():
        return clamp_host_wait_sec(os.environ.get("PIHERDER_EXCLUSIVE_HOST_WAIT_SEC"))
    try:
        from .app_settings import DEFAULTS, _load_raw_from_db

        raw = _load_raw_from_db() or {}
        stored = raw.get("exclusive_host_wait_sec", DEFAULTS["exclusive_host_wait_sec"])
        return clamp_host_wait_sec(stored)
    except Exception:
        logger.debug("host wait setting unreadable; using default", exc_info=True)
        return DEFAULT_HOST_WAIT_SEC


def host_down_signal(session, server) -> str | None:
    """``kuma`` or ``last_seen`` when the host looks down. Never a resume signal."""
    if server is None or not getattr(server, "id", None):
        return None
    from sqlmodel import select

    from ..models import IntegrationBinding, Notification
    from .integrations.registry import ROLE_SSH

    binding = session.exec(
        select(IntegrationBinding)
        .where(IntegrationBinding.server_id == int(server.id))
        .where(IntegrationBinding.role == ROLE_SSH)
        .where(IntegrationBinding.last_state == "down")
    ).first()
    if binding is not None:
        return "kuma"
    note = session.exec(
        select(Notification)
        .where(Notification.server_id == int(server.id))
        .where(Notification.type == "host_down")
        .where(Notification.status == "open")
    ).first()
    if note is not None:
        return "kuma"
    seen = getattr(server, "last_seen", None)
    if seen is not None:
        age = (datetime.utcnow() - seen).total_seconds()
        if age >= LAST_SEEN_SIGNAL_SEC:
            return "last_seen"
    return None


def host_wait_display(job, details: dict | None) -> tuple[str | None, str | None]:
    """Label for JobHold / Jobs. Does not write the job row.

    SSH already waiting wins. Otherwise Kuma or a stale ``last_seen`` may say
    "waiting on host" while the row stays pending. The worker still resumes
    only when the SSH probe succeeds.
    """
    if (getattr(job, "status", None) or "") != "pending":
        return None, None
    if (getattr(job, "job_type", None) or "") not in EXCLUSIVE_CELERY_TYPES:
        return None, None
    if (details or {}).get("current") == "waiting_for_host":
        return "waiting on host", "ssh"
    sid = getattr(job, "server_id", None)
    if not sid:
        return None, None
    js = _jobs()
    try:
        with js._get_fresh_session() as session:
            server = session.get(js.Server, int(sid))
            kind = host_down_signal(session, server)
    except Exception:
        logger.debug("host-down signal skipped", exc_info=True)
        return None, None
    if not kind:
        return None, None
    return "waiting on host", kind


def _jobs():
    from . import jobs as js

    return js


def _load_details(job) -> dict:
    try:
        data = json.loads(job.details or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def handoff_exclusive(
    *,
    job_id: int,
    server_id: int,
    audit_id: int,
    job_type: str,
    payload: dict | None = None,
    background_tasks=None,
    bg_fn=None,
    bg_args: tuple = (),
    pool=None,
    sync_fn=None,
    sync_args: tuple = (),
) -> None:
    """Send one exclusive job to Celery. Under pytest, keep the old in-process path.

    Demo mode (production) finishes the row as a simulation and does not SSH.
    """
    body = dict(payload or {})
    js = _jobs()
    if not exclusive_runs_inline():
        from .demo import demo_mode

        if demo_mode():
            with js._get_fresh_session() as session:
                job = session.get(js.Job, job_id)
                if job and job.status == "pending":
                    js._finish_demo_job(session, job)
            return
        _enqueue_celery(job_id, server_id, audit_id, job_type, body)
        return
    if background_tasks is not None and bg_fn is not None:
        background_tasks.add_task(bg_fn, *bg_args)
        return
    if pool is not None and sync_fn is not None:
        pool.submit(sync_fn, *sync_args)
        return
    raise RuntimeError(f"No in-process runner for {job_type}")


def _enqueue_celery(
    job_id: int,
    server_id: int,
    audit_id: int,
    job_type: str,
    payload: dict,
) -> None:
    js = _jobs()
    exclusive_job = getattr(js, "exclusive_job_task", None)
    if not getattr(js, "HAS_CELERY", False) or exclusive_job is None:
        msg = "Celery worker required — start the celery-worker container"
        _fail_enqueue(job_id, msg)
        raise RuntimeError(msg)
    try:
        async_result = exclusive_job.delay(
            job_id, server_id, audit_id, job_type, payload
        )
    except Exception as exc:
        msg = f"Failed to enqueue {job_type} to Celery: {exc}"
        logger.exception("[Jobs] %s", msg)
        _fail_enqueue(job_id, msg)
        raise RuntimeError(msg) from exc
    with js._get_fresh_session() as session:
        job = session.get(js.Job, job_id)
        if job:
            job.celery_task_id = async_result.id
            session.add(job)
            session.commit()
    logger.info(
        "[Jobs] Enqueued %s job #%s for server %s on the default Celery queue",
        job_type,
        job_id,
        server_id,
    )


def _fail_enqueue(job_id: int, message: str) -> None:
    js = _jobs()
    with js._get_fresh_session() as session:
        job = session.get(js.Job, job_id)
        if not job or job.status not in ("pending", "running"):
            return
        js._mark_job_terminal(job, message, session, status="failed", record_audit=False)
        js._finish_running_audit_for_job(session, job, status="failed", message=message)
        session.commit()


def run_exclusive_job(task, job_id: int, server_id: int, audit_id: int, job_type: str, payload: dict | None):
    """Celery body. No backup mutex. SSH probe before work. Running redelivery fails."""
    from celery.exceptions import MaxRetriesExceededError, Retry

    js = _jobs()
    body = dict(payload or {})
    try:
        with js._get_fresh_session() as session:
            job = session.get(js.Job, job_id)
            if not job:
                return {"status": "skipped", "job_id": job_id}
            jt = job.job_type or job_type
            if jt not in EXCLUSIVE_CELERY_TYPES:
                return {"status": "skipped", "job_id": job_id, "reason": jt}
            if job.status == "cancelled":
                return {"status": "cancelled", "job_id": job_id}
            if job.status not in ("pending", "running"):
                return {"status": "skipped", "job_id": job_id, "reason": job.status}
            task_id = getattr(getattr(task, "request", None), "id", None)
            if task_id and not (job.celery_task_id or "").strip():
                job.celery_task_id = str(task_id)
                session.add(job)
                session.commit()
            if job.status == "running":
                jt_running = jt
            else:
                jt_running = None
        if jt_running:
            _fail_running_redelivery(job_id, audit_id, jt_running)
            return {"status": "failed", "job_id": job_id, "reason": "worker_restart"}

        from .demo import demo_mode

        if demo_mode():
            with js._get_fresh_session() as session:
                job = session.get(js.Job, job_id)
                if job and job.status == "pending":
                    js._finish_demo_job(session, job)
            return {"status": "demo", "job_id": job_id}

        if not _ssh_ready(server_id):
            return _wait_for_host(task, job_id, audit_id, jt)

        _execute(jt, job_id, server_id, audit_id, body)
        return {"status": "ok", "job_id": job_id}
    except Retry:
        raise
    except MaxRetriesExceededError:
        _fail_wait_exceeded(job_id, audit_id, job_type)
        return {"status": "failed", "job_id": job_id, "reason": "host_wait"}
    except Exception as exc:
        logger.exception("exclusive_job failed job=%s type=%s", job_id, job_type)
        _fail_unexpected(job_id, audit_id, job_type, str(exc)[:800])
        return {"status": "error", "job_id": job_id}


def _ssh_ready(server_id: int) -> bool:
    """True when a short SSH probe works. Missing server is not 'host down'."""
    js = _jobs()
    from .ssh import test_connection

    with js._get_fresh_session() as session:
        server = session.get(js.Server, server_id)
        if not server:
            return False
        session.expunge(server)
    try:
        return bool(test_connection(server))
    except Exception:
        logger.info("[Jobs] SSH probe failed for server %s", server_id)
        return False


def _wait_for_host(task, job_id: int, audit_id: int, job_type: str):
    js = _jobs()
    missing = False
    timed_out = False
    limit = host_wait_limit_sec()
    with js._get_fresh_session() as session:
        job = session.get(js.Job, job_id)
        if not job or job.status not in ("pending", "running"):
            return {"status": "skipped", "job_id": job_id}
        if job.status == "running":
            return {"status": "skipped", "job_id": job_id, "reason": "running"}
        server = session.get(js.Server, job.server_id) if job.server_id else None
        if server is None:
            missing = True
        else:
            data = _load_details(job)
            now = datetime.utcnow()
            started_raw = data.get("host_wait_started")
            if not started_raw:
                started = now
            else:
                try:
                    started = datetime.fromisoformat(str(started_raw))
                except Exception:
                    started = now
            if (now - started).total_seconds() >= limit:
                timed_out = True
            else:
                js._merge_job_details(
                    job,
                    host_wait_started=started.isoformat(),
                    current="waiting_for_host",
                    log_line=(
                        f"Host SSH unreachable — waiting "
                        f"(probe every {HOST_WAIT_COUNTDOWN_SEC}s, up to {int(limit)}s)"
                    ),
                    done=False,
                )
                job.status = "pending"
                session.add(job)
                session.commit()
    if missing:
        _fail_message(job_id, audit_id, job_type, "Server not found")
        return {"status": "failed", "job_id": job_id, "reason": "no_server"}
    if timed_out:
        _fail_message(
            job_id,
            audit_id,
            job_type,
            f"Host stayed unreachable for {int(limit)}s",
        )
        return {"status": "failed", "job_id": job_id, "reason": "host_wait"}
    logger.info(
        "[Jobs] %s job #%s waiting for SSH — retry in %ss",
        job_type,
        job_id,
        HOST_WAIT_COUNTDOWN_SEC,
    )
    raise task.retry(countdown=HOST_WAIT_COUNTDOWN_SEC)


def _fail_running_redelivery(job_id: int, audit_id: int, job_type: str) -> None:
    _fail_message(
        job_id,
        audit_id,
        job_type,
        "Worker restarted while this job was running. It was not resumed.",
    )


def _fail_wait_exceeded(job_id: int, audit_id: int, job_type: str) -> None:
    _fail_message(
        job_id,
        audit_id,
        job_type,
        f"Host stayed unreachable for {int(host_wait_limit_sec())}s",
    )


def _fail_unexpected(job_id: int, audit_id: int, job_type: str, message: str) -> None:
    js = _jobs()
    with js._get_fresh_session() as session:
        job = session.get(js.Job, job_id)
        if not job or job.status not in ("pending", "running"):
            return
    _fail_message(job_id, audit_id, job_type, message)


def _fail_message(job_id: int, audit_id: int, job_type: str, message: str) -> None:
    js = _jobs()
    hostname = ""
    with js._get_fresh_session() as session:
        job = session.get(js.Job, job_id)
        if not job or job.status not in ("pending", "running"):
            return
        if job.server_id:
            server = session.get(js.Server, job.server_id)
            hostname = (server.hostname if server else "") or ""
    js._finish(audit_id, job_id, "failed", message, hostname, job_type)


def _execute(job_type: str, job_id: int, server_id: int, audit_id: int, payload: dict) -> None:
    js = _jobs()
    if job_type == "os_patch":
        js._execute_os_patch_sync(job_id, server_id, audit_id, payload.get("os_steps"))
    elif job_type == "container_patch":
        js._execute_container_patch_sync(job_id, server_id, audit_id)
    elif job_type == "host_reboot":
        js._execute_host_reboot(job_id, server_id, audit_id)
    elif job_type == "os_update_check":
        js._execute_os_update_check(job_id, server_id, audit_id)
    elif job_type == "container_update_check":
        js._execute_container_update_check(job_id, server_id, audit_id)
    elif job_type == "docker_stack_check":
        js._execute_docker_stack_check(
            job_id, server_id, audit_id, payload.get("project_path") or ""
        )
    elif job_type == "docker_stack_deploy":
        js._execute_docker_stack_deploy(
            job_id,
            server_id,
            audit_id,
            payload.get("project_path") or "",
            bool(payload.get("pull", True)),
            list(payload.get("compose_files") or []),
        )
    elif job_type in js._STACK_LIFECYCLE_JOB_TYPES:
        action = payload.get("action") or job_type.replace("docker_stack_", "", 1)
        js._execute_docker_stack_lifecycle(
            job_id, server_id, audit_id, payload.get("project_path") or "", action
        )
    elif job_type == "docker_stack_remove":
        js._execute_docker_stack_remove(
            job_id, server_id, audit_id, payload.get("project_path") or ""
        )
    elif job_type == "template_deploy":
        js._execute_template_deploy(
            job_id,
            server_id,
            audit_id,
            payload.get("template_slug") or "",
            bool(payload.get("deploy_now", True)),
        )
    elif job_type == "template_redeploy":
        js._execute_template_redeploy(
            job_id,
            server_id,
            audit_id,
            int(payload.get("deployment_id") or 0),
            bool(payload.get("deploy_now", True)),
        )
    elif job_type == "template_drift_check":
        js._execute_template_drift_check(
            job_id, server_id, audit_id, int(payload.get("deployment_id") or 0)
        )
    else:
        raise ValueError(f"not an exclusive celery job: {job_type}")

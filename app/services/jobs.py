from fastapi import BackgroundTasks
from sqlmodel import Session, select
from ..database import engine
from ..models import Job, AuditLog, Server
from datetime import datetime
import json
import httpx
from ..config import settings
from . import backup, container_patching, os_patching, herder_backup
import logging
import json
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


def _get_fresh_session() -> Session:
    return Session(engine)


def _send_summary_webhook(server_hostname: str, job_type: str, status: str, summary: str):
    if not settings.WEBHOOK_URL:
        return
    try:
        msg = f"PiHerder {job_type} on {server_hostname}: {status}\n{summary[:400]}"
        payload = {
            "message": msg,
            "number": settings.WEBHOOK_NUMBER or "",
            "recipients": json.loads(settings.WEBHOOK_RECIPIENTS or "[]"),
        }
        httpx.post(settings.WEBHOOK_URL, json=payload, timeout=8)
    except Exception:
        pass


def create_job_and_run(
    background_tasks: BackgroundTasks,
    session: Session,
    server: Server,
    job_type: str,
    user_id: int | None = None,
    source_filter: str | None = None,
    os_steps: list[str] | None = None,
):
    server_id = server.id if server is not None else None
    job = Job(server_id=server_id, job_type=job_type, status="pending")
    session.add(job)
    session.commit()
    session.refresh(job)

    audit = AuditLog(
        user_id=user_id,
        server_id=server_id,
        action=job_type,
        status="running",
        details=f"Job #{job.id} started",
    )
    session.add(audit)
    session.commit()
    session.refresh(audit)

    if job_type == "backup":
        background_tasks.add_task(_run_backup_job, job.id, server.id, audit.id, source_filter)
    elif job_type == "container_patch":
        background_tasks.add_task(_run_container_job, job.id, server.id, audit.id)
    elif job_type == "os_patch":
        background_tasks.add_task(_run_os_patch_job, job.id, server.id, audit.id, os_steps)
    elif job_type == "retention":
        background_tasks.add_task(_run_retention_job, job.id, server.id, audit.id)
    elif job_type == "herder_backup":
        background_tasks.add_task(_run_herder_backup_job, job.id, audit.id)

    return job


def _finish(audit_id: int, job_id: int, status: str, snippet: str, hostname: str = "", job_type: str = ""):
    with _get_fresh_session() as s:
        audit = s.get(AuditLog, audit_id)
        job = s.get(Job, job_id)
        if audit:
            audit.status = status
            audit.output_snippet = snippet[:2000]
            audit.finished_at = datetime.utcnow()
            s.add(audit)
        if job:
            job.status = status
            job.finished_at = datetime.utcnow()
            s.add(job)
        s.commit()

    if hostname and job_type:
        _send_summary_webhook(hostname, job_type, status, snippet)


async def _run_backup_job(job_id: int, server_id: int, audit_id: int, source_filter: str | None = None):
    with _get_fresh_session() as s:
        server = s.get(Server, server_id)
    hostname = server.hostname if server else str(server_id)
    logger.debug(f"[JOB] Starting backup for {hostname}")
    try:
        if source_filter:
            filtered = [s for s in server.get_backup_sources() if s.get("source") == source_filter]
            if filtered:
                server.backup_paths = json.dumps(filtered)
        # Run long blocking work (SSH + rsync Popen loops + file walks) off the event loop
        # so the web server stays responsive during manual/scheduled backups.
        res = await run_in_threadpool(backup.run_backup, server)
        summary = json.dumps(res)
        _finish(audit_id, job_id, "success", summary, hostname, "backup")
    except Exception as e:
        _finish(audit_id, job_id, "failed", str(e), hostname, "backup")


async def _run_container_job(job_id: int, server_id: int, audit_id: int):
    with _get_fresh_session() as s:
        server = s.get(Server, server_id)
    hostname = server.hostname if server else str(server_id)
    logger.debug(f"[JOB] Starting container_patch for {hostname}")
    try:
        res = await run_in_threadpool(container_patching.run_project_update, server)
        _finish(audit_id, job_id, "success", str(res), hostname, "container_patch")
    except Exception as e:
        _finish(audit_id, job_id, "failed", str(e), hostname, "container_patch")


async def _run_os_patch_job(job_id: int, server_id: int, audit_id: int, os_steps: list[str] | None = None):
    with _get_fresh_session() as s:
        server = s.get(Server, server_id)
    hostname = server.hostname if server else str(server_id)
    # Pre-initialize progress dict so UI streaming/poll sees activity immediately (no perceived freeze)
    try:
        os_patching.init_os_patch_progress(hostname, "starting patch job...")
    except Exception:
        pass
    try:
        res = await run_in_threadpool(os_patching.run_os_patch, server, selected_steps=os_steps)
        _finish(audit_id, job_id, "success", str(res), hostname, "os_patch")
    except Exception as e:
        _finish(audit_id, job_id, "failed", str(e), hostname, "os_patch")
    finally:
        # Clean after the threadpool work has fully appended final logs.
        # UI polls/SSE have a short window; last state also goes to audit log.
        os_patching._os_patch_progress.pop(hostname, None)


async def _run_retention_job(job_id: int, server_id: int, audit_id: int):
    with _get_fresh_session() as s:
        server = s.get(Server, server_id)
    hostname = server.hostname if server else str(server_id)
    logger.debug(f"[JOB] Starting retention for {hostname}")
    try:
        res = await run_in_threadpool(backup.run_retention, server)
        _finish(audit_id, job_id, "success", str(res), hostname, "retention")
    except Exception as e:
        _finish(audit_id, job_id, "failed", str(e), hostname, "retention")


async def _run_herder_backup_job(job_id: int, audit_id: int):
    logger.debug("[JOB] Starting herder self-backup")
    hostname = "piherder"
    try:
        # Default to safe config-only (no full audit in scheduled run)
        res = await run_in_threadpool(herder_backup.create_herder_backup, include_audit=False, config_only=True)
        summary = json.dumps({"path": str(res)})
        _finish(audit_id, job_id, "success", summary, hostname, "herder_backup")
    except Exception as e:
        _finish(audit_id, job_id, "failed", str(e), hostname, "herder_backup")

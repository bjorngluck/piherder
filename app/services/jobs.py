# app/services/jobs.py
from fastapi import BackgroundTasks
from sqlmodel import Session, select
from ..database import engine
from ..models import Job, AuditLog, Server
from datetime import datetime, timedelta
import json
import httpx
from ..config import settings
from . import backup, container_patching, os_patching, herder_backup
from .backup_audit import record_backup_audit_event, record_backup_audit_from_job
import logging
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


class BackupAlreadyRunning(Exception):
    def __init__(self, job: Job):
        self.job = job


# Import Celery task for backup jobs (integrated path)
try:
    from ..tasks import backup_server
    HAS_CELERY = True
except Exception:
    HAS_CELERY = False
    backup_server = None


def _get_fresh_session() -> Session:
    return Session(engine)


def _revoke_celery_task(task_id: str | None) -> None:
    if not task_id or not HAS_CELERY:
        return
    try:
        from ..celery_app import celery
        celery.control.revoke(task_id, terminate=True, signal="SIGTERM")
        logger.info(f"[Jobs] Revoked celery task {task_id}")
    except Exception as e:
        logger.warning(f"[Jobs] Failed to revoke celery task {task_id}: {e}")


def _mark_job_failed(
    job: Job, message: str, session: Session, *, record_audit: bool = True
) -> None:
    job.status = "failed"
    job.finished_at = datetime.utcnow()
    details = {}
    if job.details:
        try:
            details = json.loads(job.details)
        except Exception:
            pass
    details["error"] = message
    details["current"] = "failed"
    details["audit_failed_recorded"] = True
    lines = list(details.get("log_lines") or [])
    lines.append(message[:240])
    details["log_lines"] = lines[-15:]
    job.details = json.dumps(details)
    session.add(job)
    if record_audit and job.job_type == "backup":
        record_backup_audit_from_job(
            session,
            job,
            "failed",
            message=message,
            output_snippet={"error": message},
        )


def job_source_filter(job: Job | None) -> str | None:
    """Source path for a per-source job, or None for a full backup."""
    if not job or not job.details:
        return None
    try:
        data = json.loads(job.details)
        return data.get("source_filter") or None
    except Exception:
        return None


def get_active_backup_jobs(session: Session, server_id: int) -> list[Job]:
    return list(
        session.exec(
            select(Job)
            .where(
                Job.server_id == server_id,
                Job.job_type == "backup",
                Job.status.in_(["pending", "running"]),
            )
            .order_by(Job.created_at.asc())
        ).all()
    )


def get_active_backup_job(session: Session, server_id: int) -> Job | None:
    jobs = get_active_backup_jobs(session, server_id)
    running = next((j for j in jobs if j.status == "running"), None)
    return running or (jobs[-1] if jobs else None)


def get_running_backup_job(session: Session, server_id: int) -> Job | None:
    return session.exec(
        select(Job)
        .where(
            Job.server_id == server_id,
            Job.job_type == "backup",
            Job.status == "running",
        )
        .order_by(Job.started_at.desc())
    ).first()


def get_active_job_for_source(
    session: Session, server_id: int, source_filter: str | None
) -> Job | None:
    want = source_filter or None
    for job in get_active_backup_jobs(session, server_id):
        if job_source_filter(job) == want:
            return job
    return None


def resolve_backup_job(
    session: Session,
    server_id: int,
    *,
    job_id: int | None = None,
    source_filter: str | None = None,
) -> Job | None:
    if job_id:
        job = session.get(Job, job_id)
        if job and job.server_id == server_id and job.job_type == "backup":
            return job
        return None
    if source_filter is not None:
        return get_active_job_for_source(session, server_id, source_filter or None)
    return get_running_backup_job(session, server_id) or get_active_backup_job(session, server_id)


def stop_backup_job(session: Session, server: Server, job: Job) -> Job:
    """Cancel a queued job or stop a running rsync for the given job."""
    if job.status == "running":
        backup.stop_backup(server.hostname)
    _revoke_celery_task(job.celery_task_id)
    _mark_job_failed(job, "Stopped by user", session)
    session.commit()
    return job


def stop_active_backup(session: Session, server: Server, job: Job | None = None) -> Job | None:
    """Stop the running backup, or a specific job when provided."""
    target = job or get_running_backup_job(session, server.id) or get_active_backup_job(session, server.id)
    if not target:
        backup.stop_backup(server.hostname)
        return None
    return stop_backup_job(session, server, target)


def attach_source_job_states(profiles: list, active_jobs: list[Job]) -> list[dict]:
    by_source: dict[str, Job] = {}
    for job in active_jobs:
        key = job_source_filter(job) or "__full__"
        prev = by_source.get(key)
        if not prev or job.status == "running":
            by_source[key] = job
    out: list[dict] = []
    for profile in profiles:
        row = dict(profile) if isinstance(profile, dict) else profile.model_dump()
        job = by_source.get(row.get("source"))
        if job:
            row["active_job_id"] = job.id
            row["active_job_status"] = job.status
        out.append(row)
    return out


def supersede_running_backups(session: Session, server_id: int) -> int:
    """Clear stuck running/pending jobs for this server so a new backup can start."""
    stuck = session.exec(
        select(Job).where(
            Job.server_id == server_id,
            Job.job_type == "backup",
            Job.status.in_(["pending", "running"]),
        )
    ).all()
    server = session.get(Server, server_id)
    for job in stuck:
        _revoke_celery_task(job.celery_task_id)
        _mark_job_failed(job, "Superseded by new backup run", session)
    if server and stuck:
        backup.stop_backup(server.hostname)
    if stuck:
        session.commit()
        logger.info(f"[Jobs] Superseded {len(stuck)} running backup job(s) for server {server_id}")
    return len(stuck)


def cleanup_stale_backup_jobs(session: Session, max_age_minutes: int = 120) -> int:
    """Mark old pending/running backup jobs as failed (worker crash / restart recovery)."""
    cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
    stale = session.exec(
        select(Job).where(
            Job.job_type == "backup",
            Job.status.in_(["pending", "running"]),
            Job.created_at < cutoff,
        )
    ).all()
    for job in stale:
        _mark_job_failed(job, "Stale job — worker timeout or restart", session)
    if stale:
        session.commit()
        logger.info(f"[Jobs] Cleaned up {len(stale)} stale backup job(s)")
    return len(stale)


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
    if job_type == "backup":
        cleanup_stale_backup_jobs(session)
        if server_id:
            active = get_active_job_for_source(session, server_id, source_filter)
            if active:
                raise BackupAlreadyRunning(active)
    job = Job(server_id=server_id, job_type=job_type, status="pending")
    if job_type == "backup":
        job.details = json.dumps({
            "current": "queued",
            "source_filter": source_filter,
            "user_id": user_id,
            "log_lines": ["Backup queued…"],
            "queued_at": datetime.utcnow().isoformat(),
        })
    session.add(job)
    session.commit()
    session.refresh(job)

    audit = None
    if job_type == "backup":
        src_label = source_filter or "all sources"
        record_backup_audit_event(
            session,
            server_id=server_id,
            job_id=job.id,
            phase="request",
            user_id=user_id,
            source_filter=source_filter,
            message=f"Backup requested for {src_label}",
        )
        record_backup_audit_event(
            session,
            server_id=server_id,
            job_id=job.id,
            phase="queued",
            user_id=user_id,
            source_filter=source_filter,
            message="Waiting for worker",
        )
        session.commit()
        if HAS_CELERY and backup_server:
            logger.info(f"[Jobs] Enqueuing backup job #{job.id} for server {server_id} to Celery")
            async_result = backup_server.delay(
                server.id,
                job_id=job.id,
                source_filter=source_filter,
            )
            job.celery_task_id = async_result.id
            session.add(job)
            session.commit()
        else:
            raise RuntimeError("Celery worker required for backups — start celery-worker container")
    else:
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

    if job_type == "container_patch":
        background_tasks.add_task(_run_container_job, job.id, server.id, audit.id)
    elif job_type == "os_patch":
        background_tasks.add_task(_run_os_patch_job, job.id, server.id, audit.id, os_steps)
    elif job_type == "os_update_check":
        background_tasks.add_task(_run_os_update_check_job, job.id, server.id, audit.id)
    elif job_type == "container_update_check":
        background_tasks.add_task(_run_container_update_check_job, job.id, server.id, audit.id)
    elif job_type == "retention":
        background_tasks.add_task(_run_retention_job, job.id, server.id, audit.id)
    elif job_type == "herder_backup":
        background_tasks.add_task(_run_herder_backup_job, job.id, audit.id)

    return job


def enqueue_backup_for_server(
    session: Session,
    server: Server,
    user_id: int | None = None,
    source_filter: str | None = None,
) -> Job:
    """Create Job + AuditLog in DB and hand off to Celery — web never runs rsync."""
    cleanup_stale_backup_jobs(session)
    active = get_active_job_for_source(session, server.id, source_filter)
    if active:
        logger.info(f"[Jobs] Skipping enqueue — backup job #{active.id} already active for server {server.id}")
        return active
    job = Job(server_id=server.id, job_type="backup", status="pending")
    job.details = json.dumps({
        "current": "queued",
        "source_filter": source_filter,
        "user_id": user_id,
        "log_lines": ["Backup queued…"],
        "queued_at": datetime.utcnow().isoformat(),
    })
    session.add(job)
    session.commit()
    session.refresh(job)

    src_label = source_filter or "all sources"
    record_backup_audit_event(
        session,
        server_id=server.id,
        job_id=job.id,
        phase="request",
        user_id=user_id,
        source_filter=source_filter,
        message=f"Scheduled backup requested for {src_label}",
    )
    record_backup_audit_event(
        session,
        server_id=server.id,
        job_id=job.id,
        phase="queued",
        user_id=user_id,
        source_filter=source_filter,
        message="Waiting for worker",
    )
    session.commit()

    if not HAS_CELERY or not backup_server:
        raise RuntimeError("Celery worker required for backups")

    async_result = backup_server.delay(
        server.id,
        job_id=job.id,
        source_filter=source_filter,
    )
    job.celery_task_id = async_result.id
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _finish(audit_id: int, job_id: int, status: str, snippet: str, hostname: str = "", job_type: str = ""):
    server_id = None
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
            server_id = job.server_id
        s.commit()

        # Backup success/failure notifications (best-effort)
        if job_type == "backup" and server_id:
            try:
                from .notifications import notify_backup_failed, resolve_backup_failed
                server = s.get(Server, server_id)
                name = server.name if server else hostname
                if status == "failed":
                    notify_backup_failed(s, server_id, name or str(server_id), snippet[:300])
                elif status == "success":
                    resolve_backup_failed(s, server_id)
            except Exception as e:
                logger.debug(f"backup notification: {e}")

    if hostname and job_type:
        _send_summary_webhook(hostname, job_type, status, snippet)


async def _run_backup_job(job_id: int, server_id: int, audit_id: int, source_filter: str | None = None):
    """Legacy/local path for backup (kept for fallback and other job types remain here)."""
    with _get_fresh_session() as s:
        server = s.get(Server, server_id)
    hostname = server.hostname if server else str(server_id)
    logger.debug(f"[JOB] Starting backup for {hostname}")
    try:
        sources_override = None
        if source_filter:
            try:
                all_sources = server.get_backup_sources()
                filtered = [s for s in all_sources if s.get("source") == source_filter]
                if filtered:
                    sources_override = filtered
            except Exception as e:
                logger.warning(f"Could not apply source_filter: {e}")

        res = await run_in_threadpool(backup.run_backup, server, sources_override=sources_override)
        summary = json.dumps(res)
        status = "success" if backup.backup_succeeded(res) else "failed"
        _finish(audit_id, job_id, status, summary, hostname, "backup")
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
        res = await run_in_threadpool(herder_backup.create_herder_backup, include_audit=False, config_only=True)
        summary = json.dumps({"path": str(res)})
        _finish(audit_id, job_id, "success", summary, hostname, "herder_backup")
    except Exception as e:
        _finish(audit_id, job_id, "failed", str(e), hostname, "herder_backup")


def run_os_update_check_now(session: Session, server: Server, user_id: int | None = None) -> Job:
    """Synchronous path for scheduler: run OS check in-thread (no BackgroundTasks)."""
    job = Job(server_id=server.id, job_type="os_update_check", status="running", started_at=datetime.utcnow())
    session.add(job)
    session.commit()
    session.refresh(job)
    audit = AuditLog(
        user_id=user_id,
        server_id=server.id,
        action="os_update_check",
        status="running",
        details=f"Job #{job.id} started",
    )
    session.add(audit)
    session.commit()
    session.refresh(audit)
    try:
        res = os_patching.check_os_updates(server)
        _apply_os_check_result(session, server.id, res)
        status = "failed" if res.get("error") and res.get("updates_count") is None else "success"
        _finish(audit.id, job.id, status, json.dumps(res), server.hostname, "os_update_check")
    except Exception as e:
        _finish(audit.id, job.id, "failed", str(e), server.hostname, "os_update_check")
    session.refresh(job)
    return job


def run_container_update_check_now(session: Session, server: Server, user_id: int | None = None) -> Job:
    """Synchronous path for scheduler: fleet container image check (no up -d)."""
    job = Job(server_id=server.id, job_type="container_update_check", status="running", started_at=datetime.utcnow())
    session.add(job)
    session.commit()
    session.refresh(job)
    audit = AuditLog(
        user_id=user_id,
        server_id=server.id,
        action="container_update_check",
        status="running",
        details=f"Job #{job.id} started",
    )
    session.add(audit)
    session.commit()
    session.refresh(audit)
    try:
        res = container_patching.check_all_projects_updates(server)
        _apply_container_check_result(session, server.id, res)
        status = "success" if not res.get("failed") or res.get("projects_checked") else "success"
        _finish(audit.id, job.id, status, json.dumps(res), server.hostname, "container_update_check")
    except Exception as e:
        _finish(audit.id, job.id, "failed", str(e), server.hostname, "container_update_check")
    session.refresh(job)
    return job


def _apply_os_check_result(session: Session, server_id: int, res: dict) -> None:
    server = session.get(Server, server_id)
    if not server:
        return
    server.last_os_check_at = datetime.utcnow()
    if res.get("supported", True) and res.get("updates_count") is not None:
        server.os_updates_count = int(res.get("updates_count") or 0)
    server.reboot_pending = bool(res.get("reboot_pending"))
    sample = res.get("packages_sample") or []
    server.os_updates_summary = json.dumps({
        "packages_sample": sample[:15],
        "error": res.get("error"),
    })
    session.add(server)
    session.commit()
    try:
        from .notifications import notify_os_updates
        notify_os_updates(
            session,
            server_id=server.id,
            server_name=server.name,
            updates_count=server.os_updates_count or 0,
            reboot_pending=server.reboot_pending,
        )
    except Exception as e:
        logger.debug(f"notify_os_updates: {e}")


def _apply_container_check_result(session: Session, server_id: int, res: dict) -> None:
    server = session.get(Server, server_id)
    if not server:
        return
    projects = res.get("projects_with_updates") or []
    server.last_container_check_at = datetime.utcnow()
    server.container_updates_count = len(projects)
    server.container_updates_summary = json.dumps({
        "projects": projects,
        "failed": res.get("failed") or [],
        "checked": res.get("projects_checked") or [],
    })
    session.add(server)
    session.commit()
    try:
        from .notifications import notify_container_updates
        notify_container_updates(
            session,
            server_id=server.id,
            server_name=server.name,
            projects=projects,
        )
    except Exception as e:
        logger.debug(f"notify_container_updates: {e}")


async def _run_os_update_check_job(job_id: int, server_id: int, audit_id: int):
    with _get_fresh_session() as s:
        server = s.get(Server, server_id)
    hostname = server.hostname if server else str(server_id)
    try:
        res = await run_in_threadpool(os_patching.check_os_updates, server)
        with _get_fresh_session() as s:
            _apply_os_check_result(s, server_id, res)
        status = "failed" if res.get("error") and res.get("updates_count") is None else "success"
        _finish(audit_id, job_id, status, json.dumps(res), hostname, "os_update_check")
    except Exception as e:
        _finish(audit_id, job_id, "failed", str(e), hostname, "os_update_check")


async def _run_container_update_check_job(job_id: int, server_id: int, audit_id: int):
    with _get_fresh_session() as s:
        server = s.get(Server, server_id)
    hostname = server.hostname if server else str(server_id)
    try:
        res = await run_in_threadpool(container_patching.check_all_projects_updates, server)
        with _get_fresh_session() as s:
            _apply_container_check_result(s, server_id, res)
        _finish(audit_id, job_id, "success", json.dumps(res), hostname, "container_update_check")
    except Exception as e:
        _finish(audit_id, job_id, "failed", str(e), hostname, "container_update_check")

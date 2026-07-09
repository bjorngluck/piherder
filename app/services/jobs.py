# app/services/jobs.py
from fastapi import BackgroundTasks
from sqlmodel import Session, select
from ..database import engine
from ..models import Job, AuditLog, Server
from datetime import datetime, timedelta
import json
import re
import httpx
from ..config import settings
from . import backup, container_patching, os_patching, herder_backup
from .backup_audit import record_backup_audit_event, record_backup_audit_from_job
import logging
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

# Placeholder written when a non-backup job starts; replaced on _finish with a summary
_JOB_STARTED_RE = re.compile(r"^Job #\d+ started")


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


def _load_server_for_job(server_id: int) -> tuple[Server | None, str]:
    """Load Server for background work outside a live Session.

    Accessing attributes after session close/commit otherwise raises
    DetachedInstanceError (expire_on_commit) — which was leaving OS patch
    jobs stuck at "running" with no live log progress.
    """
    with Session(engine, expire_on_commit=False) as s:
        server = s.get(Server, server_id)
        if not server:
            return None, str(server_id)
        hostname = (server.hostname or server.name or str(server_id))
        # Touch fields used by SSH / patching / checks while still attached
        _ = (
            server.id,
            server.name,
            server.hostname,
            server.ssh_username,
            server.ssh_port,
            server.ssh_private_key_encrypted,
            server.ssh_password_encrypted,
            server.ssh_public_key,
            server.os_type,
            server.os_patch_enabled,
            server.container_patch_enabled,
            server.docker_base_dir,
            server.os_updates_count,
            server.os_updates_summary,
            server.reboot_pending,
        )
        try:
            # Backup sources may be JSON property
            if hasattr(server, "get_backup_sources"):
                server.get_backup_sources()
        except Exception:
            pass
        s.expunge(server)
        return server, hostname


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


JOB_TYPE_LABELS = {
    "backup": "Backup",
    "os_patch": "OS patch",
    "container_patch": "Container patch",
    "os_update_check": "OS check",
    "container_update_check": "Image check",
    "retention": "Retention",
    "diagnostics": "Diagnostics",
    "herder_backup": "PiHerder backup",
}


def job_type_label(job_type: str | None) -> str:
    if not job_type:
        return "Job"
    return JOB_TYPE_LABELS.get(job_type, job_type.replace("_", " ").title())


def list_jobs_for_server(
    session: Session,
    server_id: int,
    *,
    limit: int = 25,
    status: str | None = None,
    job_type: str | None = None,
    active_only: bool = False,
) -> list[Job]:
    """Recent jobs for a server (queue + history)."""
    q = select(Job).where(Job.server_id == server_id)
    if active_only:
        q = q.where(Job.status.in_(["pending", "running"]))
    elif status:
        q = q.where(Job.status == status)
    if job_type:
        q = q.where(Job.job_type == job_type)
    q = q.order_by(Job.created_at.desc()).limit(max(1, min(int(limit), 100)))
    return list(session.exec(q).all())


def _jobs_filter_query(
    *,
    server_id: int | None = None,
    status: str | None = None,
    job_type: str | None = None,
    active_only: bool = False,
    date_from=None,
    date_to=None,
):
    q = select(Job)
    if server_id is not None:
        q = q.where(Job.server_id == server_id)
    if active_only:
        q = q.where(Job.status.in_(["pending", "running"]))
    elif status:
        q = q.where(Job.status == status)
    if job_type:
        q = q.where(Job.job_type == job_type)
    if date_from is not None:
        q = q.where(Job.created_at >= date_from)
    if date_to is not None:
        q = q.where(Job.created_at <= date_to)
    return q


def list_jobs(
    session: Session,
    *,
    server_id: int | None = None,
    status: str | None = None,
    job_type: str | None = None,
    active_only: bool = False,
    date_from=None,
    date_to=None,
    limit: int = 50,
    offset: int = 0,
) -> list[Job]:
    """Fleet-wide job queue + history (optional filters + pagination)."""
    q = _jobs_filter_query(
        server_id=server_id,
        status=status,
        job_type=job_type,
        active_only=active_only,
        date_from=date_from,
        date_to=date_to,
    )
    lim = max(1, min(int(limit), 100))
    off = max(0, int(offset or 0))
    q = q.order_by(Job.created_at.desc()).offset(off).limit(lim)
    return list(session.exec(q).all())


def count_jobs(
    session: Session,
    *,
    server_id: int | None = None,
    status: str | None = None,
    job_type: str | None = None,
    active_only: bool = False,
    date_from=None,
    date_to=None,
) -> int:
    from sqlalchemy import func

    q = select(func.count()).select_from(Job)
    if server_id is not None:
        q = q.where(Job.server_id == server_id)
    if active_only:
        q = q.where(Job.status.in_(["pending", "running"]))
    elif status:
        q = q.where(Job.status == status)
    if job_type:
        q = q.where(Job.job_type == job_type)
    if date_from is not None:
        q = q.where(Job.created_at >= date_from)
    if date_to is not None:
        q = q.where(Job.created_at <= date_to)
    try:
        return int(session.exec(q).one())
    except Exception:
        row = session.exec(q).first()
        return int(row or 0)


def job_public_dict(job: Job, *, detail: bool = False) -> dict:
    """JSON-safe job summary for list/poll UI. detail=True includes larger log tail."""
    details: dict = {}
    if job.details:
        try:
            parsed = json.loads(job.details)
            if isinstance(parsed, dict):
                details = parsed
        except Exception:
            details = {}
    summary = details.get("summary") or ""
    log_lines = details.get("log_lines") or []
    tail_n = 60 if detail else 8
    out = {
        "id": job.id,
        "server_id": job.server_id,
        "job_type": job.job_type,
        "job_type_label": job_type_label(job.job_type),
        "status": job.status,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "current": details.get("current"),
        "summary": summary,
        "scheduled": bool(details.get("scheduled")),
        "log_tail": list(log_lines)[-tail_n:] if log_lines else [],
        "done": job.status in ("success", "failed"),
        "os_steps": details.get("os_steps"),
        "error": details.get("error"),
    }
    if detail:
        out["result_snippet"] = (details.get("result_snippet") or "")[:4000]
        # Pretty raw details minus huge keys already shown
        safe = {
            k: v
            for k, v in details.items()
            if k not in ("log_lines",) and not (isinstance(v, str) and len(v) > 8000)
        }
        try:
            out["details_json"] = json.dumps(safe, indent=2, default=str)[:12000]
        except Exception:
            out["details_json"] = str(safe)[:4000]
    return out


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
    else:
        labels = {
            "os_patch": "OS patch queued…",
            "container_patch": "Container patch queued…",
            "retention": "Retention cleanup queued…",
            "os_update_check": "OS update check queued…",
            "container_update_check": "Container update check queued…",
        }
        job.details = json.dumps({
            "current": "queued",
            "log_lines": [labels.get(job_type, f"{job_type} queued…")],
            "done": False,
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
        start_details = f"Job #{job.id} started"
        if job_type == "os_patch" and os_steps:
            start_details = f"Job #{job.id} started · {','.join(os_steps)}"
        audit = AuditLog(
            user_id=user_id,
            server_id=server_id,
            action=job_type,
            status="running",
            details=start_details,
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


def _merge_job_details(job: Job, **fields) -> None:
    """Merge keys into Job.details JSON (for UI polling / holding modal)."""
    try:
        data = json.loads(job.details or "{}")
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    for k, v in fields.items():
        if k == "log_line" and v:
            lines = list(data.get("log_lines") or [])
            lines.append(str(v))
            data["log_lines"] = lines[-40:]
        elif k == "log_lines" and v is not None:
            data["log_lines"] = list(v)[-40:]
        else:
            data[k] = v
    job.details = json.dumps(data)


def _human_job_summary(job_type: str, status: str, snippet: str) -> str:
    """Short line for holding-modal completion."""
    try:
        data = json.loads(snippet) if snippet and str(snippet).strip().startswith(("{", "[")) else None
    except Exception:
        data = None
    if job_type == "os_update_check" and isinstance(data, dict):
        ready = data.get("actionable_count", data.get("updates_count"))
        phased = data.get("phased_count") or 0
        total = data.get("total_upgradable")
        parts = [f"{ready} ready to install"]
        if phased:
            parts.append(f"{phased} phased")
        if total is not None and total != ready:
            parts.append(f"{total} listed")
        if data.get("reboot_pending"):
            parts.append("reboot pending")
        if data.get("error"):
            parts.append(f"note: {str(data['error'])[:80]}")
        return " · ".join(parts)
    if job_type == "container_update_check" and isinstance(data, dict):
        n = len(data.get("projects_with_updates") or [])
        checked = len(data.get("projects_checked") or data.get("checked") or [])
        return f"{n} project(s) with image updates" + (f" ({checked} checked)" if checked else "")
    if job_type == "os_patch" and isinstance(data, dict):
        return (data.get("summary") or snippet or status)[:200]
    if job_type == "container_patch":
        if isinstance(data, dict):
            return (data.get("summary") or container_patching.summarize_container_patch(data) or status)[:200]
        return (snippet or status)[:200]
    if job_type == "retention":
        return (snippet or "Retention complete")[:200]
    return (snippet or status)[:200]


def _finish(audit_id: int, job_id: int, status: str, snippet: str, hostname: str = "", job_type: str = ""):
    server_id = None
    with _get_fresh_session() as s:
        audit = s.get(AuditLog, audit_id)
        job = s.get(Job, job_id)
        jt = job_type or (job.job_type if job else "")
        summary = _human_job_summary(jt, status, snippet)
        if audit:
            audit.status = status
            # OS patch stores JSON + apt log_tail; allow a larger snippet than default
            max_snip = 16000 if jt == "os_patch" else 2000
            audit.output_snippet = (snippet or "")[:max_snip]
            audit.finished_at = datetime.utcnow()
            # Replace "Job #N started" with a scannable finished line for the audit list
            if jt == "os_patch" and summary:
                audit.details = f"Job #{job_id} · {summary}"[:500]
            elif (
                status in ("success", "failed")
                and summary
                and (not audit.details or _JOB_STARTED_RE.search(audit.details or ""))
            ):
                audit.details = f"Job #{job_id} · {summary}"[:500]
            s.add(audit)
        if job:
            job.status = status
            job.finished_at = datetime.utcnow()
            _merge_job_details(
                job,
                current=None if status in ("success", "failed") else status,
                status=status,
                summary=summary,
                result_snippet=(snippet or "")[:1500],
                log_line=f"[{status}] {summary}",
                done=True,
            )
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


def _flush_container_progress_to_job(job_id: int, current: str, log_line: str) -> None:
    """Best-effort live Job.details update for JobHold polling."""
    try:
        with _get_fresh_session() as s:
            job = s.get(Job, job_id)
            if not job or job.status not in ("pending", "running"):
                return
            _merge_job_details(job, current=current or "patching", log_line=log_line, done=False)
            s.add(job)
            s.commit()
    except Exception as e:
        logger.debug(f"container progress flush: {e}")


async def _run_container_job(job_id: int, server_id: int, audit_id: int):
    server, hostname = _load_server_for_job(server_id)
    with _get_fresh_session() as s:
        job = s.get(Job, job_id)
        if job:
            job.status = "running"
            job.started_at = datetime.utcnow()
            _merge_job_details(job, current="starting", log_line="Container patch started…", done=False)
            s.add(job)
            s.commit()
    if not server:
        _finish(audit_id, job_id, "failed", "Server not found", hostname, "container_patch")
        return
    logger.debug(f"[JOB] Starting container_patch for {hostname}")
    try:
        container_patching.init_container_patch_progress(hostname, "starting container patch…")
    except Exception:
        pass

    def _on_progress(current: str, msg: str):
        _flush_container_progress_to_job(job_id, current, msg)

    try:
        res = await run_in_threadpool(
            container_patching.run_project_update, server, None, _on_progress
        )
        status = "success" if container_patching.container_patch_succeeded(res) else "failed"
        # Refresh container update counts before UI reload
        if status == "success":
            try:
                with _get_fresh_session() as s:
                    j = s.get(Job, job_id)
                    if j:
                        _merge_job_details(j, current="rechecking", log_line="Rechecking image updates…")
                        s.add(j)
                        s.commit()
                check = await run_in_threadpool(
                    container_patching.check_all_projects_updates, server
                )
                with _get_fresh_session() as s:
                    _apply_container_check_result(s, server_id, check)
                n = len((check or {}).get("projects_with_updates") or [])
                container_patching.append_container_log(
                    hostname, f"[containers] update count refreshed: {n} project(s) with updates"
                )
                _flush_container_progress_to_job(
                    job_id, "rechecking", f"[containers] {n} project(s) still have updates"
                )
            except Exception as e:
                logger.debug(f"post-container-patch recheck: {e}")
                try:
                    container_patching.append_container_log(hostname, f"[containers] recheck failed: {e}")
                except Exception:
                    pass
        try:
            container_patching.mark_container_patch_done(hostname, finished_ok=(status == "success"))
            container_patching.append_container_log(hostname, f"[containers] all done — {status}")
        except Exception:
            pass
        if isinstance(res, dict) and not res.get("summary"):
            res["summary"] = container_patching.summarize_container_patch(res)
        snippet = json.dumps(res) if isinstance(res, dict) else str(res)
        _finish(audit_id, job_id, status, snippet, hostname, "container_patch")
    except Exception as e:
        try:
            container_patching.mark_container_patch_done(hostname, finished_ok=False)
            container_patching.append_container_log(hostname, f"[containers] ERROR: {e}")
        except Exception:
            pass
        _finish(
            audit_id,
            job_id,
            "failed",
            json.dumps({"error": str(e), "summary": f"Failed: {str(e)[:120]}"}),
            hostname,
            "container_patch",
        )
    finally:
        import asyncio

        async def _delayed_clear():
            await asyncio.sleep(90)
            try:
                container_patching.clear_container_patch_progress(hostname)
            except Exception:
                pass

        try:
            asyncio.create_task(_delayed_clear())
        except Exception:
            pass


async def _run_os_patch_job(job_id: int, server_id: int, audit_id: int, os_steps: list[str] | None = None):
    server, hostname = _load_server_for_job(server_id)
    with _get_fresh_session() as s:
        job = s.get(Job, job_id)
        if job:
            job.status = "running"
            job.started_at = datetime.utcnow()
            _merge_job_details(job, current="patching", log_line="OS patch started…", done=False)
            s.add(job)
            s.commit()
    if not server:
        _finish(audit_id, job_id, "failed", "Server not found", hostname, "os_patch")
        return
    # Pre-initialize progress dict so UI streaming/poll sees activity immediately
    try:
        os_patching.init_os_patch_progress(hostname, "starting patch job...")
    except Exception:
        pass
    try:
        res = await run_in_threadpool(os_patching.run_os_patch, server, selected_steps=os_steps)
        status = "success" if os_patching.os_patch_succeeded(res) else "failed"
        post_check: dict | None = None
        # Refresh cached OS update count BEFORE marking progress done so UI reload sees new badges
        if status == "success":
            try:
                os_patching._append_os_log(hostname, "[os] rechecking update counts (may take a minute)…")
                with _get_fresh_session() as s:
                    j = s.get(Job, job_id)
                    if j:
                        _merge_job_details(j, current="rechecking", log_line="Rechecking OS updates…")
                        s.add(j)
                        s.commit()
                # Reload detached server for recheck (same object is fine if attrs loaded)
                check = await run_in_threadpool(os_patching.check_os_updates, server)
                post_check = check if isinstance(check, dict) else None
                with _get_fresh_session() as s:
                    _apply_os_check_result(s, server_id, check)
                ready = check.get("actionable_count", check.get("updates_count"))
                phased = check.get("phased_count") or 0
                os_patching._append_os_log(
                    hostname,
                    f"[os] update count refreshed: {ready} ready"
                    + (f", {phased} phased" if phased else "")
                    + f", reboot_pending={check.get('reboot_pending')}",
                )
            except Exception as e:
                logger.debug(f"post-patch os update refresh: {e}")
                try:
                    os_patching._append_os_log(hostname, f"[os] recheck failed: {e}")
                except Exception:
                    pass
        # Mark progress done only after recheck so UI does not reload with stale counts
        try:
            os_patching.mark_os_patch_done(hostname, finished_ok=(status == "success"))
            os_patching._append_os_log(hostname, f"[os] all done — {status}")
        except Exception:
            pass
        # Persist summary + apt log tail (+ post-check counts) for the audit trail
        res = os_patching.attach_audit_fields(res, hostname, post_check=post_check)
        snippet = json.dumps(res)
        _finish(audit_id, job_id, status, snippet, hostname, "os_patch")
    except Exception as e:
        try:
            os_patching.mark_os_patch_done(hostname, finished_ok=False)
            os_patching._append_os_log(hostname, f"[os] ERROR: {e}")
        except Exception:
            pass
        fail_payload = os_patching.attach_audit_fields(
            {
                "server": hostname,
                "error": str(e),
                "summary": f"Failed: {str(e)[:120]}",
            },
            hostname,
        )
        _finish(audit_id, job_id, "failed", json.dumps(fail_payload), hostname, "os_patch")
    finally:
        # Keep final logs for UI polls (~90s); do not wipe immediately
        import asyncio
        async def _delayed_clear():
            await asyncio.sleep(90)
            try:
                os_patching.clear_os_patch_progress(hostname)
            except Exception:
                pass
        try:
            asyncio.create_task(_delayed_clear())
        except Exception:
            pass


async def _run_retention_job(job_id: int, server_id: int, audit_id: int):
    server, hostname = _load_server_for_job(server_id)
    with _get_fresh_session() as s:
        job = s.get(Job, job_id)
        if job:
            job.status = "running"
            job.started_at = datetime.utcnow()
            _merge_job_details(
                job,
                current="cleaning",
                log_line="Retention cleanup started…",
                done=False,
            )
            s.add(job)
            s.commit()
    if not server:
        _finish(audit_id, job_id, "failed", "Server not found", hostname, "retention")
        return
    logger.debug(f"[JOB] Starting retention for {hostname}")
    try:
        with _get_fresh_session() as s:
            j = s.get(Job, job_id)
            if j:
                _merge_job_details(
                    j, current="cleaning", log_line=f"Cleaning old backups for {hostname}…"
                )
                s.add(j)
                s.commit()
        res = await run_in_threadpool(backup.run_retention, server)
        summary = (
            res
            if isinstance(res, str)
            else json.dumps(res)
            if isinstance(res, dict)
            else str(res)
        )
        with _get_fresh_session() as s:
            j = s.get(Job, job_id)
            if j:
                _merge_job_details(
                    j, current="finishing", log_line=f"Retention result: {str(summary)[:200]}"
                )
                s.add(j)
                s.commit()
        _finish(audit_id, job_id, "success", summary, hostname, "retention")
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


# Limited pool so scheduled fleet checks queue rather than all SSH at once.
from concurrent.futures import ThreadPoolExecutor

_update_check_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="upd-check")
# Apply jobs are heavier — serialize globally (one patch stream at a time)
_patch_apply_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="patch-apply")


def _parse_os_apply_steps(raw: str | None) -> list[str]:
    """Parse Server.os_apply_steps JSON; fall back to safe default."""
    default = ["update", "upgrade", "autoremove"]
    if not raw or not str(raw).strip():
        return default
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            steps = os_patching.normalize_os_patch_steps([str(x) for x in data])
            return steps or default
    except Exception:
        pass
    # comma-separated fallback
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    steps = os_patching.normalize_os_patch_steps(parts)
    return steps or default


def _active_job_of_type(session: Session, server_id: int, job_type: str) -> Job | None:
    return session.exec(
        select(Job)
        .where(Job.server_id == server_id)
        .where(Job.job_type == job_type)
        .where(Job.status.in_(["pending", "running"]))
        .order_by(Job.created_at.desc())
    ).first()


def enqueue_os_patch_apply(
    server_id: int,
    user_id: int | None = None,
    *,
    scheduled: bool = False,
    os_steps: list[str] | None = None,
) -> Job | None:
    """Create Job + AuditLog and run OS patch on the apply pool (scheduler-safe)."""
    with _get_fresh_session() as session:
        server = session.get(Server, server_id)
        if not server:
            return None
        if _active_job_of_type(session, server_id, "os_patch"):
            logger.info(f"[Jobs] OS apply skip — already active for server {server_id}")
            return None
        steps = os_steps
        if steps is None:
            steps = _parse_os_apply_steps(getattr(server, "os_apply_steps", None))
        steps = os_patching.normalize_os_patch_steps(steps) or ["update", "upgrade", "autoremove"]
        label = "Scheduled OS patch" if scheduled else "OS patch"
        job = Job(
            server_id=server.id,
            job_type="os_patch",
            status="pending",
            details=json.dumps({
                "current": "queued",
                "log_lines": [f"{label} queued…"],
                "scheduled": scheduled,
                "os_steps": steps,
                "done": False,
            }),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        audit = AuditLog(
            user_id=user_id,
            server_id=server.id,
            action="os_patch",
            status="running",
            details=(
                f"Job #{job.id} started · scheduled · {','.join(steps)}"
                if scheduled
                else f"Job #{job.id} started · {','.join(steps)}"
            ),
        )
        session.add(audit)
        session.commit()
        session.refresh(audit)
        jid, aid, sid, step_list = job.id, audit.id, server.id, list(steps)
    _patch_apply_pool.submit(_execute_os_patch_sync, jid, sid, aid, step_list)
    return job


def enqueue_container_patch_apply(
    server_id: int,
    user_id: int | None = None,
    *,
    scheduled: bool = False,
) -> Job | None:
    """Create Job + AuditLog and run container patch on the apply pool (scheduler-safe)."""
    with _get_fresh_session() as session:
        server = session.get(Server, server_id)
        if not server:
            return None
        if _active_job_of_type(session, server_id, "container_patch"):
            logger.info(f"[Jobs] Container apply skip — already active for server {server_id}")
            return None
        label = "Scheduled container patch" if scheduled else "Container patch"
        job = Job(
            server_id=server.id,
            job_type="container_patch",
            status="pending",
            details=json.dumps({
                "current": "queued",
                "log_lines": [f"{label} queued…"],
                "scheduled": scheduled,
                "done": False,
            }),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        audit = AuditLog(
            user_id=user_id,
            server_id=server.id,
            action="container_patch",
            status="running",
            details=(
                f"Job #{job.id} started · scheduled"
                if scheduled
                else f"Job #{job.id} started"
            ),
        )
        session.add(audit)
        session.commit()
        session.refresh(audit)
        jid, aid, sid = job.id, audit.id, server.id
    _patch_apply_pool.submit(_execute_container_patch_sync, jid, sid, aid)
    return job


def _execute_os_patch_sync(
    job_id: int, server_id: int, audit_id: int, os_steps: list[str] | None = None
) -> None:
    """Thread-pool entry for OS patch (mirrors async _run_os_patch_job without event loop)."""
    server, hostname = _load_server_for_job(server_id)
    with _get_fresh_session() as s:
        job = s.get(Job, job_id)
        if job:
            job.status = "running"
            job.started_at = datetime.utcnow()
            _merge_job_details(job, current="patching", log_line="OS patch started…", done=False)
            s.add(job)
            s.commit()
    if not server:
        _finish(audit_id, job_id, "failed", "Server not found", hostname, "os_patch")
        return
    try:
        os_patching.init_os_patch_progress(hostname, "starting scheduled patch…")
    except Exception:
        pass
    try:
        res = os_patching.run_os_patch(server, selected_steps=os_steps)
        status = "success" if os_patching.os_patch_succeeded(res) else "failed"
        post_check: dict | None = None
        if status == "success":
            try:
                os_patching._append_os_log(hostname, "[os] rechecking update counts…")
                with _get_fresh_session() as s:
                    j = s.get(Job, job_id)
                    if j:
                        _merge_job_details(j, current="rechecking", log_line="Rechecking OS updates…")
                        s.add(j)
                        s.commit()
                check = os_patching.check_os_updates(server)
                post_check = check if isinstance(check, dict) else None
                with _get_fresh_session() as s:
                    _apply_os_check_result(s, server_id, check)
            except Exception as e:
                logger.debug(f"post-patch os update refresh: {e}")
        try:
            os_patching.mark_os_patch_done(hostname, finished_ok=(status == "success"))
            os_patching._append_os_log(hostname, f"[os] all done — {status}")
        except Exception:
            pass
        res = os_patching.attach_audit_fields(res, hostname, post_check=post_check)
        _finish(audit_id, job_id, status, json.dumps(res), hostname, "os_patch")
    except Exception as e:
        try:
            os_patching.mark_os_patch_done(hostname, finished_ok=False)
            os_patching._append_os_log(hostname, f"[os] ERROR: {e}")
        except Exception:
            pass
        fail_payload = os_patching.attach_audit_fields(
            {"server": hostname, "error": str(e), "summary": f"Failed: {str(e)[:120]}"},
            hostname,
        )
        _finish(audit_id, job_id, "failed", json.dumps(fail_payload), hostname, "os_patch")


def _execute_container_patch_sync(job_id: int, server_id: int, audit_id: int) -> None:
    """Thread-pool entry for container patch (scheduler-safe)."""
    server, hostname = _load_server_for_job(server_id)
    with _get_fresh_session() as s:
        job = s.get(Job, job_id)
        if job:
            job.status = "running"
            job.started_at = datetime.utcnow()
            _merge_job_details(job, current="starting", log_line="Container patch started…", done=False)
            s.add(job)
            s.commit()
    if not server:
        _finish(audit_id, job_id, "failed", "Server not found", hostname, "container_patch")
        return
    try:
        container_patching.init_container_patch_progress(hostname, "starting scheduled container patch…")
    except Exception:
        pass

    def _on_progress(current: str, msg: str):
        _flush_container_progress_to_job(job_id, current, msg)

    try:
        res = container_patching.run_project_update(server, None, _on_progress)
        status = "success" if container_patching.container_patch_succeeded(res) else "failed"
        if status == "success":
            try:
                with _get_fresh_session() as s:
                    j = s.get(Job, job_id)
                    if j:
                        _merge_job_details(j, current="rechecking", log_line="Rechecking image updates…")
                        s.add(j)
                        s.commit()
                check = container_patching.check_all_projects_updates(server)
                with _get_fresh_session() as s:
                    _apply_container_check_result(s, server_id, check)
            except Exception as e:
                logger.debug(f"post-container-patch recheck: {e}")
        try:
            container_patching.mark_container_patch_done(hostname, finished_ok=(status == "success"))
            container_patching.append_container_log(hostname, f"[containers] all done — {status}")
        except Exception:
            pass
        if isinstance(res, dict) and not res.get("summary"):
            res["summary"] = container_patching.summarize_container_patch(res)
        snippet = json.dumps(res) if isinstance(res, dict) else str(res)
        _finish(audit_id, job_id, status, snippet, hostname, "container_patch")
    except Exception as e:
        try:
            container_patching.mark_container_patch_done(hostname, finished_ok=False)
            container_patching.append_container_log(hostname, f"[containers] ERROR: {e}")
        except Exception:
            pass
        _finish(
            audit_id,
            job_id,
            "failed",
            json.dumps({"error": str(e), "summary": f"Failed: {str(e)[:120]}"}),
            hostname,
            "container_patch",
        )


def enqueue_os_update_check(server_id: int, user_id: int | None = None) -> Job | None:
    """Create pending Job + AuditLog, run SSH check on a worker thread (queued)."""
    with _get_fresh_session() as session:
        server = session.get(Server, server_id)
        if not server:
            return None
        job = Job(
            server_id=server.id,
            job_type="os_update_check",
            status="pending",
            details=json.dumps({"current": "queued", "log_lines": ["OS update check queued…"]}),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        audit = AuditLog(
            user_id=user_id,
            server_id=server.id,
            action="os_update_check",
            status="running",
            details=f"Job #{job.id} queued",
        )
        session.add(audit)
        session.commit()
        session.refresh(audit)
        jid, aid, sid = job.id, audit.id, server.id
    _update_check_pool.submit(_execute_os_update_check, jid, sid, aid)
    return job


def enqueue_container_update_check(server_id: int, user_id: int | None = None) -> Job | None:
    """Create pending Job + AuditLog, run fleet image check on a worker thread (queued)."""
    with _get_fresh_session() as session:
        server = session.get(Server, server_id)
        if not server:
            return None
        job = Job(
            server_id=server.id,
            job_type="container_update_check",
            status="pending",
            details=json.dumps({"current": "queued", "log_lines": ["Container update check queued…"]}),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        audit = AuditLog(
            user_id=user_id,
            server_id=server.id,
            action="container_update_check",
            status="running",
            details=f"Job #{job.id} queued",
        )
        session.add(audit)
        session.commit()
        session.refresh(audit)
        jid, aid, sid = job.id, audit.id, server.id
    _update_check_pool.submit(_execute_container_update_check, jid, sid, aid)
    return job


def run_os_update_check_now(session: Session, server: Server, user_id: int | None = None) -> Job:
    """Scheduler-friendly: enqueue and return pending job (does not block SSH)."""
    job = enqueue_os_update_check(server.id, user_id=user_id)
    if not job:
        raise RuntimeError(f"Could not enqueue OS check for server {server.id}")
    # Refresh from caller's session if same engine
    return session.get(Job, job.id) or job


def run_container_update_check_now(session: Session, server: Server, user_id: int | None = None) -> Job:
    """Scheduler-friendly: enqueue and return pending job (does not block SSH)."""
    job = enqueue_container_update_check(server.id, user_id=user_id)
    if not job:
        raise RuntimeError(f"Could not enqueue container check for server {server.id}")
    return session.get(Job, job.id) or job


def _execute_os_update_check(job_id: int, server_id: int, audit_id: int) -> None:
    with _get_fresh_session() as session:
        job = session.get(Job, job_id)
        server = session.get(Server, server_id)
        if job:
            job.status = "running"
            job.started_at = datetime.utcnow()
            _merge_job_details(
                job,
                current="checking",
                log_lines=["OS update check started…", "Refreshing apt lists & simulating upgrade…"],
                done=False,
            )
            session.add(job)
            session.commit()
        if not server:
            _finish(audit_id, job_id, "failed", "Server not found", "", "os_update_check")
            return
        hostname = server.hostname
        try:
            res = os_patching.check_os_updates(server)
            _apply_os_check_result(session, server.id, res)
            status = "failed" if res.get("error") and res.get("updates_count") is None else "success"
            _finish(audit_id, job_id, status, json.dumps(res), hostname, "os_update_check")
        except Exception as e:
            _finish(audit_id, job_id, "failed", str(e), hostname, "os_update_check")


def _execute_container_update_check(job_id: int, server_id: int, audit_id: int) -> None:
    with _get_fresh_session() as session:
        job = session.get(Job, job_id)
        server = session.get(Server, server_id)
        if job:
            job.status = "running"
            job.started_at = datetime.utcnow()
            _merge_job_details(
                job,
                current="checking",
                log_lines=["Container image check started…", "Scanning compose projects…"],
                done=False,
            )
            session.add(job)
            session.commit()
        if not server:
            _finish(audit_id, job_id, "failed", "Server not found", "", "container_update_check")
            return
        hostname = server.hostname
        try:
            res = container_patching.check_all_projects_updates(server)
            _apply_container_check_result(session, server.id, res)
            _finish(audit_id, job_id, "success", json.dumps(res), hostname, "container_update_check")
        except Exception as e:
            _finish(audit_id, job_id, "failed", str(e), hostname, "container_update_check")


def _apply_os_check_result(session: Session, server_id: int, res: dict) -> None:
    server = session.get(Server, server_id)
    if not server:
        return
    server.last_os_check_at = datetime.utcnow()
    if res.get("supported", True) and res.get("updates_count") is not None:
        # updates_count is actionable only (excludes Ubuntu phased-only packages)
        server.os_updates_count = int(res.get("updates_count") or 0)
    server.reboot_pending = bool(res.get("reboot_pending"))
    sample = res.get("packages_sample") or []
    phased_sample = res.get("phased_sample") or []
    server.os_updates_summary = json.dumps({
        "packages_sample": sample[:15],
        "phased_sample": phased_sample[:15],
        "actionable_count": res.get("actionable_count", res.get("updates_count")),
        "phased_count": res.get("phased_count") or 0,
        "total_upgradable": res.get("total_upgradable"),
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
            phased_count=int(res.get("phased_count") or 0),
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
        "project_details": res.get("project_details") or {},
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
    server, hostname = _load_server_for_job(server_id)
    if not server:
        _finish(audit_id, job_id, "failed", "Server not found", hostname, "os_update_check")
        return
    try:
        res = await run_in_threadpool(os_patching.check_os_updates, server)
        with _get_fresh_session() as s:
            _apply_os_check_result(s, server_id, res)
        status = "failed" if res.get("error") and res.get("updates_count") is None else "success"
        _finish(audit_id, job_id, status, json.dumps(res), hostname, "os_update_check")
    except Exception as e:
        _finish(audit_id, job_id, "failed", str(e), hostname, "os_update_check")


async def _run_container_update_check_job(job_id: int, server_id: int, audit_id: int):
    server, hostname = _load_server_for_job(server_id)
    if not server:
        _finish(audit_id, job_id, "failed", "Server not found", hostname, "container_update_check")
        return
    try:
        res = await run_in_threadpool(container_patching.check_all_projects_updates, server)
        with _get_fresh_session() as s:
            _apply_container_check_result(s, server_id, res)
        _finish(audit_id, job_id, "success", json.dumps(res), hostname, "container_update_check")
    except Exception as e:
        _finish(audit_id, job_id, "failed", str(e), hostname, "container_update_check")

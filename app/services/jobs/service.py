# app/services/jobs.py
"""
Fleet job queue: create Job rows, run work, stream progress for UI polling.

Execution paths (do not merge without care):
- UI / BackgroundTasks: create_job_and_run → async _run_*_job
- Scheduler / thread pool: enqueue_*_apply / enqueue_*_update_check → _execute_*_sync
- Backups: Celery only (enqueue_backup_for_server / create_job_and_run backup branch)

Shared helpers: _initial_job_details, _merge_job_details, _flush_job_progress,
_create_queued_job_with_audit, _finish, job_public_dict.
"""
from fastapi import BackgroundTasks
from sqlmodel import Session, select
from ...database import engine
from ...models import Job, AuditLog, Server
from datetime import datetime, timedelta
import json
import re
import httpx
from ...config import settings
from .. import backup, container_patching, os_patching, herder_backup
from ..backup_audit import record_backup_audit_event, record_backup_audit_from_job
from ..app_settings import utc_isoformat
from ..audit_write import make_audit_log, resolve_client_ip
from ..request_ip import get_request_client_ip
import logging
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

# Placeholder written when a non-backup job starts; replaced on _finish with a summary
_JOB_STARTED_RE = re.compile(r"^Job #\d+ started")


class BackupAlreadyRunning(Exception):
    def __init__(self, job: Job):
        self.job = job


class JobAlreadyActive(Exception):
    """Raised when an exclusive job type is already pending/running for the server.

    Container/OS patch and update-check jobs must not stack on the same host.
    Celery multi-worker concurrency only applies to backups; these job types run
    on the web process (BackgroundTasks / in-process thread pools).
    """

    def __init__(self, job: Job):
        self.job = job


# One active job of each type per server (UI, API, and scheduler share this rule)
_EXCLUSIVE_JOB_TYPES = frozenset(
    {
        "os_patch",
        "container_patch",
        "os_update_check",
        "container_update_check",
        "docker_stack_check",
        "docker_stack_deploy",
        "docker_stack_stop",
        "docker_stack_start",
        "docker_stack_restart",
        "template_deploy",
        "template_redeploy",
    }
)

# Host-level stack mutations share a single exclusive lane (compose write/up)
_STACK_MUTATING_JOB_TYPES = frozenset(
    {
        "docker_stack_deploy",
        "docker_stack_stop",
        "docker_stack_start",
        "docker_stack_restart",
        "template_deploy",
        "template_redeploy",
    }
)

# Whole-project lifecycle: compose stop / start / restart (no pull/up)
_STACK_LIFECYCLE_ACTIONS = frozenset({"stop", "start", "restart"})
_STACK_LIFECYCLE_JOB_TYPES = frozenset(
    {f"docker_stack_{a}" for a in _STACK_LIFECYCLE_ACTIONS}
)


# Import Celery task for backup jobs (integrated path).
# This file lives in app.services.jobs — use three dots to reach app.tasks.
try:
    from ...tasks import backup_server
    HAS_CELERY = True
except Exception as e:
    HAS_CELERY = False
    backup_server = None
    logger.warning("Celery backup task unavailable (backups will not enqueue): %s", e)


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
        from ...celery_app import celery
        celery.control.revoke(task_id, terminate=True, signal="SIGTERM")
        logger.info(f"[Jobs] Revoked celery task {task_id}")
    except Exception as e:
        logger.warning(f"[Jobs] Failed to revoke celery task {task_id}: {e}")


def _mark_job_terminal(
    job: Job,
    message: str,
    session: Session,
    *,
    status: str = "failed",
    record_audit: bool = True,
) -> None:
    """Mark job finished (failed / cancelled). Shared by stop, cancel, supersede, stale."""
    job.status = status
    job.finished_at = datetime.utcnow()
    details = {}
    if job.details:
        try:
            details = json.loads(job.details)
        except Exception:
            pass
    details["error"] = message
    details["current"] = status
    details["status"] = status
    details["summary"] = message[:200]
    details["done"] = True
    if status == "cancelled":
        details["cancelled"] = True
        details["cancel_requested"] = True
    details["audit_failed_recorded"] = True
    lines = list(details.get("log_lines") or [])
    lines.append(message[:240])
    details["log_lines"] = lines[-15:]
    job.details = json.dumps(details)
    session.add(job)
    if record_audit and job.job_type == "backup":
        phase = "cancelled" if status == "cancelled" else "failed"
        try:
            record_backup_audit_from_job(
                session,
                job,
                phase,
                message=message,
                output_snippet={"error": message, "status": status},
            )
        except ValueError:
            # Older phase map without cancelled → fall back to failed audit row
            record_backup_audit_from_job(
                session,
                job,
                "failed",
                message=message,
                output_snippet={"error": message, "status": status},
            )


def _mark_job_failed(
    job: Job, message: str, session: Session, *, record_audit: bool = True
) -> None:
    _mark_job_terminal(
        job, message, session, status="failed", record_audit=record_audit
    )


def _mark_job_cancelled(
    job: Job, message: str, session: Session, *, record_audit: bool = True
) -> None:
    _mark_job_terminal(
        job, message, session, status="cancelled", record_audit=record_audit
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
    "docker_stack_check": "Stack check",
    "docker_stack_deploy": "Stack deploy",
    "docker_stack_stop": "Stack stop",
    "docker_stack_start": "Stack start",
    "docker_stack_restart": "Stack restart",
    "template_deploy": "Template deploy",
    "template_redeploy": "Template redeploy",
    "retention": "Retention",
    "diagnostics": "Diagnostics",
    "herder_backup": "PiHerder backup",
    "pihole_action": "Pi-hole action",
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
        "created_at": utc_isoformat(job.created_at),
        "started_at": utc_isoformat(job.started_at),
        "finished_at": utc_isoformat(job.finished_at),
        "current": details.get("current"),
        "summary": summary,
        "scheduled": bool(details.get("scheduled")),
        "log_tail": list(log_lines)[-tail_n:] if log_lines else [],
        "done": job.status in ("success", "failed", "cancelled"),
        "cancellable": job.status in ("pending", "running"),
        "os_steps": details.get("os_steps"),
        "error": details.get("error"),
        "redirect_url": details.get("redirect_url"),
        "deployment_id": details.get("deployment_id"),
    }
    if detail:
        # Full log for JobHold / jobs modal (alias log_lines for poll UIs)
        out["log_lines"] = list(log_lines) if log_lines else []
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
    _mark_job_cancelled(job, "Stopped by user", session)
    session.commit()
    return job


def stop_active_backup(session: Session, server: Server, job: Job | None = None) -> Job | None:
    """Stop the running backup, or a specific job when provided."""
    target = job or get_running_backup_job(session, server.id) or get_active_backup_job(session, server.id)
    if not target:
        backup.stop_backup(server.hostname)
        return None
    return stop_backup_job(session, server, target)


class JobNotCancellable(Exception):
    """Raised when cancel is requested for a terminal or missing job."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def cancel_job(
    session: Session,
    job: Job,
    *,
    user_id: int | None = None,
    message: str = "Cancelled by user",
) -> Job:
    """Cancel a pending/running job from the Jobs UI (or API).

    - Backup: stop rsync + revoke Celery task (same as stop_backup_job).
    - Other types: mark cancelled + revoke Celery if any; in-flight
      BackgroundTasks may still run briefly — ``_finish`` will not overwrite
      cancelled status.
    """
    if not job:
        raise JobNotCancellable("Job not found")
    if job.status not in ("pending", "running"):
        raise JobNotCancellable(f"Job is already {job.status}")

    msg = (message or "Cancelled by user").strip()[:240] or "Cancelled by user"

    if job.job_type == "backup":
        if job.server_id:
            server = session.get(Server, job.server_id)
            if server and job.status == "running":
                try:
                    backup.stop_backup(server.hostname)
                except Exception as e:
                    logger.warning(f"[Jobs] stop_backup during cancel: {e}")
        _revoke_celery_task(job.celery_task_id)
        _mark_job_cancelled(job, msg, session)
    else:
        _revoke_celery_task(job.celery_task_id)
        # Best-effort: stop hostname-scoped progress markers for patches
        if job.server_id:
            try:
                server = session.get(Server, job.server_id)
                hostname = (server.hostname if server else None) or ""
                if hostname and job.job_type == "os_patch":
                    try:
                        os_patching._append_os_log(hostname, f"[os] {msg}")
                    except Exception:
                        pass
                if hostname and job.job_type == "container_patch":
                    try:
                        container_patching.append_container_log(
                            hostname, f"[containers] {msg}"
                        )
                    except Exception:
                        pass
            except Exception:
                pass
        _mark_job_cancelled(job, msg, session, record_audit=False)

    # Fleet audit row for cancel action (jobs screen / operators)
    try:
        # Prefer request IP; fall back to IP stored when the job was created
        job_ip = None
        try:
            job_ip = (json.loads(job.details or "{}") or {}).get("client_ip")
        except Exception:
            job_ip = None
        audit = make_audit_log(
            user_id=user_id,
            server_id=job.server_id,
            action="job_cancel",
            status="cancelled",
            details=json.dumps(
                {
                    "job_id": job.id,
                    "job_type": job.job_type,
                    "message": msg,
                }
            ),
            finished_at=datetime.utcnow(),
            client_ip=resolve_client_ip(None, fallback=job_ip),
        )
        session.add(audit)
    except Exception as e:
        logger.debug(f"job_cancel audit: {e}")

    session.commit()
    session.refresh(job)
    logger.info(f"[Jobs] Cancelled job #{job.id} ({job.job_type}) by user {user_id}")
    return job


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
    api_token_id: int | None = None,
    api_token_name: str | None = None,
):
    server_id = server.id if server is not None else None
    actor_extra: dict = {}
    if user_id is not None:
        actor_extra["user_id"] = user_id
    if api_token_id is not None:
        actor_extra["api_token_id"] = api_token_id
    if api_token_name:
        actor_extra["api_token_name"] = str(api_token_name)[:120]
    if job_type == "backup":
        cleanup_stale_backup_jobs(session)
        if server_id:
            active = get_active_job_for_source(session, server_id, source_filter)
            if active:
                raise BackupAlreadyRunning(active)
    elif job_type in _EXCLUSIVE_JOB_TYPES and server_id is not None:
        active = _active_job_of_type(session, server_id, job_type)
        if active:
            logger.info(
                f"[Jobs] {job_type} skip — job #{active.id} already active "
                f"for server {server_id}"
            )
            raise JobAlreadyActive(active)
    job = Job(server_id=server_id, job_type=job_type, status="pending")
    if job_type == "backup":
        job.details = _initial_job_details(
            "Backup queued…",
            source_filter=source_filter,
            queued_at=datetime.utcnow().isoformat(),
            **actor_extra,
        )
    else:
        labels = {
            "os_patch": "OS patch queued…",
            "container_patch": "Container patch queued…",
            "retention": "Retention cleanup queued…",
            "os_update_check": "OS update check queued…",
            "container_update_check": "Container update check queued…",
            "docker_stack_check": "Stack update check queued…",
            "docker_stack_deploy": "Stack deploy queued…",
            "docker_stack_stop": "Stack stop queued…",
            "docker_stack_start": "Stack start queued…",
            "docker_stack_restart": "Stack restart queued…",
        }
        job.details = _initial_job_details(
            labels.get(job_type, f"{job_type} queued…"),
            **actor_extra,
        )
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
            api_token_id=api_token_id,
            api_token_name=api_token_name,
            source_filter=source_filter,
            message=f"Backup requested for {src_label}",
        )
        record_backup_audit_event(
            session,
            server_id=server_id,
            job_id=job.id,
            phase="queued",
            user_id=user_id,
            api_token_id=api_token_id,
            api_token_name=api_token_name,
            source_filter=source_filter,
            message="Waiting for worker",
        )
        session.commit()
        if not HAS_CELERY or not backup_server:
            msg = "Celery worker required for backups — start celery-worker container"
            _mark_job_terminal(job, msg, session, status="failed", record_audit=True)
            session.commit()
            raise RuntimeError(msg)
        try:
            logger.info(f"[Jobs] Enqueuing backup job #{job.id} for server {server_id} to Celery")
            async_result = backup_server.delay(
                server.id,
                job_id=job.id,
                source_filter=source_filter,
            )
            job.celery_task_id = async_result.id
            session.add(job)
            session.commit()
        except Exception as e:
            msg = f"Failed to enqueue backup to Celery: {e}"
            logger.exception("[Jobs] %s", msg)
            _mark_job_terminal(job, msg, session, status="failed", record_audit=True)
            session.commit()
            raise RuntimeError(msg) from e
    else:
        start_details = f"Job #{job.id} started"
        if job_type == "os_patch" and os_steps:
            start_details = f"Job #{job.id} started · {','.join(os_steps)}"
        audit = make_audit_log(
            user_id=user_id,
            server_id=server_id,
            api_token_id=api_token_id,
            api_token_name=api_token_name,
            action=job_type,
            status="running",
            details=start_details,
            client_ip=resolve_client_ip(
                None,
                fallback=(json.loads(job.details or "{}") or {}).get("client_ip")
                if job.details
                else None,
            ),
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
    elif job_type == "docker_stack_check":
        project_path = (source_filter or "").strip()  # reuse source_filter slot for path
        background_tasks.add_task(
            _run_docker_stack_check_job, job.id, server.id, audit.id, project_path
        )
    elif job_type == "docker_stack_deploy":
        project_path = (source_filter or "").strip()
        background_tasks.add_task(
            _run_docker_stack_deploy_job, job.id, server.id, audit.id, project_path, True
        )
    elif job_type in _STACK_LIFECYCLE_JOB_TYPES:
        project_path = (source_filter or "").strip()
        action = job_type.replace("docker_stack_", "", 1)
        background_tasks.add_task(
            _run_docker_stack_lifecycle_job,
            job.id,
            server.id,
            audit.id,
            project_path,
            action,
        )
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
    job.details = _initial_job_details(
        "Backup queued…",
        source_filter=source_filter,
        user_id=user_id,
        queued_at=datetime.utcnow().isoformat(),
    )
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
        msg = "Celery worker required for backups"
        _mark_job_terminal(job, msg, session, status="failed", record_audit=True)
        session.commit()
        raise RuntimeError(msg)

    try:
        async_result = backup_server.delay(
            server.id,
            job_id=job.id,
            source_filter=source_filter,
        )
        job.celery_task_id = async_result.id
        session.add(job)
        session.commit()
        session.refresh(job)
    except Exception as e:
        msg = f"Failed to enqueue backup to Celery: {e}"
        logger.exception("[Jobs] %s", msg)
        _mark_job_terminal(job, msg, session, status="failed", record_audit=True)
        session.commit()
        raise RuntimeError(msg) from e
    return job


def _initial_job_details(queue_message: str, **extra) -> str:
    """JSON for a newly queued job (UI poll shape).

    Always snapshot ``client_ip`` from the current request (when present) so
    Celery/background audit rows can attribute the original operator IP.
    """
    data = {
        "current": "queued",
        "log_lines": [queue_message],
        "done": False,
    }
    data.update(extra)
    if not data.get("client_ip"):
        ip = get_request_client_ip()
        if ip:
            data["client_ip"] = ip
    return json.dumps(data)


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


def _flush_job_progress(
    job_id: int, current: str, log_line: str, *, default_current: str = "running"
) -> None:
    """Best-effort live Job.details update for JobHold / progress polling."""
    try:
        with _get_fresh_session() as s:
            job = s.get(Job, job_id)
            if not job or job.status not in ("pending", "running"):
                return
            _merge_job_details(
                job,
                current=current or default_current,
                log_line=log_line,
                done=False,
            )
            s.add(job)
            s.commit()
    except Exception as e:
        logger.debug(f"job progress flush: {e}")


def _create_queued_job_with_audit(
    session: Session,
    *,
    server_id: int | None,
    job_type: str,
    queue_message: str,
    user_id: int | None = None,
    api_token_id: int | None = None,
    api_token_name: str | None = None,
    audit_details: str | None = None,
    **details_extra,
) -> tuple[Job, AuditLog]:
    """Insert pending Job + running AuditLog; commit both. Caller starts work.

    ``audit_details`` may include ``{job_id}`` which is filled after the Job row exists.
    """
    if user_id is not None and "user_id" not in details_extra:
        details_extra["user_id"] = user_id
    if api_token_id is not None and "api_token_id" not in details_extra:
        details_extra["api_token_id"] = api_token_id
    if api_token_name and "api_token_name" not in details_extra:
        details_extra["api_token_name"] = str(api_token_name)[:120]
    job = Job(
        server_id=server_id,
        job_type=job_type,
        status="pending",
        details=_initial_job_details(queue_message, **details_extra),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    if audit_details is None:
        details = f"Job #{job.id} started"
    else:
        details = audit_details.format(job_id=job.id)
    try:
        job_ip = (json.loads(job.details or "{}") or {}).get("client_ip")
    except Exception:
        job_ip = None
    audit = make_audit_log(
        user_id=user_id,
        server_id=server_id,
        api_token_id=api_token_id,
        api_token_name=api_token_name,
        action=job_type,
        status="running",
        details=details,
        client_ip=resolve_client_ip(None, fallback=job_ip),
    )
    session.add(audit)
    session.commit()
    session.refresh(audit)
    return job, audit


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
    if job_type == "docker_stack_check" and isinstance(data, dict):
        proj = data.get("project") or data.get("project_path") or "stack"
        if data.get("has_updates"):
            imgs = data.get("updated_images") or []
            return f"{proj}: updates available ({len(imgs)} image(s))"
        if data.get("success"):
            return f"{proj}: images up to date"
        return f"{proj}: check failed"
    if job_type == "docker_stack_deploy" and isinstance(data, dict):
        proj = data.get("project") or data.get("project_path") or "stack"
        if data.get("success"):
            return f"{proj}: deploy ok"
        err = data.get("error") or status
        return f"{proj}: deploy failed — {err}"[:200]
    if job_type in _STACK_LIFECYCLE_JOB_TYPES and isinstance(data, dict):
        proj = data.get("project") or data.get("project_path") or "stack"
        act = data.get("action") or job_type.replace("docker_stack_", "", 1)
        if data.get("success") or status == "success":
            return f"{proj}: {act} ok"
        err = data.get("error") or status
        return f"{proj}: {act} failed — {err}"[:200]
    if job_type in ("template_deploy", "template_redeploy") and isinstance(data, dict):
        proj = data.get("project_name") or data.get("project") or "template"
        slug = data.get("template_slug") or ""
        label = f"{slug}/{proj}" if slug else proj
        if data.get("success") or status == "success":
            ver = data.get("config_version")
            return f"{label}: ok" + (f" (V{ver})" if ver is not None else "")
        err = data.get("error") or status
        return f"{label}: failed — {err}"[:200]
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
    jt = job_type or ""
    with _get_fresh_session() as s:
        audit = s.get(AuditLog, audit_id)
        job = s.get(Job, job_id)
        jt = job_type or (job.job_type if job else "") or ""
        if job and job.server_id:
            server_id = job.server_id
        # Honour user cancel — do not overwrite cancelled jobs when worker finishes
        if job and job.status == "cancelled":
            if audit and audit.status == "running":
                audit.status = "cancelled"
                audit.finished_at = datetime.utcnow()
                if not audit.details or _JOB_STARTED_RE.search(audit.details or ""):
                    audit.details = f"Job #{job_id} · Cancelled by user"[:500]
                s.add(audit)
                s.commit()
            return
        summary = _human_job_summary(jt, status, snippet)
        if audit:
            audit.status = status
            # OS patch stores JSON + apt log_tail; allow a larger snippet than default
            max_snip = 16000 if jt == "os_patch" else 2000
            audit.output_snippet = (snippet or "")[:max_snip]
            audit.finished_at = datetime.utcnow()
            # Backfill request IP from job.details when audit was started without context
            if not getattr(audit, "client_ip", None) and job and job.details:
                try:
                    jip = (json.loads(job.details or "{}") or {}).get("client_ip")
                    if jip:
                        audit.client_ip = str(jip)[:64]
                except Exception:
                    pass
            # Replace "Job #N started" with a scannable finished line for the audit list
            if jt == "os_patch" and summary:
                audit.details = f"Job #{job_id} · {summary}"[:500]
            elif (
                status in ("success", "failed", "cancelled")
                and summary
                and (not audit.details or _JOB_STARTED_RE.search(audit.details or ""))
            ):
                audit.details = f"Job #{job_id} · {summary}"[:500]
            s.add(audit)
        if job:
            # Already terminal (e.g. Celery wrote success first) — still run
            # notification resolve below; only skip re-writing the job row.
            already_done = (
                job.status in ("success", "failed", "cancelled") and job.finished_at
            )
            if not already_done:
                job.status = status
                job.finished_at = datetime.utcnow()
                merge_kw: dict = {
                    "current": None if status in ("success", "failed", "cancelled") else status,
                    "status": status,
                    "summary": summary,
                    "result_snippet": (snippet or "")[:1500],
                    "log_line": f"[{status}] {summary}",
                    "done": True,
                }
                # Surface navigation targets from JSON snippets (template deploy, etc.)
                try:
                    snip_data = (
                        json.loads(snippet)
                        if snippet and str(snippet).strip().startswith(("{", "["))
                        else None
                    )
                except Exception:
                    snip_data = None
                if isinstance(snip_data, dict):
                    if snip_data.get("redirect_url"):
                        merge_kw["redirect_url"] = str(snip_data["redirect_url"])[:400]
                    if snip_data.get("deployment_id") is not None:
                        merge_kw["deployment_id"] = snip_data.get("deployment_id")
                _merge_job_details(job, **merge_kw)
                s.add(job)
        s.commit()

        # Backup success/failure notifications (best-effort). Use jt (resolved
        # type), not only the job_type arg — and run even when job row was
        # already terminal so a successful run still clears backup_failed.
        if jt == "backup" and server_id:
            try:
                from ..notifications import notify_backup_failed, resolve_backup_failed
                server = s.get(Server, server_id)
                name = server.name if server else hostname
                if status == "failed":
                    notify_backup_failed(
                        s, server_id, name or str(server_id), (snippet or "")[:300]
                    )
                elif status == "success":
                    resolve_backup_failed(s, server_id)
            except Exception as e:
                logger.debug(f"backup notification: {e}")

    if hostname and jt:
        _send_summary_webhook(hostname, jt, status, snippet)


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


# Back-compat alias
def _flush_container_progress_to_job(job_id: int, current: str, log_line: str) -> None:
    _flush_job_progress(job_id, current, log_line, default_current="patching")


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
        _flush_job_progress(job_id, current, msg, default_current="patching")

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
                _flush_job_progress(
                    job_id,
                    "rechecking",
                    f"[containers] {n} project(s) still have updates",
                    default_current="patching",
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
        steps_s = ",".join(steps)
        audit_details = (
            f"Job #{{job_id}} started · scheduled · {steps_s}"
            if scheduled
            else f"Job #{{job_id}} started · {steps_s}"
        )
        job, audit = _create_queued_job_with_audit(
            session,
            server_id=server.id,
            job_type="os_patch",
            queue_message=f"{label} queued…",
            user_id=user_id,
            audit_details=audit_details,
            scheduled=scheduled,
            os_steps=steps,
        )
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
        audit_details = (
            "Job #{job_id} started · scheduled" if scheduled else "Job #{job_id} started"
        )
        job, audit = _create_queued_job_with_audit(
            session,
            server_id=server.id,
            job_type="container_patch",
            queue_message=f"{label} queued…",
            user_id=user_id,
            audit_details=audit_details,
            scheduled=scheduled,
        )
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
        _flush_job_progress(job_id, current, msg, default_current="patching")

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
    """Create pending Job + AuditLog, run SSH check on a worker thread (queued).

    Returns the existing active job if an OS check is already pending/running
    (does not start a second concurrent check on the same host).
    """
    with _get_fresh_session() as session:
        server = session.get(Server, server_id)
        if not server:
            return None
        active = _active_job_of_type(session, server_id, "os_update_check")
        if active:
            logger.info(
                f"[Jobs] OS check skip — job #{active.id} already active for server {server_id}"
            )
            return active
        job, audit = _create_queued_job_with_audit(
            session,
            server_id=server.id,
            job_type="os_update_check",
            queue_message="OS update check queued…",
            user_id=user_id,
            audit_details="Job #{job_id} queued",
        )
        jid, aid, sid = job.id, audit.id, server.id
    _update_check_pool.submit(_execute_os_update_check, jid, sid, aid)
    return job


def enqueue_container_update_check(server_id: int, user_id: int | None = None) -> Job | None:
    """Create pending Job + AuditLog, run fleet image check on a worker thread (queued).

    Returns the existing active job if a container check is already pending/running
    (does not start a second concurrent check on the same host).
    """
    with _get_fresh_session() as session:
        server = session.get(Server, server_id)
        if not server:
            return None
        active = _active_job_of_type(session, server_id, "container_update_check")
        if active:
            logger.info(
                f"[Jobs] Container check skip — job #{active.id} already active "
                f"for server {server_id}"
            )
            return active
        job, audit = _create_queued_job_with_audit(
            session,
            server_id=server.id,
            job_type="container_update_check",
            queue_message="Container update check queued…",
            user_id=user_id,
            audit_details="Job #{job_id} queued",
        )
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
        from ..notifications import notify_os_updates
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
        from ..notifications import notify_container_updates
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


# ---------- B07: per-stack Docker check / deploy as Jobs + live log ----------


def _project_basename(project_path: str) -> str:
    import os

    return os.path.basename((project_path or "").rstrip("/")) or (project_path or "project")


def _active_docker_stack_job(
    session: Session, server_id: int, job_type: str, project_path: str
) -> Job | None:
    """Find active stack job for same host + project path (or any if path empty)."""
    want = (project_path or "").strip()
    want_base = _project_basename(want) if want else ""
    rows = session.exec(
        select(Job)
        .where(Job.server_id == server_id)
        .where(Job.job_type == job_type)
        .where(Job.status.in_(["pending", "running"]))
        .order_by(Job.created_at.desc())
    ).all()
    for job in rows:
        if not want:
            return job
        try:
            data = json.loads(job.details or "{}")
        except Exception:
            data = {}
        path = (data.get("project_path") or data.get("source_filter") or "").strip()
        if path == want or _project_basename(path) == want_base:
            return job
    return None


def enqueue_docker_stack_check(
    server_id: int,
    project_path: str,
    user_id: int | None = None,
    *,
    background_tasks: BackgroundTasks | None = None,
) -> Job:
    """Queue a per-project compose image check (B07).

    Raises JobAlreadyActive if a stack check is already pending/running on this host.
    """
    path = (project_path or "").strip()
    if not path:
        raise ValueError("project_path required")
    with _get_fresh_session() as session:
        server = session.get(Server, server_id)
        if not server:
            raise ValueError("server not found")
        active = _active_docker_stack_job(session, server_id, "docker_stack_check", path)
        if not active:
            active = _active_job_of_type(session, server_id, "docker_stack_check")
        if active:
            logger.info(
                f"[Jobs] docker_stack_check skip — job #{active.id} already active "
                f"for server {server_id}"
            )
            session.expunge(active)
            raise JobAlreadyActive(active)
        proj = _project_basename(path)
        job, audit = _create_queued_job_with_audit(
            session,
            server_id=server.id,
            job_type="docker_stack_check",
            queue_message=f"Stack check queued for {proj}…",
            user_id=user_id,
            audit_details=f"Job #{{job_id}} · check {proj}",
            project_path=path,
            project=proj,
        )
        jid, aid, sid = job.id, audit.id, server.id
    if background_tasks is not None:
        background_tasks.add_task(_run_docker_stack_check_job, jid, sid, aid, path)
    else:
        _update_check_pool.submit(_execute_docker_stack_check, jid, sid, aid, path)
    with _get_fresh_session() as session:
        job = session.get(Job, jid)
        if job:
            session.expunge(job)
        return job


def enqueue_docker_stack_deploy(
    server_id: int,
    project_path: str,
    *,
    pull: bool = True,
    user_id: int | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> Job:
    """Queue a per-project compose deploy (pull + up) as a Job (B07).

    Raises JobAlreadyActive if a stack deploy is already pending/running on this host.
    """
    path = (project_path or "").strip()
    if not path:
        raise ValueError("project_path required")
    with _get_fresh_session() as session:
        server = session.get(Server, server_id)
        if not server:
            raise ValueError("server not found")
        active = _active_docker_stack_job(session, server_id, "docker_stack_deploy", path)
        if not active:
            active = _active_stack_mutating_job(session, server_id)
        if active:
            logger.info(
                f"[Jobs] docker_stack_deploy skip — job #{active.id} already active "
                f"for server {server_id}"
            )
            session.expunge(active)
            raise JobAlreadyActive(active)
        proj = _project_basename(path)
        job, audit = _create_queued_job_with_audit(
            session,
            server_id=server.id,
            job_type="docker_stack_deploy",
            queue_message=f"Stack deploy queued for {proj}…",
            user_id=user_id,
            audit_details=f"Job #{{job_id}} · deploy {proj}",
            project_path=path,
            project=proj,
            pull=bool(pull),
        )
        jid, aid, sid = job.id, audit.id, server.id
        do_pull = bool(pull)
    if background_tasks is not None:
        background_tasks.add_task(
            _run_docker_stack_deploy_job, jid, sid, aid, path, do_pull
        )
    else:
        _update_check_pool.submit(
            _execute_docker_stack_deploy, jid, sid, aid, path, do_pull
        )
    with _get_fresh_session() as session:
        job = session.get(Job, jid)
        if job:
            session.expunge(job)
        return job


def _append_output_log_lines(job_id: int, current: str, output: str, *, prefix: str = "") -> None:
    """Split command output into a few progress log lines for JobHold."""
    text = (output or "").strip()
    if not text:
        return
    lines = [ln for ln in text.splitlines() if ln.strip()][-20:]
    for ln in lines:
        msg = f"{prefix}{ln}" if prefix else ln
        _flush_job_progress(job_id, current, msg[:300], default_current=current)


def _apply_single_project_check_result(
    session: Session, server_id: int, project_path: str, result: dict
) -> None:
    """Merge one-project check into Server.container_updates_summary (same as old route)."""
    import os

    server = session.get(Server, server_id)
    if not server:
        return
    proj_name = os.path.basename((project_path or "").rstrip("/")) or project_path
    summary: dict = {}
    if server.container_updates_summary:
        try:
            summary = json.loads(server.container_updates_summary) or {}
        except Exception:
            summary = {}
    projects = list(summary.get("projects") or [])
    details = dict(summary.get("project_details") or {})
    if result.get("has_updates"):
        if proj_name not in projects:
            projects.append(proj_name)
        details[proj_name] = {"images": list(result.get("updated_images") or [])}
    else:
        projects = [p for p in projects if p != proj_name]
        details.pop(proj_name, None)
    server.container_updates_summary = json.dumps(
        {
            "projects": projects,
            "project_details": details,
            "failed": summary.get("failed") or [],
            "checked": summary.get("checked") or [],
        }
    )
    server.container_updates_count = len(projects)
    server.last_container_check_at = datetime.utcnow()
    session.add(server)
    session.commit()
    try:
        from ..notifications import notify_container_updates

        notify_container_updates(
            session,
            server_id=server.id,
            server_name=server.name or proj_name,
            projects=projects,
        )
    except Exception as e:
        logger.debug(f"notify_container_updates after stack check: {e}")


def _apply_single_project_deploy_result(
    session: Session, server_id: int, project_path: str, ok: bool
) -> None:
    """Clear pending update badge for this stack after successful deploy."""
    if not ok:
        return
    import os

    server = session.get(Server, server_id)
    if not server:
        return
    proj_name = os.path.basename((project_path or "").rstrip("/")) or project_path
    summary: dict = {}
    if server.container_updates_summary:
        try:
            summary = json.loads(server.container_updates_summary) or {}
        except Exception:
            summary = {}
    remaining = [
        p
        for p in (summary.get("projects") or [])
        if str(p).strip() and str(p).strip() != str(proj_name).strip()
    ]
    details = dict(summary.get("project_details") or {})
    details.pop(proj_name, None)
    for k in list(details.keys()):
        if str(k).strip() == str(proj_name).strip() or os.path.basename(
            str(k).rstrip("/")
        ) == str(proj_name).strip():
            details.pop(k, None)
    server.container_updates_summary = json.dumps(
        {
            "projects": remaining,
            "project_details": details,
            "failed": summary.get("failed") or [],
            "checked": summary.get("checked") or [],
        }
    )
    server.container_updates_count = len(remaining)
    session.add(server)
    session.commit()
    try:
        from ..notifications import notify_container_updates

        notify_container_updates(
            session,
            server_id=server.id,
            server_name=server.name or proj_name,
            projects=remaining,
        )
    except Exception as e:
        logger.debug(f"notify_container_updates after stack deploy: {e}")


def _execute_docker_stack_check(
    job_id: int, server_id: int, audit_id: int, project_path: str
) -> None:
    from .. import docker_management as docker_svc

    server, hostname = _load_server_for_job(server_id)
    path = (project_path or "").strip()
    proj = _project_basename(path)
    with _get_fresh_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.status = "running"
            job.started_at = datetime.utcnow()
            _merge_job_details(
                job,
                current="checking",
                log_line=f"Checking registry images for {proj}…",
                done=False,
            )
            session.add(job)
            session.commit()
    if not server:
        _finish(audit_id, job_id, "failed", "Server not found", hostname, "docker_stack_check")
        return
    try:
        _flush_job_progress(
            job_id, "pulling", f"docker compose pull in {path}…", default_current="checking"
        )
        result = docker_svc.check_compose_updates(server, path)
        pull_out = result.get("pull_output") or ""
        _append_output_log_lines(job_id, "checking", pull_out)
        with _get_fresh_session() as s:
            _apply_single_project_check_result(s, server_id, path, result)
        payload = {
            "project": proj,
            "project_path": path,
            "has_updates": bool(result.get("has_updates")),
            "updated_images": list(result.get("updated_images") or []),
            "success": bool(result.get("success")),
            "pull_output": (pull_out or "")[:800],
        }
        status = "success" if result.get("success") or result.get("has_updates") else "failed"
        if result.get("has_updates"):
            _flush_job_progress(
                job_id,
                "updates",
                f"Updates: {', '.join(payload['updated_images'][:8]) or 'yes'}",
                default_current="checking",
            )
        else:
            _flush_job_progress(
                job_id, "ok", "No newer registry images", default_current="checking"
            )
        _finish(audit_id, job_id, status, json.dumps(payload), hostname, "docker_stack_check")
    except Exception as e:
        logger.exception("docker_stack_check failed")
        _finish(audit_id, job_id, "failed", str(e), hostname, "docker_stack_check")


def _execute_docker_stack_deploy(
    job_id: int,
    server_id: int,
    audit_id: int,
    project_path: str,
    pull: bool = True,
) -> None:
    from .. import docker_management as docker_svc

    server, hostname = _load_server_for_job(server_id)
    path = (project_path or "").strip()
    proj = _project_basename(path)
    with _get_fresh_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.status = "running"
            job.started_at = datetime.utcnow()
            _merge_job_details(
                job,
                current="deploying",
                log_line=f"Deploying {proj} (pull={pull})…",
                done=False,
            )
            session.add(job)
            session.commit()
    if not server:
        _finish(audit_id, job_id, "failed", "Server not found", hostname, "docker_stack_deploy")
        return
    try:
        if pull:
            _flush_job_progress(
                job_id, "pulling", "docker compose pull…", default_current="deploying"
            )
        _flush_job_progress(
            job_id, "up", "docker compose up -d…", default_current="deploying"
        )
        result = docker_svc.redeploy_project(server, path, pull=pull) or {}
        _append_output_log_lines(job_id, "deploying", result.get("output") or "")
        ok = bool(result.get("success"))
        with _get_fresh_session() as s:
            _apply_single_project_deploy_result(s, server_id, path, ok)
        payload = {
            "project": proj,
            "project_path": path,
            "pull": pull,
            "success": ok,
            "pull_status": result.get("pull_status"),
            "up_status": result.get("up_status"),
            "error": result.get("error"),
            "output": (result.get("output") or "")[:1500],
        }
        status = "success" if ok else "failed"
        _finish(audit_id, job_id, status, json.dumps(payload), hostname, "docker_stack_deploy")
    except Exception as e:
        logger.exception("docker_stack_deploy failed")
        _finish(
            audit_id,
            job_id,
            "failed",
            json.dumps({"project": proj, "project_path": path, "error": str(e)}),
            hostname,
            "docker_stack_deploy",
        )


async def _run_docker_stack_check_job(
    job_id: int, server_id: int, audit_id: int, project_path: str
):
    await run_in_threadpool(
        _execute_docker_stack_check, job_id, server_id, audit_id, project_path
    )


async def _run_docker_stack_deploy_job(
    job_id: int,
    server_id: int,
    audit_id: int,
    project_path: str,
    pull: bool = True,
):
    await run_in_threadpool(
        _execute_docker_stack_deploy, job_id, server_id, audit_id, project_path, pull
    )


def enqueue_docker_stack_lifecycle(
    server_id: int,
    project_path: str,
    action: str,
    *,
    user_id: int | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> Job:
    """Queue compose stop / start / restart for a whole project (H2.75 P1).

    Raises JobAlreadyActive if another stack mutation is pending/running on this host.
    """
    act = (action or "").strip().lower()
    if act not in _STACK_LIFECYCLE_ACTIONS:
        raise ValueError(f"invalid lifecycle action: {action!r}")
    path = (project_path or "").strip()
    if not path:
        raise ValueError("project_path required")
    job_type = f"docker_stack_{act}"
    with _get_fresh_session() as session:
        server = session.get(Server, server_id)
        if not server:
            raise ValueError("server not found")
        active = _active_docker_stack_job(session, server_id, job_type, path)
        if not active:
            active = _active_stack_mutating_job(session, server_id)
        if active:
            logger.info(
                f"[Jobs] {job_type} skip — job #{active.id} already active "
                f"for server {server_id}"
            )
            session.expunge(active)
            raise JobAlreadyActive(active)
        proj = _project_basename(path)
        job, audit = _create_queued_job_with_audit(
            session,
            server_id=server.id,
            job_type=job_type,
            queue_message=f"Stack {act} queued for {proj}…",
            user_id=user_id,
            audit_details=f"Job #{{job_id}} · {act} all services · {proj}",
            project_path=path,
            project=proj,
            action=act,
        )
        jid, aid, sid = job.id, audit.id, server.id
    if background_tasks is not None:
        background_tasks.add_task(
            _run_docker_stack_lifecycle_job, jid, sid, aid, path, act
        )
    else:
        _update_check_pool.submit(
            _execute_docker_stack_lifecycle, jid, sid, aid, path, act
        )
    with _get_fresh_session() as session:
        job = session.get(Job, jid)
        if job:
            session.expunge(job)
        return job


def _execute_docker_stack_lifecycle(
    job_id: int,
    server_id: int,
    audit_id: int,
    project_path: str,
    action: str,
) -> None:
    from .. import docker_management as docker_svc
    from .. import docker_inventory as inventory_svc

    act = (action or "").strip().lower()
    job_type = f"docker_stack_{act}"
    server, hostname = _load_server_for_job(server_id)
    path = (project_path or "").strip()
    proj = _project_basename(path)
    with _get_fresh_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.status = "running"
            job.started_at = datetime.utcnow()
            _merge_job_details(
                job,
                current=act,
                log_line=f"docker compose {act} for {proj}…",
                done=False,
            )
            session.add(job)
            session.commit()
    if not server:
        _finish(audit_id, job_id, "failed", "Server not found", hostname, job_type)
        return
    try:
        _flush_job_progress(
            job_id,
            act,
            f"Running docker compose {act} in {path}…",
            default_current=act,
        )
        result = docker_svc.compose_action(server, path, act, service=None) or {}
        _append_output_log_lines(job_id, act, result.get("output") or "")
        ok = bool(result.get("success"))
        if ok:
            try:
                with _get_fresh_session() as s:
                    srv = s.get(Server, server_id)
                    if srv:
                        inventory_svc.invalidate_after_mutation(s, srv, None)
            except Exception as inv_e:
                logger.debug("inventory invalidate after stack %s: %s", act, inv_e)
        payload = {
            "project": proj,
            "project_path": path,
            "action": act,
            "success": ok,
            "error": result.get("error"),
            "output": (result.get("output") or "")[:1500],
        }
        status = "success" if ok else "failed"
        _flush_job_progress(
            job_id,
            "done" if ok else "error",
            f"{act} {'ok' if ok else 'failed'}",
            default_current=act,
        )
        _finish(audit_id, job_id, status, json.dumps(payload), hostname, job_type)
    except Exception as e:
        logger.exception("docker_stack_%s failed", act)
        _finish(
            audit_id,
            job_id,
            "failed",
            json.dumps(
                {
                    "project": proj,
                    "project_path": path,
                    "action": act,
                    "error": str(e),
                }
            ),
            hostname,
            job_type,
        )


async def _run_docker_stack_lifecycle_job(
    job_id: int,
    server_id: int,
    audit_id: int,
    project_path: str,
    action: str,
):
    await run_in_threadpool(
        _execute_docker_stack_lifecycle,
        job_id,
        server_id,
        audit_id,
        project_path,
        action,
    )


def _active_stack_mutating_job(session: Session, server_id: int) -> Job | None:
    """Any pending/running stack write/up job on this host."""
    for jt in _STACK_MUTATING_JOB_TYPES:
        active = _active_job_of_type(session, server_id, jt)
        if active:
            return active
    return None


def enqueue_template_deploy(
    server_id: int,
    *,
    template_slug: str,
    values: dict,
    deploy_now: bool = True,
    user_id: int | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> Job:
    """Queue template apply (SSH write + optional compose up) as a Job (v0.6.0).

    Variable values (including secrets) are Fernet-encrypted in Job.details and
    cleared when the job finishes. Raises JobAlreadyActive if a stack mutation
    is already pending/running on this host.
    """
    from ...security.encryption import encrypt_str

    slug = (template_slug or "").strip()
    if not slug:
        raise ValueError("template_slug required")
    if not isinstance(values, dict):
        raise ValueError("values must be a dict")
    values_encrypted = encrypt_str(json.dumps(values))
    with _get_fresh_session() as session:
        server = session.get(Server, server_id)
        if not server:
            raise ValueError("server not found")
        active = _active_stack_mutating_job(session, server_id)
        if active:
            logger.info(
                f"[Jobs] template_deploy skip — job #{active.id} ({active.job_type}) "
                f"already active for server {server_id}"
            )
            session.expunge(active)
            raise JobAlreadyActive(active)
        job, audit = _create_queued_job_with_audit(
            session,
            server_id=server.id,
            job_type="template_deploy",
            queue_message=f"Template deploy queued ({slug})…",
            user_id=user_id,
            audit_details=f"Job #{{job_id}} · template deploy {slug}",
            template_slug=slug,
            deploy_now=bool(deploy_now),
            values_encrypted=values_encrypted,
        )
        jid, aid, sid = job.id, audit.id, server.id
        do_deploy = bool(deploy_now)
    if background_tasks is not None:
        background_tasks.add_task(
            _run_template_deploy_job, jid, sid, aid, slug, do_deploy
        )
    else:
        _update_check_pool.submit(
            _execute_template_deploy, jid, sid, aid, slug, do_deploy
        )
    with _get_fresh_session() as session:
        job = session.get(Job, jid)
        if job:
            session.expunge(job)
        return job


def enqueue_template_redeploy(
    server_id: int,
    *,
    deployment_id: int,
    updated_public: dict | None = None,
    updated_secrets: dict | None = None,
    deploy_now: bool = True,
    user_id: int | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> Job:
    """Queue template redeploy from desired state as a Job (v0.6.0)."""
    from ...security.encryption import encrypt_str

    dep_id = int(deployment_id)
    pub = {k: str(v) for k, v in (updated_public or {}).items()}
    secs = {
        k: str(v)
        for k, v in (updated_secrets or {}).items()
        if v is not None and str(v) != ""
    }
    secrets_encrypted = encrypt_str(json.dumps(secs)) if secs else None
    with _get_fresh_session() as session:
        server = session.get(Server, server_id)
        if not server:
            raise ValueError("server not found")
        active = _active_stack_mutating_job(session, server_id)
        if active:
            logger.info(
                f"[Jobs] template_redeploy skip — job #{active.id} ({active.job_type}) "
                f"already active for server {server_id}"
            )
            session.expunge(active)
            raise JobAlreadyActive(active)
        job, audit = _create_queued_job_with_audit(
            session,
            server_id=server.id,
            job_type="template_redeploy",
            queue_message=f"Template redeploy queued (deployment #{dep_id})…",
            user_id=user_id,
            audit_details=f"Job #{{job_id}} · template redeploy deployment={dep_id}",
            deployment_id=dep_id,
            deploy_now=bool(deploy_now),
            updated_public=pub,
            secrets_encrypted=secrets_encrypted,
        )
        jid, aid, sid = job.id, audit.id, server.id
        do_deploy = bool(deploy_now)
    if background_tasks is not None:
        background_tasks.add_task(
            _run_template_redeploy_job, jid, sid, aid, dep_id, do_deploy
        )
    else:
        _update_check_pool.submit(
            _execute_template_redeploy, jid, sid, aid, dep_id, do_deploy
        )
    with _get_fresh_session() as session:
        job = session.get(Job, jid)
        if job:
            session.expunge(job)
        return job


def _load_job_details(job_id: int) -> dict:
    with _get_fresh_session() as session:
        job = session.get(Job, job_id)
        if not job or not job.details:
            return {}
        try:
            data = json.loads(job.details)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _clear_job_secret_blobs(job_id: int) -> None:
    """Remove encrypted variable/secret payloads from Job.details after finish."""
    try:
        with _get_fresh_session() as s:
            job = s.get(Job, job_id)
            if not job:
                return
            try:
                data = json.loads(job.details or "{}")
                if not isinstance(data, dict):
                    return
            except Exception:
                return
            changed = False
            for key in ("values_encrypted", "secrets_encrypted"):
                if key in data:
                    data.pop(key, None)
                    changed = True
            if changed:
                job.details = json.dumps(data)
                s.add(job)
                s.commit()
    except Exception as e:
        logger.debug(f"clear job secret blobs: {e}")


def _execute_template_deploy(
    job_id: int,
    server_id: int,
    audit_id: int,
    template_slug: str,
    deploy_now: bool = True,
) -> None:
    from ...security.encryption import decrypt_str
    from ..service_templates import TemplateError, apply_template_to_host

    server, hostname = _load_server_for_job(server_id)
    slug = (template_slug or "").strip()
    with _get_fresh_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.status = "running"
            job.started_at = datetime.utcnow()
            _merge_job_details(
                job,
                current="deploying",
                log_line=f"Applying template {slug}…",
                done=False,
            )
            session.add(job)
            session.commit()
    if not server:
        _finish(audit_id, job_id, "failed", "Server not found", hostname, "template_deploy")
        _clear_job_secret_blobs(job_id)
        return

    details = _load_job_details(job_id)
    values: dict = {}
    try:
        blob = details.get("values_encrypted") or ""
        if blob:
            values = json.loads(decrypt_str(str(blob)))
            if not isinstance(values, dict):
                values = {}
    except Exception as e:
        _finish(
            audit_id,
            job_id,
            "failed",
            json.dumps(
                {
                    "success": False,
                    "error": f"Could not load deploy values: {e}",
                    "template_slug": slug,
                }
            ),
            hostname,
            "template_deploy",
        )
        _clear_job_secret_blobs(job_id)
        return

    try:
        _flush_job_progress(
            job_id,
            "writing",
            "Rendering files and writing over SSH…",
            default_current="deploying",
        )
        with _get_fresh_session() as session:
            srv = session.get(Server, server_id)
            if not srv:
                raise TemplateError("Server not found")
            result = apply_template_to_host(
                session,
                server=srv,
                template_slug=slug,
                values=values,
                deploy_now=deploy_now,
                auto_generate=False,
            )
        dep_id = result.get("deployment_id")
        project = result.get("project_name") or slug
        _flush_job_progress(
            job_id,
            "done",
            f"Stored desired state V{result.get('config_version')} for {project}",
            default_current="deploying",
        )
        rd = result.get("redeploy") or {}
        ok_host = True
        if deploy_now and isinstance(rd, dict) and rd.get("success") is False:
            ok_host = False
        payload = {
            "success": ok_host,
            "template_slug": slug,
            "project_name": project,
            "deployment_id": dep_id,
            "config_version": result.get("config_version"),
            "project_path": result.get("project_path"),
            "secret_keys": result.get("secret_keys") or [],
            "redeploy": {
                "success": (rd.get("success") if isinstance(rd, dict) else None),
                "error": (rd.get("error") if isinstance(rd, dict) else None),
            }
            if isinstance(rd, dict)
            else None,
            "redirect_url": f"/templates/deployments/{dep_id}" if dep_id else None,
            "error": (rd.get("error") if isinstance(rd, dict) and not ok_host else None),
        }
        status = "success" if ok_host else "failed"
        _finish(audit_id, job_id, status, json.dumps(payload), hostname, "template_deploy")
    except TemplateError as e:
        logger.info("template_deploy TemplateError: %s", e)
        _finish(
            audit_id,
            job_id,
            "failed",
            json.dumps(
                {"success": False, "error": str(e)[:400], "template_slug": slug}
            ),
            hostname,
            "template_deploy",
        )
    except Exception as e:
        logger.exception("template_deploy failed")
        _finish(
            audit_id,
            job_id,
            "failed",
            json.dumps(
                {"success": False, "error": str(e)[:400], "template_slug": slug}
            ),
            hostname,
            "template_deploy",
        )
    finally:
        _clear_job_secret_blobs(job_id)


def _execute_template_redeploy(
    job_id: int,
    server_id: int,
    audit_id: int,
    deployment_id: int,
    deploy_now: bool = True,
) -> None:
    from ...security.encryption import decrypt_str
    from ..service_templates import TemplateError, get_deployment, redeploy_desired_state

    server, hostname = _load_server_for_job(server_id)
    dep_id = int(deployment_id)
    with _get_fresh_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.status = "running"
            job.started_at = datetime.utcnow()
            _merge_job_details(
                job,
                current="redeploying",
                log_line=f"Redeploying deployment #{dep_id}…",
                done=False,
            )
            session.add(job)
            session.commit()
    if not server:
        _finish(
            audit_id, job_id, "failed", "Server not found", hostname, "template_redeploy"
        )
        _clear_job_secret_blobs(job_id)
        return

    details = _load_job_details(job_id)
    updated_public = (
        details.get("updated_public")
        if isinstance(details.get("updated_public"), dict)
        else {}
    )
    updated_secrets: dict = {}
    try:
        blob = details.get("secrets_encrypted")
        if blob:
            updated_secrets = json.loads(decrypt_str(str(blob)))
            if not isinstance(updated_secrets, dict):
                updated_secrets = {}
    except Exception as e:
        _finish(
            audit_id,
            job_id,
            "failed",
            json.dumps(
                {
                    "success": False,
                    "error": f"Could not load secret updates: {e}",
                    "deployment_id": dep_id,
                }
            ),
            hostname,
            "template_redeploy",
        )
        _clear_job_secret_blobs(job_id)
        return

    try:
        _flush_job_progress(
            job_id,
            "writing",
            "Updating desired state and writing over SSH…",
            default_current="redeploying",
        )
        with _get_fresh_session() as session:
            srv = session.get(Server, server_id)
            dep = get_deployment(session, dep_id)
            if not srv or not dep:
                raise TemplateError("Server or deployment not found")
            if dep.server_id != server_id:
                raise TemplateError("Deployment does not belong to this server")
            result = redeploy_desired_state(
                session,
                server=srv,
                deployment=dep,
                updated_public=updated_public or None,
                updated_secrets=updated_secrets or None,
                deploy_now=deploy_now,
            )
        project = result.get("project_name") or f"deployment-{dep_id}"
        rd = result.get("redeploy") or {}
        ok_host = True
        if deploy_now and isinstance(rd, dict) and rd.get("success") is False:
            ok_host = False
        out_dep = result.get("deployment_id") or dep_id
        payload = {
            "success": ok_host,
            "deployment_id": out_dep,
            "project_name": project,
            "config_version": result.get("config_version"),
            "redirect_url": f"/templates/deployments/{out_dep}",
            "error": (rd.get("error") if isinstance(rd, dict) and not ok_host else None),
        }
        status = "success" if ok_host else "failed"
        _finish(
            audit_id, job_id, status, json.dumps(payload), hostname, "template_redeploy"
        )
    except TemplateError as e:
        logger.info("template_redeploy TemplateError: %s", e)
        _finish(
            audit_id,
            job_id,
            "failed",
            json.dumps(
                {"success": False, "error": str(e)[:400], "deployment_id": dep_id}
            ),
            hostname,
            "template_redeploy",
        )
    except Exception as e:
        logger.exception("template_redeploy failed")
        _finish(
            audit_id,
            job_id,
            "failed",
            json.dumps(
                {"success": False, "error": str(e)[:400], "deployment_id": dep_id}
            ),
            hostname,
            "template_redeploy",
        )
    finally:
        _clear_job_secret_blobs(job_id)


async def _run_template_deploy_job(
    job_id: int,
    server_id: int,
    audit_id: int,
    template_slug: str,
    deploy_now: bool = True,
):
    await run_in_threadpool(
        _execute_template_deploy, job_id, server_id, audit_id, template_slug, deploy_now
    )


async def _run_template_redeploy_job(
    job_id: int,
    server_id: int,
    audit_id: int,
    deployment_id: int,
    deploy_now: bool = True,
):
    await run_in_threadpool(
        _execute_template_redeploy,
        job_id,
        server_id,
        audit_id,
        deployment_id,
        deploy_now,
    )

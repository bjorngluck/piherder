# app/tasks.py
"""
Celery tasks for PiHerder.

Worker feeds DB (Job + Server + AuditLog) with status.
UI reads only from DB (minimal polling).
"""
from sqlmodel import Session, select
from app.celery_app import celery
from app.services.backup import (
    run_backup,
    backup_succeeded,
    backup_failure_message,
    _flush_job_progress_db,
    clear_job_progress_buffer,
)
from app.services.backup_audit import record_backup_audit_from_job
from app.database import engine
from app.models import Server, Job
from datetime import datetime
import json
import logging
import traceback

logger = logging.getLogger(__name__)


@celery.task(bind=True, max_retries=1, default_retry_delay=30)
def backup_server(self, server_id: int, job_id: int | None = None, audit_id: int | None = None, source_filter: str | None = None):
    """
    Celery background task.
    Worker writes rich status into Job + append-only AuditLog events.
    """
    db = Session(engine)
    server = None

    try:
        server = db.exec(select(Server).where(Server.id == server_id)).first()
        if not server:
            logger.error(f"Server {server_id} not found")
            if job_id:
                _update_job_status(job_id, "failed", {"error": "Server not found"})
            return {"status": "error", "message": "Server not found"}

        if job_id:
            job = db.get(Job, job_id)
            if not job or job.status not in ("pending", "running"):
                logger.info(f"[Celery] Job {job_id} no longer active (status={getattr(job, 'status', None)}), skipping")
                return {"status": "skipped", "job_id": job_id}

        if job_id:
            initial = {
                "current": "starting",
                "source_filter": source_filter,
                "started_at": datetime.utcnow().isoformat(),
            }
            _update_job_status(job_id, "running", initial)
            job = db.get(Job, job_id)
            if job:
                src = source_filter or "all sources"
                record_backup_audit_from_job(
                    db, job, "running", message=f"Backup in progress for {src}"
                )
                db.commit()

        sources_override = None
        if source_filter:
            try:
                all_sources = server.get_backup_sources()
                filtered = [s for s in all_sources if s.get("source") == source_filter]
                if filtered:
                    sources_override = filtered
            except Exception as e:
                logger.warning(f"source_filter error: {e}")

        result = run_backup(server, sources_override=sources_override, job_id=job_id)

        summary = result if isinstance(result, dict) else {"raw": str(result)}
        ok = backup_succeeded(summary) if isinstance(summary, dict) else False
        if job_id:
            _flush_job_progress_db(job_id, force=True)
            if ok:
                final = {"current": "completed", "result_summary": summary}
            else:
                err = backup_failure_message(summary)
                final = {
                    "current": "failed",
                    "result_summary": summary,
                    "error": err,
                    "log_lines": [f"Backup failed: {err[:240]}"],
                }
            _update_job_status(job_id, "success" if ok else "failed", final)
            clear_job_progress_buffer(job_id)
            job = db.get(Job, job_id)
            if job and job.status in ("success", "failed"):
                phase = "success" if ok else "failed"
                snippet = summary if ok else {"error": backup_failure_message(summary), **summary}
                record_backup_audit_from_job(
                    db, job, phase,
                    message=backup_failure_message(summary) if not ok else None,
                    output_snippet=snippet,
                )
                db.commit()

        if ok:
            try:
                server.last_backup_at = datetime.utcnow()
                db.add(server)
                db.commit()
            except Exception:
                pass
            try:
                from .services.notifications import resolve_backup_failed
                resolve_backup_failed(db, server_id)
            except Exception:
                pass
        else:
            try:
                from .services.notifications import notify_backup_failed
                from .services.backup import backup_failure_message
                msg = backup_failure_message(summary) if isinstance(summary, dict) else str(summary)
                notify_backup_failed(db, server_id, server.name if server else str(server_id), msg)
            except Exception:
                pass

        logger.info(f"[Celery] Backup {'completed' if ok else 'failed'} for server {server_id}")
        return {"status": "success" if ok else "failed", "server_id": server_id, "result": result}

    except Exception as exc:
        logger.error(f"Backup failed for server {server_id}: {exc}\n{traceback.format_exc()}")

        error_str = str(exc).lower()
        is_transient = any(x in error_str for x in ("connection", "timeout", "refused", "reset", "closed"))

        if job_id:
            _flush_job_progress_db(job_id, force=True)
            err = str(exc)[:800]
            _update_job_status(job_id, "failed", {
                "error": err,
                "current": "failed",
                "log_lines": [f"Backup failed: {err[:240]}"],
            })
            clear_job_progress_buffer(job_id)
            try:
                with Session(engine) as s:
                    job = s.get(Job, job_id)
                    if job and job.status == "failed":
                        try:
                            existing = json.loads(job.details or "{}")
                        except Exception:
                            existing = {}
                        if not existing.get("audit_failed_recorded"):
                            record_backup_audit_from_job(
                                s,
                                job,
                                "failed",
                                message=err,
                                output_snippet={"error": err},
                            )
                            existing["audit_failed_recorded"] = True
                            job.details = json.dumps(existing)
                            s.add(job)
                            s.commit()
            except Exception as audit_exc:
                logger.error(f"Failed to record backup failed audit for job {job_id}: {audit_exc}")

        if is_transient:
            logger.warning(f"Transient error on server {server_id} - retrying once")
            raise self.retry(exc=exc)
        else:
            logger.info(f"Permanent error on server {server_id} - not retrying")

    finally:
        db.close()


def _update_job_status(job_id: int, status: str, extra: dict):
    """Update Job status + merge details JSON (worker feeds DB)."""
    try:
        with Session(engine) as s:
            job = s.get(Job, job_id)
            if job:
                job.status = status
                if status == "running" and job.started_at is None:
                    job.started_at = datetime.utcnow()
                if extra:
                    existing = {}
                    try:
                        if job.details:
                            existing = json.loads(job.details)
                    except Exception:
                        pass
                    existing.update(extra)
                    job.details = json.dumps(existing)
                if status in ("success", "failed"):
                    job.finished_at = datetime.utcnow()
                s.add(job)
                s.commit()
    except Exception as e:
        logger.error(f"Failed to update job {job_id} status={status}: {e}")
# app/tasks.py
"""
Celery tasks for PiHerder.
Background jobs (backups, etc.) run here to keep web requests responsive
and to allow distributed/scalable execution.
"""
from sqlmodel import Session, select
from app.celery_app import celery
from app.services.backup import run_backup
from app.database import engine
from app.models import Server, Job, AuditLog
from datetime import datetime
import json
import logging
import traceback

logger = logging.getLogger(__name__)


@celery.task(bind=True, max_retries=1, default_retry_delay=30)
def backup_server(self, server_id: int, job_id: int | None = None, audit_id: int | None = None, source_filter: str | None = None):
    """
    Celery background task to run backup for a server.
    Worker feeds DB (last_backup_at etc.) so web stays thin.
    Retries only once on transient connection errors.
    """
    try:
        logger.info(f"[Celery] Starting backup for server ID: {server_id} (job={job_id})")

        with Session(engine) as db:
            server = db.exec(select(Server).where(Server.id == server_id)).first()

            if not server:
                logger.error(f"Server with ID {server_id} not found")
                if job_id or audit_id:
                    _finish_job_audit(job_id, audit_id, "failed", f"Server {server_id} not found", server_hostname=str(server_id))
                return {"status": "error", "message": f"Server {server_id} not found"}

            hostname = server.hostname

            # Compute filtered sources WITHOUT mutating the persisted server.backup_paths
            sources_override = None
            if source_filter:
                try:
                    all_sources = server.get_backup_sources()
                    filtered = [s for s in all_sources if s.get("source") == source_filter]
                    if filtered:
                        sources_override = filtered
                except Exception as e:
                    logger.warning(f"Could not apply source_filter: {e}")

            # Run the actual (potentially long) backup - pass override if filtering
            result = run_backup(server, sources_override=sources_override)

            summary = json.dumps(result) if isinstance(result, dict) else str(result)

            if job_id or audit_id:
                _finish_job_audit(job_id, audit_id, "success", summary, hostname, "backup")

            # Worker feeds DB so web can be thin pure reader
            try:
                server.last_backup_at = datetime.utcnow()
                db.add(server)
                db.commit()
            except Exception as e:
                logger.warning(f"Could not update last_backup_at for server {server_id}: {e}")

            logger.info(f"[Celery] Backup completed successfully for server {server_id}")
            return {
                "status": "success",
                "server_id": server_id,
                "job_id": job_id,
                "result": result
            }

    except Exception as exc:
        logger.error(f"Backup failed for server {server_id}: {exc}\n{traceback.format_exc()}")

        # Only retry once on clearly transient connection/SSH errors
        error_str = str(exc).lower()
        is_transient = any(x in error_str for x in ("connection", "timeout", "refused", "reset", "closed"))

        if job_id or audit_id:
            try:
                _finish_job_audit(job_id, audit_id, "failed", str(exc)[:2000], getattr(server, 'hostname', str(server_id)) if 'server' in locals() else str(server_id), "backup")
            except Exception:
                pass

        if is_transient:
            logger.warning(f"[Celery] Transient error detected for server {server_id} - retrying once")
            raise self.retry(exc=exc)
        else:
            # Permanent error - fail immediately (no more retries)
            logger.info(f"[Celery] Permanent error for server {server_id} - not retrying")


def _finish_job_audit(job_id: int | None, audit_id: int | None, status: str, snippet: str, hostname: str = "", job_type: str = "backup"):
    """Helper to update Job + AuditLog after Celery task completes (success or failure)."""
    if not job_id and not audit_id:
        return
    try:
        with Session(engine) as s:
            if audit_id:
                audit = s.get(AuditLog, audit_id)
                if audit:
                    audit.status = status
                    audit.output_snippet = (snippet or "")[:2000]
                    audit.finished_at = datetime.utcnow()
                    s.add(audit)
            if job_id:
                job = s.get(Job, job_id)
                if job:
                    job.status = status
                    job.finished_at = datetime.utcnow()
                    s.add(job)
            s.commit()

        # Optional summary webhook (best-effort)
        if hostname and job_type:
            try:
                from app.services.jobs import _send_summary_webhook
                _send_summary_webhook(hostname, job_type, status, snippet)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Failed to finish job/audit in Celery task: {e}")

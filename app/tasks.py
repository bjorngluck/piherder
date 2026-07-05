# app/tasks.py
"""
Celery tasks for PiHerder.

Worker feeds DB (Job + Server + AuditLog) with status.
UI reads only from DB (minimal polling).
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
    Celery background task.
    Worker writes rich status into Job + AuditLog.
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

        hostname = server.hostname

        # Mark job running + initial details in DB (UI reads this)
        if job_id:
            initial = {
                "current": "starting",
                "source_filter": source_filter,
                "started_at": datetime.utcnow().isoformat()
            }
            _update_job_status(job_id, "running", initial)

        # Sources override if filtering
        sources_override = None
        if source_filter:
            try:
                all_sources = server.get_backup_sources()
                filtered = [s for s in all_sources if s.get("source") == source_filter]
                if filtered:
                    sources_override = filtered
            except Exception as e:
                logger.warning(f"source_filter error: {e}")

        # Run the actual backup
        result = run_backup(server, sources_override=sources_override)

        # Final success update with rich result
        summary = result if isinstance(result, dict) else {"raw": str(result)}
        if job_id:
            final = {
                "current": "completed",
                "result_summary": summary
            }
            _update_job_status(job_id, "success", final)

        # Update AuditLog with final status
        _finalize_audit_log(server_id, "success", summary)

        # Also persist last_backup_at on Server
        try:
            server.last_backup_at = datetime.utcnow()
            db.add(server)
            db.commit()
        except Exception:
            pass

        logger.info(f"[Celery] Backup completed for server {server_id}")
        return {"status": "success", "server_id": server_id, "result": result}

    except Exception as exc:
        logger.error(f"Backup failed for server {server_id}: {exc}\n{traceback.format_exc()}")

        error_str = str(exc).lower()
        is_transient = any(x in error_str for x in ("connection", "timeout", "refused", "reset", "closed"))

        if job_id:
            _update_job_status(job_id, "failed", {"error": str(exc)[:800]})

        _finalize_audit_log(server_id, "failed", {"error": str(exc)[:800]})

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

def _finalize_audit_log(server_id: int, status: str, details: dict):
    """Update the latest backup AuditLog for this server with final status."""
    try:
        with Session(engine) as s:
            audit = s.exec(
                select(AuditLog)
                .where(AuditLog.server_id == server_id, AuditLog.action == "backup")
                .order_by(AuditLog.started_at.desc())
                .limit(1)
            ).first()
            if audit:
                audit.status = status
                if details:
                    audit.output_snippet = json.dumps(details)[:2000]
                audit.finished_at = datetime.utcnow()
                s.add(audit)
                s.commit()
    except Exception as e:
        logger.error(f"Failed to finalize audit log for server {server_id}: {e}")

# app/tasks.py
"""
Celery tasks for PiHerder.
Background jobs (backups, etc.) run here.

Worker feeds DB with status (Job.status + details JSON).
Web UI reads ONLY from DB (thin layer).
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
    Worker writes status + snippets to Job record in DB.
    UI pulls everything from DB.
    """
    db = Session(engine)
    server = None
    job = None

    try:
        server = db.exec(select(Server).where(Server.id == server_id)).first()
        if not server:
            logger.error(f"Server {server_id} not found")
            if job_id:
                _update_job_status(job_id, "failed", {"error": "Server not found"})
            return {"status": "error", "message": "Server not found"}

        hostname = server.hostname

        # Mark job as running in DB (UI reads this)
        if job_id:
            job = db.get(Job, job_id)
            if job:
                job.status = "running"
                job.started_at = datetime.utcnow()
                job.details = json.dumps({"current": "starting", "source_filter": source_filter})
                db.add(job)
                db.commit()

        # Compute sources override if filtering
        sources_override = None
        if source_filter:
            try:
                all_sources = server.get_backup_sources()
                filtered = [s for s in all_sources if s.get("source") == source_filter]
                if filtered:
                    sources_override = filtered
            except Exception as e:
                logger.warning(f"source_filter error: {e}")

        # Run backup (worker does the heavy work)
        result = run_backup(server, sources_override=sources_override)

        # Finalize job in DB
        summary = result if isinstance(result, dict) else {"raw": str(result)}
        if job_id:
            _update_job_status(job_id, "success", summary)

        # Also update Server.last_backup_at (already done in some paths, ensure here)
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
            _update_job_status(job_id, "failed", {"error": str(exc)[:500]})

        if is_transient:
            logger.warning(f"Transient error on server {server_id} - retrying once")
            raise self.retry(exc=exc)
        else:
            logger.info(f"Permanent error on server {server_id} - not retrying")

    finally:
        db.close()


def _update_job_status(job_id: int, status: str, details: dict):
    """Helper: update Job status + details JSON in DB (UI reads this)."""
    try:
        with Session(engine) as s:
            job = s.get(Job, job_id)
            if job:
                job.status = status
                if details:
                    existing = {}
                    try:
                        existing = json.loads(job.details or "{}")
                    except Exception:
                        pass
                    existing.update(details)
                    job.details = json.dumps(existing)
                if status in ("success", "failed"):
                    job.finished_at = datetime.utcnow()
                s.add(job)
                s.commit()
    except Exception as e:
        logger.error(f"Failed to update job {job_id}: {e}")

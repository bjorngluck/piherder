# app/tasks.py
"""
Celery tasks for PiHerder.
Background jobs (backups, etc.) run here to keep web requests responsive.
"""
from sqlmodel import Session, select
from app.celery_app import celery
from app.services.backup import run_backup
from app.database import engine
from app.models import Server
import logging

logger = logging.getLogger(__name__)


@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def backup_server(self, server_id: int):
    """
    Celery background task to run backup for a server.
    Uses a fresh DB session (no web request context).
    """
    try:
        logger.info(f"[Celery] Starting backup for server ID: {server_id}")

        with Session(engine) as db:
            # Modern SQLModel lookup (equivalent to db.get(Server, server_id) too)
            server = db.exec(select(Server).where(Server.id == server_id)).first()

            if not server:
                logger.error(f"Server with ID {server_id} not found")
                return {"status": "error", "message": f"Server {server_id} not found"}

            # Run the actual backup using your existing function
            result = run_backup(server)

            logger.info(f"[Celery] Backup completed successfully for server {server_id}")
            return {
                "status": "success",
                "server_id": server_id,
                "result": result
            }

    except Exception as exc:
        logger.error(f"Backup failed for server {server_id}: {exc}")
        raise self.retry(exc=exc)

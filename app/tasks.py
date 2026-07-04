# app/tasks.py
from app.celery_app import celery
from app.services.backup import run_backup
from app.database import get_db
from app.models import Server
import logging

logger = logging.getLogger(__name__)


@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def backup_server(self, server_id: int):
    """
    Celery background task to run backup for a server.
    """
    try:
        logger.info(f"[Celery] Starting backup for server ID: {server_id}")

        # Get a database session
        db = next(get_db())

        # Fetch the server object
        server = db.query(Server).filter(Server.id == server_id).first()

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
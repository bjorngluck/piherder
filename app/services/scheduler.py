"""
Scheduler helpers extracted from main.py for maintainability.

Handles:
- Per-server backup cron jobs (via APScheduler -> Celery enqueue)
- PiHerder self-backup schedule registration and execution

Kept lightweight: functions only, no new scheduler instance here.
The global APScheduler lives in main.py lifespan.
"""

from sqlmodel import Session
import json
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

HERDER_SCHEDULE_JOB_ID = "herder_self_backup"


def schedule_backup_job(server_id: int):
    """Called by APScheduler — enqueue Celery only, never rsync on web."""
    # We can't easily import HAS/scheduler here without circularity,
    # so the caller in lifespan guards it. The function itself is defensive.
    try:
        from ..database import engine
        from .jobs import enqueue_backup_for_server
        from ..models import Server

        with Session(engine) as db:
            server = db.get(Server, server_id)
            if server and server.backup_enabled:
                enqueue_backup_for_server(db, server)
    except Exception as e:
        logger.debug(f"[SCHEDULER] Error enqueuing backup for {server_id}: {e}")


def sync_herder_backup_schedule(scheduler, HAS_SCHEDULER):
    """Register or remove the global PiHerder self-backup cron job from config."""
    if not HAS_SCHEDULER or not scheduler:
        return
    from . import herder_backup as hb

    try:
        scheduler.remove_job(HERDER_SCHEDULE_JOB_ID)
    except Exception:
        pass

    cfg = hb.load_herder_config()
    if not cfg.get("schedule_enabled"):
        logger.info("[SCHEDULER] PiHerder self-backup schedule disabled")
        return

    cron = (cfg.get("schedule_cron") or "").strip()
    if not cron:
        return

    try:
        hb.validate_cron_expression(cron)
        parts = cron.split()
        from apscheduler.triggers.cron import CronTrigger
        trigger = CronTrigger(
            minute=parts[0], hour=parts[1],
            day=parts[2], month=parts[3], day_of_week=parts[4],
            timezone=hb.get_app_timezone(),
        )
        scheduler.add_job(
            func=schedule_herder_backup_job,
            trigger=trigger,
            id=HERDER_SCHEDULE_JOB_ID,
            replace_existing=True,
            name="PiHerder self-backup",
        )
        logger.info(f"[SCHEDULER] PiHerder self-backup scheduled: {cron} ({hb.get_app_timezone()})")
    except Exception as e:
        logger.warning(f"[SCHEDULER] Could not register herder backup schedule: {e}")


def schedule_herder_backup_job():
    """Global scheduled PiHerder self-backup (config + keys + optional audit)."""
    # Job is only registered when enabled, so no HAS guard needed here.
    logger.info("[SCHEDULER] Running scheduled PiHerder self-backup")
    try:
        from . import herder_backup as hb
        from ..database import engine
        from ..models import AuditLog
        from sqlmodel import Session

        cfg = hb.load_herder_config()
        mode = cfg.get("schedule_mode", "config_only")
        include_audit = (mode == "full")
        config_only = (mode != "full")
        path = hb.create_herder_backup(include_audit=include_audit, config_only=config_only)
        logger.info(f"[SCHEDULER] PiHerder self-backup written: {path}")
        try:
            with Session(engine) as s:
                al = AuditLog(
                    user_id=None,
                    server_id=None,
                    action="herder_backup",
                    status="success",
                    details=f"Scheduled self-backup ({mode}): {getattr(path, 'name', path)}",
                    output_snippet=json.dumps({"path": str(path), "mode": mode}),
                    started_at=datetime.utcnow(),
                    finished_at=datetime.utcnow(),
                )
                s.add(al)
                s.commit()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[SCHEDULER] PiHerder self-backup error: {e}")

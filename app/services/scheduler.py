"""
Scheduler helpers extracted from main.py for maintainability.

Handles:
- Per-server backup cron jobs (via APScheduler -> Celery enqueue)
- Per-server OS update checks (check-only)
- Per-server container update checks (check-only)
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


def _cron_trigger(cron: str, timezone=None):
    from apscheduler.triggers.cron import CronTrigger
    parts = cron.strip().split()
    if len(parts) != 5:
        raise ValueError("cron must have 5 fields")
    kwargs = dict(
        minute=parts[0], hour=parts[1],
        day=parts[2], month=parts[3], day_of_week=parts[4],
    )
    if timezone:
        kwargs["timezone"] = timezone
    return CronTrigger(**kwargs)


def schedule_backup_job(server_id: int):
    """Called by APScheduler — enqueue Celery only, never rsync on web."""
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


def schedule_os_check_job(server_id: int):
    """Enqueue check-only OS update scan for a server."""
    try:
        from ..database import engine
        from .jobs import run_os_update_check_now
        from ..models import Server

        with Session(engine) as db:
            server = db.get(Server, server_id)
            if server and (server.os_check_enabled or server.os_patch_enabled):
                run_os_update_check_now(db, server)
    except Exception as e:
        logger.debug(f"[SCHEDULER] Error OS check for {server_id}: {e}")


def schedule_container_check_job(server_id: int):
    """Enqueue check-only container image scan for a server."""
    try:
        from ..database import engine
        from .jobs import run_container_update_check_now
        from ..models import Server

        with Session(engine) as db:
            server = db.get(Server, server_id)
            if server and (server.container_check_enabled or server.container_patch_enabled):
                run_container_update_check_now(db, server)
    except Exception as e:
        logger.debug(f"[SCHEDULER] Error container check for {server_id}: {e}")


def _remove_job(scheduler, job_id: str):
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass


def sync_server_cron_jobs(scheduler, HAS_SCHEDULER, server):
    """Register or remove all per-server cron jobs for one server. Call on config save + startup."""
    if not HAS_SCHEDULER or not scheduler or not server or not server.id:
        return

    sid = server.id

    # Backup
    bid = f"backup_{sid}"
    _remove_job(scheduler, bid)
    if server.backup_enabled and server.backup_schedule:
        try:
            trigger = _cron_trigger(server.backup_schedule)
            scheduler.add_job(
                func=schedule_backup_job,
                trigger=trigger,
                args=[sid],
                id=bid,
                replace_existing=True,
                name=f"Backup {server.name}",
            )
            logger.info(f"[SCHEDULER] Backup scheduled for server {sid}: {server.backup_schedule}")
        except Exception as e:
            logger.warning(f"[SCHEDULER] Backup schedule failed for {sid}: {e}")

    # OS update check
    oid = f"os_check_{sid}"
    _remove_job(scheduler, oid)
    if server.os_check_enabled and server.os_check_schedule and server.os_patch_enabled:
        try:
            trigger = _cron_trigger(server.os_check_schedule)
            scheduler.add_job(
                func=schedule_os_check_job,
                trigger=trigger,
                args=[sid],
                id=oid,
                replace_existing=True,
                name=f"OS check {server.name}",
            )
            logger.info(f"[SCHEDULER] OS check scheduled for server {sid}: {server.os_check_schedule}")
        except Exception as e:
            logger.warning(f"[SCHEDULER] OS check schedule failed for {sid}: {e}")

    # Container update check
    cid = f"container_check_{sid}"
    _remove_job(scheduler, cid)
    if server.container_check_enabled and server.container_check_schedule and server.container_patch_enabled:
        try:
            trigger = _cron_trigger(server.container_check_schedule)
            scheduler.add_job(
                func=schedule_container_check_job,
                trigger=trigger,
                args=[sid],
                id=cid,
                replace_existing=True,
                name=f"Container check {server.name}",
            )
            logger.info(f"[SCHEDULER] Container check scheduled for server {sid}: {server.container_check_schedule}")
        except Exception as e:
            logger.warning(f"[SCHEDULER] Container check schedule failed for {sid}: {e}")


def sync_all_server_cron_jobs(scheduler, HAS_SCHEDULER):
    if not HAS_SCHEDULER or not scheduler:
        return
    try:
        from ..database import engine
        from ..models import Server
        from sqlmodel import select
        with Session(engine) as db:
            for server in db.exec(select(Server)).all():
                sync_server_cron_jobs(scheduler, HAS_SCHEDULER, server)
    except Exception as e:
        logger.warning(f"[SCHEDULER] sync_all failed: {e}")


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
        trigger = _cron_trigger(cron, timezone=hb.get_app_timezone())
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
    logger.info("[SCHEDULER] Running scheduled PiHerder self-backup")
    from ..database import engine
    try:
        from . import herder_backup as hb
        from ..models import AuditLog

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
        try:
            from .notifications import upsert_notification
            with Session(engine) as s:
                upsert_notification(
                    s,
                    fingerprint="herder_backup_failed",
                    type="herder_backup_failed",
                    title="PiHerder self-backup failed",
                    body=str(e)[:300],
                    link_url="/herder-backups",
                    severity="critical",
                )
        except Exception:
            pass

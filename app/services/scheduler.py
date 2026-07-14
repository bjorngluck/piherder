"""
Scheduler helpers extracted from main.py for maintainability.

Handles:
- Per-server backup cron jobs (via APScheduler -> Celery enqueue)
- Per-server OS update checks (check-only)
- Per-server container update checks (check-only)
- Per-server OS / container patch *apply* schedules (opt-in; default off)
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


def os_apply_skip_reason(server) -> str | None:
    """Return a short skip reason, or None if OS patch apply should be enqueued.

    Pure helper (no DB/IO) so unit tests can cover schedule guardrails.
    """
    if not server:
        return "missing"
    if not server.os_patch_enabled or not getattr(server, "os_apply_enabled", False):
        return "disabled"
    if getattr(server, "os_apply_only_if_updates", True):
        count = getattr(server, "os_updates_count", None)
        if count is not None and int(count) <= 0:
            return "no_updates"
    return None


def container_apply_skip_reason(server) -> str | None:
    """Return a short skip reason, or None if container patch apply should be enqueued."""
    if not server:
        return "missing"
    if not server.container_patch_enabled or not getattr(
        server, "container_apply_enabled", False
    ):
        return "disabled"
    if getattr(server, "container_apply_only_if_updates", True):
        count = getattr(server, "container_updates_count", None)
        if count is not None and int(count) <= 0:
            return "no_updates"
    return None


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
    """Enqueue check-only OS update scan (queued worker pool, non-blocking)."""
    try:
        from ..database import engine
        from .jobs import enqueue_os_update_check
        from ..models import Server

        with Session(engine) as db:
            server = db.get(Server, server_id)
            if not server or not server.os_check_enabled:
                return
            enqueue_os_update_check(server.id)
            logger.info(f"[SCHEDULER] Queued OS update check for server {server_id}")
    except Exception as e:
        logger.warning(f"[SCHEDULER] Error OS check for {server_id}: {e}")


def schedule_container_check_job(server_id: int):
    """Enqueue check-only container image scan (queued worker pool, non-blocking)."""
    try:
        from ..database import engine
        from .jobs import enqueue_container_update_check
        from ..models import Server

        with Session(engine) as db:
            server = db.get(Server, server_id)
            if not server or not server.container_check_enabled:
                return
            enqueue_container_update_check(server.id)
            logger.info(f"[SCHEDULER] Queued container update check for server {server_id}")
    except Exception as e:
        logger.warning(f"[SCHEDULER] Error container check for {server_id}: {e}")


def schedule_os_apply_job(server_id: int):
    """Enqueue OS patch apply if still enabled and (optionally) updates pending."""
    try:
        from ..database import engine
        from .jobs import enqueue_os_patch_apply
        from ..models import Server

        with Session(engine) as db:
            server = db.get(Server, server_id)
            if not server:
                return
            skip = os_apply_skip_reason(server)
            if skip == "disabled":
                logger.info(f"[SCHEDULER] OS apply skipped (disabled) for server {server_id}")
                return
            if skip == "no_updates":
                logger.info(
                    f"[SCHEDULER] OS apply skipped (no updates) for server {server_id}"
                )
                return
            job = enqueue_os_patch_apply(server.id, user_id=None, scheduled=True)
            if job:
                logger.info(
                    f"[SCHEDULER] Queued OS patch apply job #{job.id} for server {server_id}"
                )
            else:
                logger.info(f"[SCHEDULER] OS apply not queued (busy/skip) for server {server_id}")
    except Exception as e:
        logger.warning(f"[SCHEDULER] Error OS apply for {server_id}: {e}")


def schedule_container_apply_job(server_id: int):
    """Enqueue container patch apply if still enabled and (optionally) updates pending."""
    try:
        from ..database import engine
        from .jobs import enqueue_container_patch_apply
        from ..models import Server

        with Session(engine) as db:
            server = db.get(Server, server_id)
            if not server:
                return
            skip = container_apply_skip_reason(server)
            if skip == "disabled":
                logger.info(
                    f"[SCHEDULER] Container apply skipped (disabled) for server {server_id}"
                )
                return
            if skip == "no_updates":
                logger.info(
                    f"[SCHEDULER] Container apply skipped (no image updates) "
                    f"for server {server_id}"
                )
                return
            job = enqueue_container_patch_apply(server.id, user_id=None, scheduled=True)
            if job:
                logger.info(
                    f"[SCHEDULER] Queued container patch apply job #{job.id} "
                    f"for server {server_id}"
                )
            else:
                logger.info(
                    f"[SCHEDULER] Container apply not queued (busy/skip) for server {server_id}"
                )
    except Exception as e:
        logger.warning(f"[SCHEDULER] Error container apply for {server_id}: {e}")


def _remove_job(scheduler, job_id: str):
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass


def server_cron_job_ids(server_id: int) -> list[str]:
    """All APScheduler job ids registered for a fleet server."""
    sid = int(server_id)
    return [
        f"backup_{sid}",
        f"os_check_{sid}",
        f"container_check_{sid}",
        f"os_apply_{sid}",
        f"container_apply_{sid}",
    ]


def unregister_server_cron_jobs(scheduler, HAS_SCHEDULER, server_id: int) -> None:
    """Remove all per-server cron jobs (e.g. when deleting a server from the fleet)."""
    if not HAS_SCHEDULER or not scheduler or not server_id:
        return
    for jid in server_cron_job_ids(server_id):
        _remove_job(scheduler, jid)
    logger.info(f"[SCHEDULER] Unregistered cron jobs for server {server_id}")


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

    # App timezone for check schedules (matches Settings → timezone)
    try:
        from .app_settings import get_app_timezone
        tz = get_app_timezone()
    except Exception:
        tz = None

    # OS update check
    oid = f"os_check_{sid}"
    _remove_job(scheduler, oid)
    if server.os_check_enabled and server.os_check_schedule:
        try:
            trigger = _cron_trigger(server.os_check_schedule, timezone=tz)
            scheduler.add_job(
                func=schedule_os_check_job,
                trigger=trigger,
                args=[sid],
                id=oid,
                replace_existing=True,
                name=f"OS check {server.name}",
            )
            logger.info(f"[SCHEDULER] OS check scheduled for server {sid}: {server.os_check_schedule} ({tz})")
        except Exception as e:
            logger.warning(f"[SCHEDULER] OS check schedule failed for {sid}: {e}")

    # Container update check
    cid = f"container_check_{sid}"
    _remove_job(scheduler, cid)
    if server.container_check_enabled and server.container_check_schedule:
        try:
            trigger = _cron_trigger(server.container_check_schedule, timezone=tz)
            scheduler.add_job(
                func=schedule_container_check_job,
                trigger=trigger,
                args=[sid],
                id=cid,
                replace_existing=True,
                name=f"Container check {server.name}",
            )
            logger.info(
                f"[SCHEDULER] Container check scheduled for server {sid}: "
                f"{server.container_check_schedule} ({tz})"
            )
        except Exception as e:
            logger.warning(f"[SCHEDULER] Container check schedule failed for {sid}: {e}")

    # OS patch apply (opt-in; requires feature flag + explicit apply enable)
    oaid = f"os_apply_{sid}"
    _remove_job(scheduler, oaid)
    if (
        server.os_patch_enabled
        and getattr(server, "os_apply_enabled", False)
        and getattr(server, "os_apply_schedule", None)
    ):
        try:
            trigger = _cron_trigger(server.os_apply_schedule, timezone=tz)
            scheduler.add_job(
                func=schedule_os_apply_job,
                trigger=trigger,
                args=[sid],
                id=oaid,
                replace_existing=True,
                name=f"OS apply {server.name}",
            )
            logger.info(
                f"[SCHEDULER] OS apply scheduled for server {sid}: "
                f"{server.os_apply_schedule} ({tz})"
            )
        except Exception as e:
            logger.warning(f"[SCHEDULER] OS apply schedule failed for {sid}: {e}")

    # Container patch apply (opt-in)
    caid = f"container_apply_{sid}"
    _remove_job(scheduler, caid)
    if (
        server.container_patch_enabled
        and getattr(server, "container_apply_enabled", False)
        and getattr(server, "container_apply_schedule", None)
    ):
        try:
            trigger = _cron_trigger(server.container_apply_schedule, timezone=tz)
            scheduler.add_job(
                func=schedule_container_apply_job,
                trigger=trigger,
                args=[sid],
                id=caid,
                replace_existing=True,
                name=f"Container apply {server.name}",
            )
            logger.info(
                f"[SCHEDULER] Container apply scheduled for server {sid}: "
                f"{server.container_apply_schedule} ({tz})"
            )
        except Exception as e:
            logger.warning(f"[SCHEDULER] Container apply schedule failed for {sid}: {e}")


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


DOCKER_INVENTORY_JOB_ID = "docker_inventory_fleet"
# Refresh stale docker inventories every 10 minutes (L1 SSH per enabled host)
DOCKER_INVENTORY_INTERVAL_MIN = 10


def schedule_docker_inventory_fleet():
    """Periodic refresh of DB docker inventory for hosts with container feature on."""
    try:
        from ..database import engine
        from ..models import Server
        from sqlmodel import select
        from . import docker_inventory as inventory_svc

        with Session(engine) as db:
            servers = db.exec(
                select(Server).where(Server.container_patch_enabled == True)  # noqa: E712
            ).all()
            for server in servers:
                if not server.id:
                    continue
                if inventory_svc.is_stale(server, max_age_sec=inventory_svc.SCHEDULER_STALE_SEC):
                    try:
                        inventory_svc.refresh_server_inventory(server.id, force=False)
                    except Exception as e:
                        logger.warning(
                            f"[SCHEDULER] docker inventory refresh failed for {server.id}: {e}"
                        )
    except Exception as e:
        logger.warning(f"[SCHEDULER] docker inventory fleet job failed: {e}")


def sync_docker_inventory_schedule(scheduler, HAS_SCHEDULER):
    """Register fleet-wide docker inventory refresh interval job."""
    if not HAS_SCHEDULER or not scheduler:
        return
    _remove_job(scheduler, DOCKER_INVENTORY_JOB_ID)
    try:
        from apscheduler.triggers.interval import IntervalTrigger

        scheduler.add_job(
            func=schedule_docker_inventory_fleet,
            trigger=IntervalTrigger(minutes=DOCKER_INVENTORY_INTERVAL_MIN),
            id=DOCKER_INVENTORY_JOB_ID,
            replace_existing=True,
            name="Docker inventory fleet refresh",
        )
        logger.info(
            f"[SCHEDULER] Docker inventory fleet every {DOCKER_INVENTORY_INTERVAL_MIN}m"
        )
    except Exception as e:
        logger.warning(f"[SCHEDULER] Docker inventory schedule failed: {e}")


STACK_HEALTH_JOB_ID = "stack_health_check"
STACK_HEALTH_INTERVAL_MIN = 2


def schedule_stack_health_job():
    """Periodic PiHerder stack health + state-change notifications."""
    try:
        from . import stack_health as stack_svc

        # Lazy import avoids circular import with main at module load
        try:
            from ..main import HAS_SCHEDULER as _hs, scheduler as _sched
        except Exception:
            _hs, _sched = False, None
        stack_svc.run_stack_health_check(
            scheduler=_sched,
            has_scheduler=bool(_hs),
            notify=True,
        )
    except Exception as e:
        logger.warning(f"[SCHEDULER] stack health job failed: {e}")


def sync_stack_health_schedule(scheduler, HAS_SCHEDULER):
    """Register interval job for stack Status checks."""
    if not HAS_SCHEDULER or not scheduler:
        return
    _remove_job(scheduler, STACK_HEALTH_JOB_ID)
    try:
        from apscheduler.triggers.interval import IntervalTrigger

        scheduler.add_job(
            func=schedule_stack_health_job,
            trigger=IntervalTrigger(minutes=STACK_HEALTH_INTERVAL_MIN),
            id=STACK_HEALTH_JOB_ID,
            replace_existing=True,
            name="PiHerder stack health",
        )
        logger.info(
            f"[SCHEDULER] Stack health every {STACK_HEALTH_INTERVAL_MIN}m"
        )
    except Exception as e:
        logger.warning(f"[SCHEDULER] Stack health schedule failed: {e}")


INTEGRATIONS_POLL_JOB_ID = "integrations_poll"
# Default 60s — individual integrations may request longer; we poll all enabled each tick.
INTEGRATIONS_POLL_INTERVAL_SEC = 60


def schedule_integrations_poll_job():
    """Periodic poll of enabled integrations (Uptime Kuma /metrics)."""
    try:
        from .integrations import poll as poll_svc

        poll_svc.poll_all_enabled(notify=True)
    except Exception as e:
        logger.warning(f"[SCHEDULER] integrations poll failed: {e}")


def sync_integrations_poll_schedule(scheduler, HAS_SCHEDULER):
    """Register interval job for external integration status polls."""
    if not HAS_SCHEDULER or not scheduler:
        return
    _remove_job(scheduler, INTEGRATIONS_POLL_JOB_ID)
    try:
        from apscheduler.triggers.interval import IntervalTrigger

        scheduler.add_job(
            func=schedule_integrations_poll_job,
            trigger=IntervalTrigger(seconds=INTEGRATIONS_POLL_INTERVAL_SEC),
            id=INTEGRATIONS_POLL_JOB_ID,
            replace_existing=True,
            name="Integrations poll (Kuma/Grafana/Pi-hole/NPM)",
        )
        logger.info(
            f"[SCHEDULER] Integrations poll every {INTEGRATIONS_POLL_INTERVAL_SEC}s"
        )
    except Exception as e:
        logger.warning(f"[SCHEDULER] Integrations poll schedule failed: {e}")


CERT_RENEW_JOB_ID = "cert_renew_check"
CERT_RENEW_INTERVAL_HOURS = 6


def schedule_cert_renew_job():
    """Check managed certs nearing expiry; renew via NPM and redistribute."""
    try:
        from ..database import engine
        from . import certificates as cert_svc
        from sqlmodel import Session

        with Session(engine) as db:
            results = cert_svc.check_expiring_and_renew(db)
            if results:
                logger.info("[SCHEDULER] cert renew check: %s result(s)", len(results))
    except Exception as e:
        logger.warning(f"[SCHEDULER] cert renew check failed: {e}")


TEMPLATE_DRIFT_JOB_ID = "template_drift_check"
TEMPLATE_DRIFT_INTERVAL_HOURS = 6


def run_template_drift_checks():
    """Scheduled: compare host compose/.env to StackDeployment desired state."""
    try:
        from ..database import engine
        from .service_templates.deploy import check_all_deployments_drift

        with Session(engine) as s:
            res = check_all_deployments_drift(s)
            logger.info("[SCHEDULER] template drift check: %s", res)
    except Exception as e:
        logger.warning("[SCHEDULER] template drift check failed: %s", e)


def sync_template_drift_schedule(scheduler, HAS_SCHEDULER):
    """Register periodic config drift checks for template deployments."""
    if not HAS_SCHEDULER or not scheduler:
        return
    try:
        try:
            scheduler.remove_job(TEMPLATE_DRIFT_JOB_ID)
        except Exception:
            pass
        from apscheduler.triggers.interval import IntervalTrigger

        scheduler.add_job(
            run_template_drift_checks,
            trigger=IntervalTrigger(hours=TEMPLATE_DRIFT_INTERVAL_HOURS),
            id=TEMPLATE_DRIFT_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "[SCHEDULER] template drift every %sh id=%s",
            TEMPLATE_DRIFT_INTERVAL_HOURS,
            TEMPLATE_DRIFT_JOB_ID,
        )
    except Exception as e:
        logger.warning("sync_template_drift_schedule: %s", e)


def sync_cert_renew_schedule(scheduler, HAS_SCHEDULER):
    """Register periodic certificate expiry / renew / distribute job."""
    if not HAS_SCHEDULER or not scheduler:
        return
    _remove_job(scheduler, CERT_RENEW_JOB_ID)
    try:
        from apscheduler.triggers.interval import IntervalTrigger

        scheduler.add_job(
            func=schedule_cert_renew_job,
            trigger=IntervalTrigger(hours=CERT_RENEW_INTERVAL_HOURS),
            id=CERT_RENEW_JOB_ID,
            replace_existing=True,
            name="TLS certificate renew check",
        )
        logger.info(
            f"[SCHEDULER] Cert renew check every {CERT_RENEW_INTERVAL_HOURS}h"
        )
    except Exception as e:
        logger.warning(f"[SCHEDULER] Cert renew schedule failed: {e}")


def sync_herder_backup_schedule(scheduler, HAS_SCHEDULER):
    """Register or remove the global PiHerder self-backup cron job from config."""
    if not HAS_SCHEDULER or not scheduler:
        return
    from . import app_settings as app_cfg

    try:
        scheduler.remove_job(HERDER_SCHEDULE_JOB_ID)
    except Exception:
        pass

    cfg = app_cfg.load_settings()
    if not cfg.get("schedule_enabled"):
        logger.info("[SCHEDULER] PiHerder self-backup schedule disabled")
        return

    cron = (cfg.get("schedule_cron") or "").strip()
    if not cron:
        return

    try:
        app_cfg.validate_cron_expression(cron)
        tz = app_cfg.get_app_timezone()
        trigger = _cron_trigger(cron, timezone=tz)
        scheduler.add_job(
            func=schedule_herder_backup_job,
            trigger=trigger,
            id=HERDER_SCHEDULE_JOB_ID,
            replace_existing=True,
            name="PiHerder self-backup",
        )
        logger.info(f"[SCHEDULER] PiHerder self-backup scheduled: {cron} ({tz})")
    except Exception as e:
        logger.warning(f"[SCHEDULER] Could not register herder backup schedule: {e}")


def schedule_herder_backup_job():
    """Global scheduled PiHerder self-backup (config + keys + optional audit)."""
    logger.info("[SCHEDULER] Running scheduled PiHerder self-backup")
    from ..database import engine
    try:
        from . import herder_backup as hb
        from . import app_settings as app_cfg
        from ..models import AuditLog

        cfg = app_cfg.load_settings()
        mode = cfg.get("schedule_mode", "config_only")
        include_audit = (mode == "full")
        config_only = (mode != "full")
        path = hb.create_herder_backup(include_audit=include_audit, config_only=config_only)
        logger.info(f"[SCHEDULER] PiHerder self-backup written: {path}")
        try:
            with Session(engine) as s:
                from .audit_write import make_audit_log

                al = make_audit_log(
                    user_id=None,
                    server_id=None,
                    action="herder_backup",
                    status="success",
                    details=f"Scheduled self-backup ({mode}): {getattr(path, 'name', path)}",
                    output_snippet=json.dumps({"path": str(path), "mode": mode}),
                    started_at=datetime.utcnow(),
                    finished_at=datetime.utcnow(),
                    client_ip=None,  # system / scheduler — no HTTP request
                )
                s.add(al)
                s.commit()
                try:
                    from .notifications import resolve_by_fingerprint
                    resolve_by_fingerprint(s, "herder_backup_failed")
                except Exception:
                    pass
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

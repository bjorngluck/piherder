"""Global update-check defaults and apply-to-fleet helpers."""
from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session, select

from ..models import Server
from . import app_settings as app_cfg

logger = logging.getLogger(__name__)

# Midnight in app timezone (5-field cron: minute hour day month weekday)
DEFAULT_MIDNIGHT_CRON = "0 0 * * *"
# Daily at 02:00. Fleet apply uses this when a host has backups on and no schedule.
DEFAULT_BACKUP_CRON = "0 2 * * *"
# OS checks use offset 0 and container checks use 15. Backups sit 30 minutes away.
BACKUP_STAGGER_OFFSET = 30


def staggered_cron(base_cron: str, server_id: int, offset: int = 0) -> str:
    """Keep hour/day fields; set minute to (server_id + offset) % 60 for stagger."""
    parts = (base_cron or DEFAULT_MIDNIGHT_CRON).strip().split()
    if len(parts) != 5:
        parts = DEFAULT_MIDNIGHT_CRON.split()
    minute = (int(server_id) + int(offset)) % 60
    parts[0] = str(minute)
    return " ".join(parts)


def _daily_single_hour(parts: list[str]) -> Optional[int]:
    """Hour 0-23 when the base is one hour every day. Otherwise None.

    A list (``2,14``), a step (``*/6``), or any day or weekday limit is not
    a single daily hour. Those bases only stagger the minute.
    """
    if len(parts) != 5:
        return None
    _minute, hour, dom, month, dow = parts
    if dom != "*" or month != "*" or dow != "*":
        return None
    if not str(hour).isdigit():
        return None
    value = int(hour)
    if value < 0 or value > 23:
        return None
    return value


def fleet_backup_cron(base_cron: str, server_id: int) -> str:
    """Stagger one host from its id. This does not read the rest of the fleet.

    A daily base at one hour, such as ``0 2 * * *``, keeps ids below 60 on
    that hour at minute ``(id + 30) % 60``. Later ids take the following
    hours, one minute each, then the hours before the base hour. The first
    1,440 ids each get a distinct minute of the day. Past that, a host
    shares a minute with the id 1,440 earlier. The worker queues them.

    Any other base only changes the minute. A list or a step hour stays as
    written, so hosts 60 apart share that minute instead of silently picking
    one hour out of the list. A saved host schedule is not rewritten here.
    Adding a host does not move anyone else's time.
    """
    parts = (base_cron or DEFAULT_BACKUP_CRON).strip().split()
    if len(parts) != 5:
        parts = DEFAULT_BACKUP_CRON.split()
    sid = int(server_id or 0)
    if sid < 0:
        sid = 0
    hour = _daily_single_hour(parts)
    if hour is None:
        parts[0] = str((sid + BACKUP_STAGGER_OFFSET) % 60)
        return " ".join(parts)
    if sid < 60:
        parts[0] = str((sid + BACKUP_STAGGER_OFFSET) % 60)
        return " ".join(parts)
    extra = sid - 60
    minutes_after = max(0, (23 - hour) * 60)
    if extra < minutes_after:
        parts[0] = str(extra % 60)
        parts[1] = str(hour + 1 + extra // 60)
        return " ".join(parts)
    wrapped = extra - minutes_after
    morning = hour * 60
    if wrapped < morning:
        parts[0] = str(wrapped % 60)
        parts[1] = str(wrapped // 60)
        return " ".join(parts)
    return fleet_backup_cron(base_cron, sid % 1440)


def host_backup_cron(base_cron: str, server_id: int, jitter: bool = True) -> str:
    """Fleet backup time for one host.

    The base clock is the same for the fleet. The minute and, when needed, the
    hour still differ per host id. ``jitter`` is accepted so older callers keep
    working. Backup placement does not use it. Update checks still do.
    """
    del jitter
    return fleet_backup_cron(base_cron or DEFAULT_BACKUP_CRON, server_id)


def _fleet_backup_settings() -> tuple[str, bool]:
    try:
        cfg = app_cfg.load_settings()
        base = (cfg.get("backup_cron") or DEFAULT_BACKUP_CRON).strip() or DEFAULT_BACKUP_CRON
        base = app_cfg.validate_cron_expression(base)
        jitter = bool(cfg.get("update_check_jitter", True))
    except Exception:
        logger.warning("[UPDATE-CHECK] Fleet backup schedule unavailable")
        return DEFAULT_BACKUP_CRON, True
    return base, jitter


def effective_fleet_cron(server_id: int) -> str:
    """Fleet time for a host that has backups on and no saved schedule.

    The saved fleet clock is one time. Each host id still gets its own minute.
    """
    base, _jitter = _fleet_backup_settings()
    return fleet_backup_cron(base, server_id)


def _backup_job_state(server_id: int):
    """Whether a backup job is registered.

    ``None`` means the scheduler cannot answer: this process has no scheduler,
    it is not running yet, or the lookup failed. ``False`` means it is running
    and this host has no ``backup_{id}`` job. ``True`` means that job exists.
    """
    if not server_id:
        return None
    try:
        from ..main import HAS_SCHEDULER, scheduler
    except Exception:
        logger.debug("[UPDATE-CHECK] Scheduler import failed", exc_info=True)
        return None
    if not HAS_SCHEDULER or scheduler is None:
        return None
    if getattr(scheduler, "running", None) is not True:
        return None
    getter = getattr(scheduler, "get_job", None)
    if getter is None:
        return None
    try:
        job = getter(f"backup_{int(server_id)}")
    except Exception:
        logger.debug("[UPDATE-CHECK] Backup job lookup failed", exc_info=True)
        return None
    if job is None:
        return False
    return job


def resolve_backup_schedule(server, *, with_next: bool = True) -> dict:
    """Saved cron, the cron that will run, where it came from, and the live next run.

    ``backup_schedule`` is the host value or null. It is not rewritten here.
    ``backup_schedule_source`` is ``host``, ``fleet``, or null when backups are off
    or no fleet time is available. ``next_backup_run`` is the registered job's
    next run time. ``backup_scheduled`` is null when the scheduler cannot answer,
    and false only when it is running, backups are on, and no job is registered.
    """
    raw = getattr(server, "backup_schedule", None)
    own = (raw or "").strip() or None
    enabled = bool(getattr(server, "backup_enabled", False))
    if not enabled:
        return {
            "backup_schedule": own,
            "effective_backup_schedule": None,
            "backup_schedule_source": None,
            "next_backup_run": None,
            "backup_scheduled": False,
        }
    if own:
        cron, source = own, "host"
    else:
        try:
            cron = effective_fleet_cron(int(getattr(server, "id", 0) or 0))
            source = "fleet"
        except Exception:
            logger.warning("[UPDATE-CHECK] Could not resolve a fleet backup time")
            cron, source = None, None
    scheduled = None
    next_run = None
    if with_next:
        state = _backup_job_state(int(getattr(server, "id", 0) or 0))
        if state is None or state is False:
            scheduled = state
        else:
            scheduled = True
            when = getattr(state, "next_run_time", None)
            if when is not None:
                try:
                    next_run = when.isoformat()
                except Exception:
                    next_run = None
    return {
        "backup_schedule": own,
        "effective_backup_schedule": cron,
        "backup_schedule_source": source,
        "next_backup_run": next_run,
        "backup_scheduled": scheduled,
    }


def copy_backup_schedule_fields(target: dict, server) -> None:
    """Add the effective schedule fields used by the host list and Backups page.

    The next run is the live scheduler job. That lookup does not build a trigger.
    """
    info = resolve_backup_schedule(server, with_next=True)
    target["effective_backup_schedule"] = info["effective_backup_schedule"]
    target["backup_schedule_source"] = info["backup_schedule_source"]
    target["next_backup_run"] = info["next_backup_run"]
    target["backup_scheduled"] = info["backup_scheduled"]


def stamp_new_host_backup_schedule(session: Session, server: Server) -> Optional[str]:
    """Do not write a schedule when a host is added.

    Older callers imported this helper. The backup flag and any saved schedule
    stay as they are. The scheduler derives the fleet time when backups are on
    and the host has no saved schedule.
    """
    del session, server
    return None


def apply_global_update_checks_to_all(
    session: Session,
    *,
    os_enabled: bool = True,
    os_cron: str = DEFAULT_MIDNIGHT_CRON,
    container_enabled: bool = True,
    container_cron: str = DEFAULT_MIDNIGHT_CRON,
    jitter: bool = True,
    only_patch_enabled: bool = False,
    enable_feature_flags: bool = True,
    enable_backups: bool = True,
    backup_cron: str = DEFAULT_BACKUP_CRON,
) -> dict:
    """Write per-server check schedules from global defaults. Returns counts.

    By default applies to *all* servers and turns on related feature flags so the
    Servers list shows OS / Containers (and optionally Backups) as on.
    Backups only flip the flag. A saved backup schedule is left alone, and an
    empty one stays empty so the scheduler can inherit the fleet time.
    """
    os_cron = app_cfg.validate_cron_expression(os_cron or DEFAULT_MIDNIGHT_CRON)
    container_cron = app_cfg.validate_cron_expression(container_cron or DEFAULT_MIDNIGHT_CRON)
    backup_cron = app_cfg.validate_cron_expression(backup_cron or DEFAULT_BACKUP_CRON)

    servers = list(session.exec(select(Server).order_by(Server.id)).all())
    os_n = 0
    cont_n = 0
    flags_os = 0
    flags_cont = 0
    flags_backup = 0
    backup_scheduled = 0
    for s in servers:
        sid = s.id or 0
        # OS
        if os_enabled and (s.os_patch_enabled if only_patch_enabled else True):
            s.os_check_enabled = True
            s.os_check_schedule = (
                staggered_cron(os_cron, sid, offset=0) if jitter else os_cron
            )
            if enable_feature_flags and not s.os_patch_enabled:
                s.os_patch_enabled = True
                flags_os += 1
            elif enable_feature_flags:
                s.os_patch_enabled = True
            os_n += 1
        elif not os_enabled:
            s.os_check_enabled = False
            # leave schedule string for re-enable

        # Containers — offset +15 so OS and image checks rarely collide on same host
        if container_enabled and (s.container_patch_enabled if only_patch_enabled else True):
            s.container_check_enabled = True
            s.container_check_schedule = (
                staggered_cron(container_cron, sid, offset=15) if jitter else container_cron
            )
            if enable_feature_flags:
                if not s.container_patch_enabled:
                    flags_cont += 1
                s.container_patch_enabled = True
            cont_n += 1
        elif not container_enabled:
            # Clear the cron too. A leftover schedule would be registered again
            # on the next scheduler sync while the fleet switch is on.
            s.container_check_enabled = False
            s.container_check_schedule = None

        # Backups. Turn the flag on and leave backup_schedule untouched.
        # Turning the checkbox off does not clear the flag or a saved schedule.
        if enable_backups:
            if not s.backup_enabled:
                flags_backup += 1
            s.backup_enabled = True

        session.add(s)

    session.commit()
    logger.info(
        f"[UPDATE-CHECK] Applied global defaults: os={os_n} container={cont_n} "
        f"flags os+={flags_os} cont+={flags_cont} backup+={flags_backup} "
        f"backup_scheduled={backup_scheduled} "
        f"cron_os={os_cron} cron_container={container_cron} cron_backup={backup_cron} jitter={jitter}"
    )
    return {
        "servers_total": len(servers),
        "os_applied": os_n,
        "container_applied": cont_n,
        "flags_os": flags_os,
        "flags_container": flags_cont,
        "flags_backup": flags_backup,
        "backup_scheduled": backup_scheduled,
        "os_cron": os_cron,
        "container_cron": container_cron,
        "backup_cron": backup_cron,
        "jitter": jitter,
    }


def clear_matching_fleet_backup_schedules(session: Session) -> int:
    """Clear copied fleet minutes once, then never again.

    The first run clears a saved schedule only when it is exactly the fleet
    time for that id. Those hosts keep the same minute as source fleet. Every
    other saved schedule stays. The settings marker is set even when nothing
    matched. A later run, including one where a host has saved that same
    minute, leaves the row alone. One audit row records the count, with no
    host names, and only when something changed.
    """
    if app_cfg.load_settings().get("fleet_backup_inherit_cleaned"):
        logger.info("[UPDATE-CHECK] Fleet backup schedule cleanup already ran")
        return 0
    base, _jitter = _fleet_backup_settings()
    cleared = 0
    for server in session.exec(select(Server).order_by(Server.id)).all():
        saved = (server.backup_schedule or "").strip()
        if not saved or not server.id:
            continue
        if saved != fleet_backup_cron(base, int(server.id)):
            continue
        server.backup_schedule = None
        session.add(server)
        cleared += 1
    if cleared:
        from .server_audit import record_server_audit

        record_server_audit(
            session,
            server_id=None,
            user_id=None,
            action="fleet_backup_schedule_inherit",
            message=f"Cleared {cleared} backup schedule(s) that matched the fleet time",
            details={"cleared": cleared},
        )
        session.commit()
    app_cfg.save_settings({"fleet_backup_inherit_cleaned": True})
    logger.info(
        "[UPDATE-CHECK] Fleet backup schedule cleanup cleared %s host(s)",
        cleared,
    )
    return cleared


def load_update_check_settings() -> dict:
    cfg = app_cfg.load_settings()
    return {
        "os_check_global_enabled": bool(cfg.get("os_check_global_enabled", True)),
        "os_check_cron": cfg.get("os_check_cron") or DEFAULT_MIDNIGHT_CRON,
        "container_check_global_enabled": bool(cfg.get("container_check_global_enabled", True)),
        "container_check_cron": cfg.get("container_check_cron") or DEFAULT_MIDNIGHT_CRON,
        "update_check_jitter": bool(cfg.get("update_check_jitter", True)),
        "backup_cron": cfg.get("backup_cron") or DEFAULT_BACKUP_CRON,
    }

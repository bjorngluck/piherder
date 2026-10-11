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


def fleet_backup_cron(base_cron: str, server_id: int) -> str:
    """Stagger one host from its id. This does not read the rest of the fleet.

    A daily cron has 1440 minute slots. Ids below 60 keep minute
    ``(id + 30) % 60`` on the base hour, which already fills that hour.
    Later ids take the following hours, one minute each. The first 1440 ids
    therefore each get a different minute of the day, which covers a fleet of
    hundreds or a thousand hosts. Past that, the day repeats, so a few hosts
    can share a minute instead of a whole hour firing together. Adding a host
    does not move anyone else's time.
    """
    parts = (base_cron or DEFAULT_BACKUP_CRON).strip().split()
    if len(parts) != 5:
        parts = DEFAULT_BACKUP_CRON.split()
    sid = int(server_id or 0)
    if not str(parts[1]).isdigit():
        parts[0] = str((sid + BACKUP_STAGGER_OFFSET) % 60)
        return " ".join(parts)
    if sid < 60:
        parts[0] = str((sid + BACKUP_STAGGER_OFFSET) % 60)
        return " ".join(parts)
    extra = sid - 60
    parts[0] = str(extra % 60)
    parts[1] = str((int(parts[1]) + 1 + extra // 60) % 24)
    return " ".join(parts)


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


def resolve_backup_schedule(server, *, with_next: bool = True) -> dict:
    """Saved cron, the cron that will run, where it came from, and the next run.

    ``backup_schedule`` is the host value or null. It is not rewritten here.
    ``backup_schedule_source`` is ``host``, ``fleet``, or null when backups are off
    or no fleet time is available. ``next_backup_run`` is an ISO time in the
    Settings timezone.
    """
    raw = getattr(server, "backup_schedule", None)
    own = (raw or "").strip() or None
    if not bool(getattr(server, "backup_enabled", False)):
        return {
            "backup_schedule": own,
            "effective_backup_schedule": None,
            "backup_schedule_source": None,
            "next_backup_run": None,
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
    return {
        "backup_schedule": own,
        "effective_backup_schedule": cron,
        "backup_schedule_source": source,
        "next_backup_run": _next_cron_iso(cron) if cron and with_next else None,
    }


def copy_backup_schedule_fields(target: dict, server) -> None:
    """Add the effective schedule fields used by the host list and Backups page.

    Skips the next-run calculation. A list of hundreds of hosts should not
    build a trigger per row.
    """
    info = resolve_backup_schedule(server, with_next=False)
    target["effective_backup_schedule"] = info["effective_backup_schedule"]
    target["backup_schedule_source"] = info["backup_schedule_source"]


def _next_cron_iso(cron: str | None) -> str | None:
    if not cron or not str(cron).strip():
        return None
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from .app_settings import get_app_timezone
        from .scheduler import _cron_trigger

        tz_name = get_app_timezone()
        tz = ZoneInfo(tz_name) if tz_name else None
        trigger = _cron_trigger(str(cron).strip(), timezone=tz_name)
        now = datetime.now(tz) if tz else datetime.now()
        nxt = trigger.get_next_fire_time(None, now)
        if nxt is None:
            return None
        return nxt.isoformat()
    except Exception:
        logger.debug("[UPDATE-CHECK] Next backup run unavailable", exc_info=True)
        return None


def stamp_new_host_backup_schedule(session: Session, server: Server) -> Optional[str]:
    """Give a new backup-enabled host the fleet time when it has none.

    Returns the cron written, or None when the host already has a schedule
    or backups are off. A settings read failure leaves the host unchanged.
    """
    if not server.backup_enabled or (server.backup_schedule or "").strip():
        return None
    try:
        cfg = app_cfg.load_settings()
        base = app_cfg.validate_cron_expression(
            (cfg.get("backup_cron") or DEFAULT_BACKUP_CRON).strip() or DEFAULT_BACKUP_CRON
        )
        jitter = bool(cfg.get("update_check_jitter", True))
    except Exception:
        logger.warning("[UPDATE-CHECK] Fleet backup schedule unavailable for a new host")
        return None
    cron = host_backup_cron(base, int(server.id or 0), jitter)
    server.backup_schedule = cron
    session.add(server)
    session.commit()
    return cron


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
    When backups are included, a host with no backup schedule gets the fleet time.
    A saved backup schedule is left alone.
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
            s.container_check_enabled = False

        # Backups. Turn the flag on, and write a schedule only when the host has none.
        # A saved schedule stays. Turning the checkbox off does not clear a schedule.
        if enable_backups:
            if not s.backup_enabled:
                flags_backup += 1
            s.backup_enabled = True
            if not (s.backup_schedule or "").strip():
                s.backup_schedule = host_backup_cron(backup_cron, sid, jitter)
                backup_scheduled += 1

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

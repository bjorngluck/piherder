"""Global update-check defaults and apply-to-fleet helpers."""
from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session, select

from ..models import Server
from . import herder_backup as hb

logger = logging.getLogger(__name__)

# Midnight in app timezone (5-field cron: minute hour day month weekday)
DEFAULT_MIDNIGHT_CRON = "0 0 * * *"


def staggered_cron(base_cron: str, server_id: int, offset: int = 0) -> str:
    """Keep hour/day fields; set minute to (server_id + offset) % 60 for stagger."""
    parts = (base_cron or DEFAULT_MIDNIGHT_CRON).strip().split()
    if len(parts) != 5:
        parts = DEFAULT_MIDNIGHT_CRON.split()
    minute = (int(server_id) + int(offset)) % 60
    parts[0] = str(minute)
    return " ".join(parts)


def apply_global_update_checks_to_all(
    session: Session,
    *,
    os_enabled: bool = True,
    os_cron: str = DEFAULT_MIDNIGHT_CRON,
    container_enabled: bool = True,
    container_cron: str = DEFAULT_MIDNIGHT_CRON,
    jitter: bool = True,
    only_patch_enabled: bool = False,
) -> dict:
    """Write per-server check schedules from global defaults. Returns counts.

    By default applies to *all* servers (check-only does not require patch-apply toggles).
    Set only_patch_enabled=True to limit to hosts with os/container patch features on.
    """
    os_cron = hb.validate_cron_expression(os_cron or DEFAULT_MIDNIGHT_CRON)
    container_cron = hb.validate_cron_expression(container_cron or DEFAULT_MIDNIGHT_CRON)

    servers = list(session.exec(select(Server).order_by(Server.id)).all())
    os_n = 0
    cont_n = 0
    for s in servers:
        sid = s.id or 0
        # OS
        if os_enabled and (s.os_patch_enabled if only_patch_enabled else True):
            s.os_check_enabled = True
            s.os_check_schedule = (
                staggered_cron(os_cron, sid, offset=0) if jitter else os_cron
            )
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
            cont_n += 1
        elif not container_enabled:
            s.container_check_enabled = False

        session.add(s)

    session.commit()
    logger.info(
        f"[UPDATE-CHECK] Applied global defaults: os={os_n} container={cont_n} "
        f"cron_os={os_cron} cron_container={container_cron} jitter={jitter}"
    )
    return {
        "servers_total": len(servers),
        "os_applied": os_n,
        "container_applied": cont_n,
        "os_cron": os_cron,
        "container_cron": container_cron,
        "jitter": jitter,
    }


def load_update_check_settings() -> dict:
    cfg = hb.load_herder_config()
    return {
        "os_check_global_enabled": bool(cfg.get("os_check_global_enabled", True)),
        "os_check_cron": cfg.get("os_check_cron") or DEFAULT_MIDNIGHT_CRON,
        "container_check_global_enabled": bool(cfg.get("container_check_global_enabled", True)),
        "container_check_cron": cfg.get("container_check_cron") or DEFAULT_MIDNIGHT_CRON,
        "update_check_jitter": bool(cfg.get("update_check_jitter", True)),
    }

"""Cheap fleet snapshot for HA coordinators (v1.6 HA-p2 optional GET /api/v1/summary).

DB reads only — never SSH.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from ..version_info import APP_VERSION

ACTIVE_JOB = frozenset({"pending", "running"})
MOVE_JOB = "service_migrate"


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    try:
        return dt.isoformat()
    except Exception:
        return None


def fleet_summary(
    servers: Iterable[Any],
    jobs: Iterable[Any],
    *,
    version: str | None = None,
) -> dict[str, Any]:
    """Aggregate host rows + active jobs into the Slice 1 heartbeat payload."""
    rows = list(servers)
    job_rows = list(jobs)
    os_n = 0
    cont_n = 0
    reboot_n = 0
    backups: list[datetime] = []
    for s in rows:
        ou = getattr(s, "os_updates_count", None)
        if ou is not None and int(ou) > 0:
            os_n += 1
        cu = getattr(s, "container_updates_count", None)
        if cu is not None and int(cu) > 0:
            cont_n += 1
        if bool(getattr(s, "reboot_pending", False)):
            reboot_n += 1
        lb = getattr(s, "last_backup_at", None)
        if isinstance(lb, datetime):
            backups.append(lb)

    active = [j for j in job_rows if str(getattr(j, "status", "")).lower() in ACTIVE_JOB]
    move_running = any(
        str(getattr(j, "job_type", "")).lower() == MOVE_JOB for j in active
    )
    oldest = min(backups) if backups else None
    return {
        "ok": True,
        "version": version or APP_VERSION,
        "hosts": len(rows),
        "os_updates": os_n,
        "container_updates": cont_n,
        "reboot_pending": reboot_n,
        "jobs_running": len(active),
        "move_running": bool(move_running),
        "last_backup_oldest_at": _iso(oldest),
    }

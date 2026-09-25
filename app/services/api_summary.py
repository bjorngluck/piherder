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


OS_LABELS = {
    "haos": "HAOS",
    "ubuntu": "Ubuntu",
    "debian": "Debian",
    "raspbian": "Raspberry Pi OS",
    "raspberrypi": "Raspberry Pi OS",
    "alpine": "Alpine",
    "fedora": "Fedora",
    "rhel": "RHEL",
    "centos": "CentOS",
    "arch": "Arch",
    "linux": "Linux",
}


def os_display_label(
    os_type: str | None,
    summary_json: str | None = None,
    *,
    os_pretty: str | None = None,
    os_id: str | None = None,
) -> str:
    """Human OS for HA: HAOS / Ubuntu / Debian — not a raw default of debian."""
    import json

    if os_pretty and str(os_pretty).strip():
        pretty = str(os_pretty).strip()
        oid = (os_id or "").strip().lower()
        pl = pretty.lower()
        if oid in ("haos", "hassos") or "home assistant os" in pl or "hassos" in pl:
            return pretty if "home assistant" in pl or "haos" in pl else "HAOS"
        return pretty
    ot = (os_id or os_type or "").strip().lower()
    pretty = ""
    if summary_json:
        try:
            data = json.loads(summary_json)
            if isinstance(data, dict):
                ident = data.get("identity") or {}
                if isinstance(ident, dict):
                    rel = ident.get("os_release") or {}
                    pretty = str(
                        ident.get("os_release_name")
                        or (rel.get("pretty_name") if isinstance(rel, dict) else "")
                        or ident.get("name")
                        or ""
                    )
                ha = data.get("ha") or {}
                if isinstance(ha, dict) and not pretty:
                    host = ha.get("host") or {}
                    if isinstance(host, dict):
                        pretty = str(host.get("operating_system") or "")
        except Exception:
            pretty = ""
    pl = pretty.lower()
    if "haos" in ot or "hassos" in pl or "home assistant os" in pl or (
        "home assistant" in pl and "os" in pl
    ):
        return "HAOS"
    if "ubuntu" in ot or "ubuntu" in pl:
        return "Ubuntu"
    if "raspb" in ot or "raspberry" in pl:
        return "Raspberry Pi OS"
    if ot in OS_LABELS:
        return OS_LABELS[ot]
    if pretty:
        return pretty
    return "Linux"


def fleet_summary(
    servers: Iterable[Any],
    jobs: Iterable[Any],
    *,
    version: str | None = None,
    alerts_open: int = 0,
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

    def _n(obj, name: str) -> int:
        try:
            v = getattr(obj, name, None)
            if v is None and isinstance(obj, dict):
                v = obj.get(name)
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

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
        "alerts_open": int(alerts_open or 0),
        "cpu_cores": sum(_n(s, "cpu_cores") for s in rows),
        "memory_total_bytes": sum(_n(s, "memory_total_bytes") for s in rows),
        "memory_used_bytes": sum(_n(s, "memory_used_bytes") for s in rows),
        "disk_total_bytes": sum(_n(s, "disk_total_bytes") for s in rows),
        "disk_used_bytes": sum(_n(s, "disk_used_bytes") for s in rows),
        "containers": sum(_n(s, "container_count") if hasattr(s, "container_count") else 0 for s in rows),
    }

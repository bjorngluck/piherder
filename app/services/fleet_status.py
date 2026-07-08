"""Fleet-wide patch / update status from persisted check fields (no SSH)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from ..models import Server


def _server_attention(s: Server) -> bool:
    os_n = s.os_updates_count
    cont_n = s.container_updates_count
    return bool(
        s.reboot_pending
        or (os_n is not None and os_n > 0)
        or (cont_n is not None and cont_n > 0)
    )


def server_status_row(s: Server) -> dict[str, Any]:
    """One row for fleet table UI."""
    os_n = s.os_updates_count
    cont_n = s.container_updates_count
    needs = _server_attention(s)
    # Parse container project names from summary JSON if present
    projects: list[str] = []
    if s.container_updates_summary:
        try:
            import json
            data = json.loads(s.container_updates_summary)
            if isinstance(data, dict):
                projects = list(data.get("projects") or [])[:12]
        except Exception:
            pass

    return {
        "id": s.id,
        "name": s.name,
        "hostname": s.hostname,
        "os_patch_enabled": s.os_patch_enabled,
        "container_patch_enabled": s.container_patch_enabled,
        "os_check_enabled": s.os_check_enabled,
        "container_check_enabled": s.container_check_enabled,
        "os_updates_count": os_n,
        "container_updates_count": cont_n,
        "reboot_pending": bool(s.reboot_pending),
        "last_os_check_at": s.last_os_check_at,
        "last_container_check_at": s.last_container_check_at,
        "last_backup_at": s.last_backup_at,
        "container_projects": projects,
        "needs_attention": needs,
        "never_checked_os": os_n is None and s.last_os_check_at is None,
        "never_checked_containers": cont_n is None and s.last_container_check_at is None,
    }


def summarize_fleet(servers: List[Server]) -> dict[str, Any]:
    """Aggregate patch-status metrics across the fleet (DB fields only)."""
    rows = [server_status_row(s) for s in servers]
    attention = [r for r in rows if r["needs_attention"]]
    reboots = [r for r in rows if r["reboot_pending"]]
    os_hosts = [r for r in rows if r["os_updates_count"] is not None and r["os_updates_count"] > 0]
    cont_hosts = [
        r for r in rows if r["container_updates_count"] is not None and r["container_updates_count"] > 0
    ]
    total_os_pkgs = sum(r["os_updates_count"] or 0 for r in rows)
    total_cont_proj = sum(r["container_updates_count"] or 0 for r in rows)
    never_os = sum(1 for r in rows if r["never_checked_os"] and r["os_patch_enabled"])
    never_cont = sum(1 for r in rows if r["never_checked_containers"] and r["container_patch_enabled"])

    # Sort: attention first, then name
    rows_sorted = sorted(
        rows,
        key=lambda r: (0 if r["needs_attention"] else 1, (r["name"] or "").lower()),
    )

    return {
        "server_count": len(rows),
        "attention_count": len(attention),
        "reboot_count": len(reboots),
        "os_host_count": len(os_hosts),
        "container_host_count": len(cont_hosts),
        "total_os_packages": total_os_pkgs,
        "total_container_projects": total_cont_proj,
        "never_checked_os": never_os,
        "never_checked_containers": never_cont,
        "rows": rows_sorted,
        "healthy_count": len(rows) - len(attention),
    }

"""Thin fleet-health widget registry (Stream N). DB reads only — no SSH."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from sqlmodel import Session, func, select

from ..config import settings
from ..models import Job, ManagedCertificate, NmapDevice, Notification, Server
from . import alert_policy as apol
from . import docker_inventory as inv
from .certificates import days_until_expiry
from .integrations.registry import TYPE_NMAP, list_integrations

logger = logging.getLogger(__name__)

WIDGET_IDS: tuple[str, ...] = (
    "alerts_by_severity",
    "backups_stale",
    "certs_expiring",
    "jobs_failed_24h",
    "nmap_queue",
    "map_infra",
    "docker_fleet",
)

CERT_WINDOW_DAYS = 30
JOBS_FAILED_HOURS = 24
ITEM_CAP = 5
MAP_INFRA_TYPES: tuple[str, ...] = ("host_down", "map_infra_down")
NMAP_NEW = "new"
NMAP_OFFLINE = "stale"


@dataclass(frozen=True)
class WidgetSpec:
    id: str
    label: str
    collect: Callable[[Session], dict[str, Any]]


def backup_stale_hours() -> int:
    return max(1, int(getattr(settings, "METRICS_BACKUP_STALE_HOURS", 36) or 36))


def backup_counts(
    servers: list[Server], stale_hours: Optional[int] = None
) -> tuple[int, int]:
    """Return (backup_enabled, stale) using last_backup_at vs cutoff."""
    hours = backup_stale_hours() if stale_hours is None else max(1, int(stale_hours))
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    enabled = 0
    stale = 0
    for s in servers:
        if not getattr(s, "backup_enabled", False):
            continue
        enabled += 1
        last = getattr(s, "last_backup_at", None)
        if last is None or last < cutoff:
            stale += 1
    return enabled, stale


def count_jobs_failed_24h(session: Session) -> int:
    try:
        since = datetime.utcnow() - timedelta(hours=JOBS_FAILED_HOURS)
        n = session.exec(
            select(func.count())
            .select_from(Job)
            .where(Job.status == "failed", Job.finished_at >= since)
        ).one()
        return int(n or 0)
    except Exception:
        return 0


def _widget(
    *,
    id: str,
    label: str,
    href: str,
    value: int,
    value_label: str,
    rows: list[dict[str, Any]],
    empty_hint: str,
    parts: Optional[list[dict[str, Any]]] = None,
    hot: Optional[bool] = None,
    href_label: str = "Open full list →",
) -> dict[str, Any]:
    n = int(value or 0)
    return {
        "id": id,
        "label": label,
        "version": 1,
        "href": href,
        "href_label": href_label,
        "value": n,
        "value_label": value_label,
        "hot": bool(n > 0) if hot is None else bool(hot),
        "parts": parts or [],
        "rows": rows[:ITEM_CAP],
        "empty_hint": empty_hint,
    }


def _item(
    label: str,
    href: str,
    *,
    sub: str = "",
    tone: str = "",
) -> dict[str, str]:
    return {"label": label, "href": href, "sub": sub, "tone": tone}


def collect_alerts_by_severity(session: Session) -> dict[str, Any]:
    counts = {s: 0 for s in apol.SEVERITIES}
    try:
        rows = session.exec(
            select(Notification.severity, func.count())
            .where(Notification.status == "open")
            .group_by(Notification.severity)
        ).all()
        for sev, n in rows:
            key = (sev or "warning").strip().lower()
            if key in counts:
                counts[key] = int(n or 0)
            else:
                counts["warning"] = counts.get("warning", 0) + int(n or 0)
    except Exception:
        logger.debug("alerts severity counts failed", exc_info=True)
    total = sum(counts.values())
    latest = []
    try:
        latest = list(
            session.exec(
                select(Notification)
                .where(Notification.status == "open")
                .order_by(Notification.updated_at.desc())
                .limit(ITEM_CAP)
            ).all()
        )
    except Exception:
        latest = []
    items = [
        _item(
            n.title or n.type or "Alert",
            n.link_url or "/notifications",
            sub=(n.severity or "warning"),
            tone="crit" if (n.severity or "") == "critical" else (
                "warn" if (n.severity or "") == "warning" else ""
            ),
        )
        for n in latest
    ]
    parts = [
        {"n": counts["critical"], "l": "critical", "tone": "crit"},
        {"n": counts["warning"], "l": "warning", "tone": "warn"},
        {"n": counts["info"], "l": "info", "tone": ""},
    ]
    return _widget(
        id="alerts_by_severity",
        label="Open alerts",
        href="/notifications",
        value=total,
        value_label="open",
        rows=items,
        empty_hint="No open alerts.",
        parts=parts,
        hot=counts["critical"] > 0 or total > 0,
    )


def collect_backups_stale(session: Session) -> dict[str, Any]:
    hours = backup_stale_hours()
    servers = list(session.exec(select(Server).order_by(Server.name)).all())
    enabled, stale_n = backup_counts(servers, hours)
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    items: list[dict[str, str]] = []
    for s in servers:
        if not s.backup_enabled:
            continue
        last = s.last_backup_at
        if last is not None and last >= cutoff:
            continue
        sid = s.id
        sub = "never backed up" if last is None else "older than stale window"
        items.append(
            _item(
                s.name or s.hostname or f"#{sid}",
                f"/servers/{sid}/backups" if sid else "/servers",
                sub=sub,
                tone="warn",
            )
        )
    return _widget(
        id="backups_stale",
        label="Backups stale",
        href="/servers",
        value=stale_n,
        value_label=f"stale / {enabled} enabled",
        rows=items,
        empty_hint=(
            "No backup-enabled hosts."
            if enabled == 0
            else f"All enabled backups ran within {hours}h."
        ),
        parts=[{"n": enabled, "l": "enabled", "tone": ""}],
        hot=stale_n > 0,
    )


def collect_certs_expiring(session: Session) -> dict[str, Any]:
    rows = list(session.exec(select(ManagedCertificate).order_by(ManagedCertificate.name)).all())
    due: list[tuple[int, Any]] = []
    expired = 0
    for c in rows:
        days = days_until_expiry(c.not_after)
        if days is None:
            continue
        if days < 0:
            expired += 1
            due.append((days, c))
        elif days <= CERT_WINDOW_DAYS:
            due.append((days, c))
    due.sort(key=lambda t: t[0])
    items = []
    for days, c in due[:ITEM_CAP]:
        cid = c.id
        if days < 0:
            sub, tone = "expired", "crit"
        else:
            sub, tone = f"{days}d left", "warn"
        items.append(
            _item(
                c.name or f"cert #{cid}",
                f"/certificates/{cid}" if cid else "/certificates",
                sub=sub,
                tone=tone,
            )
        )
    n = len(due)
    return _widget(
        id="certs_expiring",
        label="Certs expiring",
        href="/certificates",
        value=n,
        value_label=f"≤{CERT_WINDOW_DAYS}d",
        rows=items,
        empty_hint=f"No vault certs expiring within {CERT_WINDOW_DAYS} days.",
        parts=[
            {"n": expired, "l": "expired", "tone": "crit"},
            {"n": n - expired, "l": f"≤{CERT_WINDOW_DAYS}d", "tone": "warn"},
        ],
        hot=n > 0,
    )


def collect_jobs_failed_24h(session: Session) -> dict[str, Any]:
    since = datetime.utcnow() - timedelta(hours=JOBS_FAILED_HOURS)
    n = count_jobs_failed_24h(session)
    rows = list(
        session.exec(
            select(Job)
            .where(Job.status == "failed", Job.finished_at >= since)
            .order_by(Job.finished_at.desc())
            .limit(ITEM_CAP)
        ).all()
    )
    items = []
    for j in rows:
        kind = (j.job_type or "job").replace("_", " ")
        items.append(
            _item(
                kind,
                "/jobs?status=failed",
                sub=(j.details or "")[:80],
                tone="warn",
            )
        )
    return _widget(
        id="jobs_failed_24h",
        label="Jobs failed",
        href="/jobs?status=failed",
        value=n,
        value_label="last 24h",
        rows=items,
        empty_hint="No failed jobs in the last 24 hours.",
        hot=n > 0,
    )


def _first_nmap_href(session: Session) -> str:
    try:
        rows = list_integrations(session, type_filter=TYPE_NMAP)
    except Exception:
        rows = []
    if rows:
        iid = getattr(rows[0], "id", None)
        if iid:
            return f"/integrations/{iid}"
    return "/dns/physical#map"


def _nmap_label(d: NmapDevice) -> str:
    return (
        (d.display_name or "").strip()
        or (d.hostname or "").strip()
        or (d.ip_address or "").strip()
        or f"device #{d.id}"
    )


def _nmap_href(d: NmapDevice) -> str:
    try:
        from .dns_fabric.core import hosts_map_url

        if d.id:
            return hosts_map_url(discovery_id=int(d.id))
    except Exception:
        pass
    return "/dns/physical#map"


def collect_nmap_queue(session: Session) -> dict[str, Any]:
    new_n = 0
    offline_n = 0
    try:
        new_n = int(
            session.exec(
                select(func.count())
                .select_from(NmapDevice)
                .where(NmapDevice.state == NMAP_NEW)
            ).one()
            or 0
        )
        offline_n = int(
            session.exec(
                select(func.count())
                .select_from(NmapDevice)
                .where(NmapDevice.state == NMAP_OFFLINE)
            ).one()
            or 0
        )
    except Exception:
        logger.debug("nmap counts failed", exc_info=True)
    latest = list(
        session.exec(
            select(NmapDevice)
            .where(NmapDevice.state.in_((NMAP_NEW, NMAP_OFFLINE)))  # type: ignore[attr-defined]
            .order_by(NmapDevice.updated_at.desc())
            .limit(ITEM_CAP)
        ).all()
    )
    items = [
        _item(
            _nmap_label(d),
            _nmap_href(d),
            sub="new" if d.state == NMAP_NEW else "offline",
            tone="warn" if d.state == NMAP_NEW else "",
        )
        for d in latest
    ]
    total = new_n + offline_n
    has_nmap = False
    try:
        has_nmap = bool(list_integrations(session, type_filter=TYPE_NMAP))
    except Exception:
        has_nmap = False
    hint = (
        "No LAN discovery yet — add nmap in Catalog."
        if not has_nmap
        else "No new or offline discovered devices."
    )
    return _widget(
        id="nmap_queue",
        label="LAN discovery",
        href=_first_nmap_href(session),
        value=total,
        value_label="new + offline",
        rows=items,
        empty_hint=hint,
        parts=[
            {"n": new_n, "l": "new", "tone": "warn"},
            {"n": offline_n, "l": "offline", "tone": ""},
        ],
        hot=total > 0,
    )


def _open_type_count(session: Session, type_id: str) -> int:
    try:
        n = session.exec(
            select(func.count())
            .select_from(Notification)
            .where(Notification.status == "open", Notification.type == type_id)
        ).one()
        return int(n or 0)
    except Exception:
        return 0


def collect_map_infra(session: Session) -> dict[str, Any]:
    host_n = _open_type_count(session, "host_down")
    map_n = _open_type_count(session, "map_infra_down")
    rows = list(
        session.exec(
            select(Notification)
            .where(
                Notification.status == "open",
                Notification.type.in_(MAP_INFRA_TYPES),  # type: ignore[attr-defined]
            )
            .order_by(Notification.updated_at.desc())
            .limit(ITEM_CAP)
        ).all()
    )
    items = [
        _item(
            r.title or r.type,
            r.link_url
            or (
                "/notifications?category=host"
                if r.type == "host_down"
                else "/notifications?category=map_infra"
            ),
            sub="host" if r.type == "host_down" else "map infra",
            tone="crit" if r.type == "host_down" else "warn",
        )
        for r in rows
    ]
    total = host_n + map_n
    return _widget(
        id="map_infra",
        label="Map / host down",
        href="/notifications?category=host",
        value=total,
        value_label="open",
        rows=items,
        empty_hint="No host-down or gateway/WAN alerts.",
        parts=[
            {"n": host_n, "l": "host", "tone": "crit"},
            {"n": map_n, "l": "map infra", "tone": "warn"},
        ],
        hot=total > 0,
    )


def _docker_host_row(server: Server) -> Optional[dict[str, Any]]:
    enabled = bool(getattr(server, "container_patch_enabled", False))
    data = inv.parse_inventory(server)
    if not enabled and data is None:
        return None
    meta = inv.inventory_meta(server)
    status = (meta.get("status") or "never").strip() or "never"
    running = 0
    counted = 0
    projects = int(meta.get("project_count") or 0)
    if data:
        for p in data.get("projects") or []:
            for c in p.get("containers") or []:
                if c.get("placeholder"):
                    continue
                counted += 1
                if c.get("running"):
                    running += 1
        for c in data.get("orphan_containers") or []:
            counted += 1
            if c.get("running"):
                running += 1
        if not projects:
            projects = len(data.get("projects") or [])
    total = int(meta.get("container_count") or counted or 0)
    attention = status in ("never", "error", "stale")
    return {
        "id": server.id,
        "name": server.name or server.hostname or f"#{server.id}",
        "projects": projects,
        "containers": total,
        "running": running,
        "status": status,
        "attention": attention,
    }


def collect_docker_fleet(session: Session) -> dict[str, Any]:
    servers = list(session.exec(select(Server).order_by(Server.name)).all())
    rows: list[dict[str, Any]] = []
    for s in servers:
        row = _docker_host_row(s)
        if row:
            rows.append(row)
    containers = sum(int(r["containers"] or 0) for r in rows)
    running = sum(int(r["running"] or 0) for r in rows)
    projects = sum(int(r["projects"] or 0) for r in rows)
    attention_n = sum(1 for r in rows if r["attention"])
    ranked = sorted(
        rows,
        key=lambda r: (0 if r["attention"] else 1, (r["name"] or "").lower()),
    )
    items = []
    for r in ranked[:ITEM_CAP]:
        sid = r["id"]
        bits = [f"{r['running']}/{r['containers']} up"]
        if r["projects"]:
            bits.append(f"{r['projects']} stacks")
        bits.append(r["status"])
        items.append(
            _item(
                r["name"],
                f"/servers/{sid}/docker" if sid else "/servers",
                sub=" · ".join(bits),
                tone="warn" if r["attention"] else "",
            )
        )
    return _widget(
        id="docker_fleet",
        label="Docker",
        href="/servers",
        value=containers,
        value_label="containers",
        rows=items,
        empty_hint="No Docker inventory yet — enable Docker on a host.",
        parts=[
            {"n": running, "l": "running", "tone": ""},
            {"n": projects, "l": "stacks", "tone": ""},
            {"n": attention_n, "l": "stale/never", "tone": "warn"},
        ],
        hot=attention_n > 0,
    )


WIDGETS: tuple[WidgetSpec, ...] = (
    WidgetSpec("alerts_by_severity", "Open alerts", collect_alerts_by_severity),
    WidgetSpec("backups_stale", "Backups stale", collect_backups_stale),
    WidgetSpec("certs_expiring", "Certs expiring", collect_certs_expiring),
    WidgetSpec("jobs_failed_24h", "Jobs failed", collect_jobs_failed_24h),
    WidgetSpec("nmap_queue", "LAN discovery", collect_nmap_queue),
    WidgetSpec("map_infra", "Map / host down", collect_map_infra),
    WidgetSpec("docker_fleet", "Docker", collect_docker_fleet),
)


def collect_board(session: Session) -> list[dict[str, Any]]:
    """Fixed N2 board — order is the registry order (not user-customizable)."""
    out: list[dict[str, Any]] = []
    for spec in WIDGETS:
        try:
            card = spec.collect(session)
        except Exception:
            logger.exception("insights widget %s failed", spec.id)
            card = _widget(
                id=spec.id,
                label=spec.label,
                href="/",
                value=0,
                value_label="unavailable",
                rows=[],
                empty_hint="Could not load this card.",
                hot=False,
            )
        out.append(card)
    return out

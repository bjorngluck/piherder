"""PiHerder operational reports from Job history (not Grafana / not status portlets)."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlmodel import Session, func, select

from ..models import (
    AuditLog,
    ConsoleTranscript,
    Integration,
    Job,
    NmapDevice,
    NmapScanRun,
    Server,
)
from .backup_profiles import human_size
from .console_audit import parse_kv_details

logger = logging.getLogger(__name__)

REPORT_DAY_CHOICES = (7, 30, 90)
DEFAULT_REPORT_DAYS = 30
HISTORY_DAYS = 365

# apt-get: "12 upgraded, 1 newly installed, 0 to remove and 3 not upgraded."
_APT_COUNTS_RE = re.compile(
    r"(\d+)\s+upgraded,\s+(\d+)\s+newly installed",
    re.IGNORECASE,
)

_BACKUP_TYPES = ("backup",)
_OS_PATCH_TYPES = ("os_patch",)
_DOCKER_DEPLOY_TYPES = (
    "docker_stack_deploy",
    "docker_stack_stop",
    "docker_stack_start",
    "docker_stack_restart",
    "template_deploy",
    "template_redeploy",
)
_DOCKER_PATCH_TYPES = ("container_patch",)
_DOCKER_JOB_TYPES = _DOCKER_DEPLOY_TYPES + _DOCKER_PATCH_TYPES
_CONSOLE_ACTIONS = ("ssh_console_open", "ssh_console_close", "ssh_console_denied")


def clamp_report_days(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_REPORT_DAYS
    return n if n in REPORT_DAY_CHOICES else DEFAULT_REPORT_DAYS


def _app_day(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    try:
        from .app_settings import get_app_timezone

        tz_name = get_app_timezone()
        aware = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        return aware.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    except Exception:
        return dt.strftime("%Y-%m-%d")


def _day_list(end: datetime, days: int) -> list[str]:
    days = max(1, int(days))
    out: list[str] = []
    for i in range(days - 1, -1, -1):
        out.append(_app_day(end - timedelta(days=i)))
    return out


def _parse_details(job: Job) -> dict[str, Any]:
    try:
        data = json.loads(job.details or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def backup_dest_bytes(details: dict[str, Any]) -> int:
    """Dest tree size after a run (not bytes transferred — rsync skips unchanged files)."""
    summary = details.get("result_summary")
    if not isinstance(summary, dict):
        summary = details
    total = summary.get("total_size_bytes")
    if total is None:
        results = summary.get("results") or []
        total = 0
        for r in results:
            if not isinstance(r, dict):
                continue
            try:
                total += int(r.get("size_bytes") or 0)
            except (TypeError, ValueError):
                continue
    try:
        return max(0, int(total or 0))
    except (TypeError, ValueError):
        return 0


def os_packages_applied(details: dict[str, Any]) -> Optional[int]:
    """Best-effort packages from apt log in job details; None if unknown."""
    blob = ""
    try:
        blob = json.dumps(details)
    except Exception:
        blob = str(details)
    m = _APT_COUNTS_RE.search(blob)
    if not m:
        return None
    try:
        return int(m.group(1)) + int(m.group(2))
    except (TypeError, ValueError):
        return None


def _load_jobs(session: Session, types: tuple[str, ...], since: datetime) -> list[Job]:
    try:
        return list(
            session.exec(
                select(Job)
                .where(
                    Job.job_type.in_(types),  # type: ignore[attr-defined]
                    Job.finished_at >= since,
                    Job.status.in_(("success", "failed")),  # type: ignore[attr-defined]
                )
                .order_by(Job.finished_at.asc())
            ).all()
        )
    except Exception:
        logger.debug("ops_reports job load failed", exc_info=True)
        return []


def _empty_day_row(day: str) -> dict[str, Any]:
    return {
        "day": day,
        "ok": 0,
        "fail": 0,
        "bytes": 0,
        "packages": 0,
        "packages_known": False,
        "dest_bytes": 0,
        "ok_pct": 0,
    }


def _host_label(servers: dict[int, Server], server_id: Optional[int]) -> str:
    if server_id is None:
        return "unknown"
    s = servers.get(int(server_id))
    if not s:
        return f"#{server_id}"
    return s.name or s.hostname or f"#{server_id}"


def collect_backup_history(
    session: Session,
    *,
    days: int = DEFAULT_REPORT_DAYS,
    now: Optional[datetime] = None,
    jobs: Optional[list[Job]] = None,
    servers: Optional[dict[int, Server]] = None,
) -> dict[str, Any]:
    now = now or datetime.utcnow()
    days = max(1, int(days))
    since = now - timedelta(days=days)
    if jobs is None:
        all_jobs = _load_jobs(session, _BACKUP_TYPES, now - timedelta(days=HISTORY_DAYS))
    else:
        all_jobs = [
            j
            for j in jobs
            if j.job_type == "backup"
            and j.finished_at
            and j.status in ("success", "failed")
        ]
    window_jobs = [j for j in all_jobs if j.finished_at and j.finished_at >= since]
    if servers is None:
        servers = {int(s.id): s for s in session.exec(select(Server)).all() if s.id}

    day_keys = _day_list(now, days)
    by_day = {d: _empty_day_row(d) for d in day_keys}
    last_dest_by_host: dict[int, int] = {}
    host_rows: dict[int, dict[str, Any]] = {}
    ok_n = fail_n = 0

    # Seed dest occupancy from successes before the visible window.
    for j in all_jobs:
        if j.status != "success" or not j.finished_at or j.finished_at >= since:
            continue
        dest = backup_dest_bytes(_parse_details(j))
        if dest and j.server_id is not None:
            last_dest_by_host[int(j.server_id)] = dest

    dest_by_day: dict[str, list[tuple[int, int]]] = {d: [] for d in day_keys}
    for j in window_jobs:
        day = _app_day(j.finished_at)
        if day not in by_day:
            continue
        row = by_day[day]
        sid = int(j.server_id) if j.server_id is not None else 0
        dest = backup_dest_bytes(_parse_details(j)) if j.status == "success" else 0
        if j.status == "success":
            row["ok"] += 1
            ok_n += 1
            if dest:
                dest_by_day[day].append((sid, dest))
        else:
            row["fail"] += 1
            fail_n += 1
        hr = host_rows.setdefault(
            sid,
            {
                "server_id": j.server_id,
                "name": _host_label(servers, j.server_id),
                "href": f"/servers/{j.server_id}/backups" if j.server_id else "/servers",
                "ok": 0,
                "fail": 0,
                "dest_bytes": 0,
                "last_day": "",
            },
        )
        if j.status == "success":
            hr["ok"] += 1
            if dest:
                hr["dest_bytes"] = dest
        else:
            hr["fail"] += 1
        hr["last_day"] = day

    occupancy: list[int] = []
    for day in day_keys:
        for sid, dest in dest_by_day[day]:
            last_dest_by_host[sid] = dest
        total = sum(last_dest_by_host.values())
        by_day[day]["dest_bytes"] = total
        occupancy.append(total)
        tot = by_day[day]["ok"] + by_day[day]["fail"]
        by_day[day]["ok_pct"] = int(round(100 * by_day[day]["ok"] / tot)) if tot else 0

    max_dest = max(occupancy) if occupancy else 0
    day_rows = []
    for d in day_keys:
        r = by_day[d]
        r["dest_human"] = human_size(int(r["dest_bytes"] or 0))
        r["bar_pct"] = int(round(100 * r["dest_bytes"] / max_dest)) if max_dest else 0
        day_rows.append(r)

    first_dest = occupancy[0] if occupancy else 0
    last_dest = occupancy[-1] if occupancy else 0
    delta = last_dest - first_dest

    hosts = sorted(host_rows.values(), key=lambda h: (h["name"] or "").lower())
    for h in hosts:
        h["dest_human"] = human_size(int(h["dest_bytes"] or 0))

    dest_now = _backup_dest_now()
    total_runs = ok_n + fail_n
    return {
        "days": days,
        "ok": ok_n,
        "fail": fail_n,
        "runs": total_runs,
        "ok_pct": int(round(100 * ok_n / total_runs)) if total_runs else 0,
        "dest_last_human": human_size(last_dest),
        "dest_delta": delta,
        "dest_delta_human": human_size(abs(delta)),
        "dest_grew": delta > 0,
        "dest_shrank": delta < 0,
        "dest_now": dest_now,
        "day_rows": day_rows,
        "hosts": hosts,
        "empty": total_runs == 0,
        "note": (
            "Dest size is the rsync destination tree after each successful run "
            "(unchanged files still count). Not bytes transferred that day."
        ),
        "jobs_href": "/jobs?job_type=backup",
    }


def _backup_dest_now() -> dict[str, Any]:
    """Point-in-time dest usage from last Settings → Status check (no du here)."""
    out = {"human": None, "message": None, "checked_at": None}
    try:
        from .stack_health import load_last_report

        last = load_last_report() or {}
        out["checked_at"] = last.get("checked_at")
        for c in last.get("components") or []:
            cid = (c.get("id") or "")
            if cid == "disk_used_backups":
                detail = c.get("detail") or {}
                b = detail.get("tree_bytes")
                if b is not None:
                    out["human"] = human_size(int(b))
                    out["message"] = c.get("message")
                    return out
            if cid == "disk_mount_backups":
                out["message"] = c.get("message")
        return out
    except Exception:
        return out


def collect_os_patch_history(
    session: Session,
    *,
    days: int = DEFAULT_REPORT_DAYS,
    now: Optional[datetime] = None,
    jobs: Optional[list[Job]] = None,
    servers: Optional[dict[int, Server]] = None,
    all_jobs_year: Optional[list[Job]] = None,
) -> dict[str, Any]:
    now = now or datetime.utcnow()
    days = max(1, int(days))
    since = now - timedelta(days=days)
    if servers is None:
        servers = {int(s.id): s for s in session.exec(select(Server)).all() if s.id}
    if jobs is None:
        jobs = _load_jobs(session, _OS_PATCH_TYPES, since)
    else:
        jobs = [
            j
            for j in jobs
            if j.job_type == "os_patch"
            and j.finished_at
            and j.finished_at >= since
            and j.status in ("success", "failed")
        ]
    year_jobs = all_jobs_year
    if year_jobs is None:
        year_jobs = _load_jobs(
            session, _OS_PATCH_TYPES, now - timedelta(days=HISTORY_DAYS)
        )
    else:
        year_jobs = [
            j
            for j in year_jobs
            if j.job_type == "os_patch"
            and j.finished_at
            and j.status in ("success", "failed")
        ]

    enabled_n = sum(
        1 for s in servers.values() if getattr(s, "os_patch_enabled", False)
    )
    host_denom = enabled_n or len(
        {int(j.server_id) for j in year_jobs if j.server_id is not None}
    )

    day_keys = _day_list(now, days)
    by_day = {d: _empty_day_row(d) for d in day_keys}
    host_rows: dict[int, dict[str, Any]] = {}
    ok_n = fail_n = 0
    pkg_n = 0
    pkg_jobs = 0

    for j in jobs:
        day = _app_day(j.finished_at)
        if day not in by_day:
            continue
        row = by_day[day]
        details = _parse_details(j)
        pkgs = os_packages_applied(details)
        sid = int(j.server_id) if j.server_id is not None else 0
        if j.status == "success":
            row["ok"] += 1
            ok_n += 1
            if pkgs is not None:
                row["packages"] += pkgs
                row["packages_known"] = True
                pkg_n += pkgs
                pkg_jobs += 1
        else:
            row["fail"] += 1
            fail_n += 1
        tot = row["ok"] + row["fail"]
        row["ok_pct"] = int(round(100 * row["ok"] / tot)) if tot else 0
        hr = host_rows.setdefault(
            sid,
            {
                "server_id": j.server_id,
                "name": _host_label(servers, j.server_id),
                "href": f"/servers/{j.server_id}" if j.server_id else "/servers",
                "ok": 0,
                "fail": 0,
                "packages": 0,
                "packages_known": False,
                "last_day": "",
            },
        )
        if j.status == "success":
            hr["ok"] += 1
            if pkgs is not None:
                hr["packages"] += pkgs
                hr["packages_known"] = True
        else:
            hr["fail"] += 1
        hr["last_day"] = day

    max_ok = max((r["ok"] for r in by_day.values()), default=0)
    day_rows = []
    for d in day_keys:
        r = by_day[d]
        r["bar_pct"] = int(round(100 * r["ok"] / max_ok)) if max_ok else 0
        day_rows.append(r)

    hosts = sorted(host_rows.values(), key=lambda h: (h["name"] or "").lower())
    rates = _patch_rates(year_jobs, now, host_denom)

    total_runs = ok_n + fail_n
    return {
        "days": days,
        "ok": ok_n,
        "fail": fail_n,
        "runs": total_runs,
        "ok_pct": int(round(100 * ok_n / total_runs)) if total_runs else 0,
        "packages": pkg_n,
        "packages_known": pkg_jobs > 0,
        "host_denom": host_denom,
        "enabled_hosts": enabled_n,
        "avg_packages_per_host": (
            round(pkg_n / host_denom, 1) if host_denom and pkg_jobs else None
        ),
        "rates": rates,
        "day_rows": day_rows,
        "hosts": hosts,
        "empty": total_runs == 0,
        "note": (
            "Package counts come from apt “upgraded, newly installed” lines when "
            "the apply job stored them. HAOS and older jobs may only show apply runs."
        ),
        "jobs_href": "/jobs?job_type=os_patch",
    }


def _patch_rates(jobs: list[Job], now: datetime, host_denom: int) -> list[dict[str, Any]]:
    """Average successful applies (and packages when known) per host per period."""
    windows = (
        ("week", 7),
        ("month", 30),
        ("year", 365),
    )
    out = []
    denom = max(1, int(host_denom or 1))
    for label, span in windows:
        since = now - timedelta(days=span)
        ok = 0
        pkgs = 0
        pkg_jobs = 0
        for j in jobs:
            if not j.finished_at or j.finished_at < since:
                continue
            if j.status != "success":
                continue
            ok += 1
            n = os_packages_applied(_parse_details(j))
            if n is not None:
                pkgs += n
                pkg_jobs += 1
        out.append(
            {
                "label": label,
                "days": span,
                "applies": ok,
                "applies_per_host": round(ok / denom, 2),
                "packages": pkgs if pkg_jobs else None,
                "packages_per_host": (
                    round(pkgs / denom, 1) if pkg_jobs else None
                ),
            }
        )
    return out


def _scan_occupancy(
    runs: list[NmapScanRun],
    day_keys: list[str],
    since: datetime,
) -> list[int]:
    """Carry-forward hosts_up (last success per integration) across day_keys."""
    last_up: dict[int, int] = {}
    by_day: dict[str, list[NmapScanRun]] = {}
    for run in runs:
        if run.status != "success" or not run.finished_at:
            continue
        if run.finished_at < since:
            last_up[int(run.integration_id or 0)] = int(run.hosts_up or 0)
            continue
        by_day.setdefault(_app_day(run.finished_at), []).append(run)
    occ: list[int] = []
    for day in day_keys:
        for run in by_day.get(day) or []:
            last_up[int(run.integration_id or 0)] = int(run.hosts_up or 0)
        occ.append(sum(last_up.values()))
    return occ


def _lan_href(session: Session) -> str:
    try:
        row = session.exec(
            select(Integration)
            .where(Integration.type == "nmap")
            .order_by(Integration.id.asc())
        ).first()
        if row and row.id:
            return f"/integrations/{row.id}"
    except Exception:
        pass
    return "/dns/physical#map"


def collect_lan_history(
    session: Session,
    *,
    days: int = DEFAULT_REPORT_DAYS,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Live devices per day from NmapScanRun.hosts_up (last successful scan, carry-forward)."""
    now = now or datetime.utcnow()
    days = max(1, int(days))
    since = now - timedelta(days=days)
    since_hist = now - timedelta(days=HISTORY_DAYS)
    try:
        runs = list(
            session.exec(
                select(NmapScanRun)
                .where(NmapScanRun.finished_at >= since_hist)
                .order_by(NmapScanRun.finished_at.asc())
            ).all()
        )
    except Exception:
        logger.debug("ops_reports nmap runs failed", exc_info=True)
        runs = []

    day_keys = _day_list(now, days)
    by_day = {
        d: {
            "day": d,
            "live": 0,
            "scans_ok": 0,
            "scans_fail": 0,
            "hosts_total": 0,
            "bar_pct": 0,
        }
        for d in day_keys
    }

    for run in runs:
        if not run.finished_at or run.finished_at < since:
            continue
        day = _app_day(run.finished_at)
        if day not in by_day:
            continue
        row = by_day[day]
        if run.status == "success":
            row["scans_ok"] += 1
            row["hosts_total"] = int(run.hosts_total or 0)
        elif run.status == "failed":
            row["scans_fail"] += 1

    occupancy = _scan_occupancy(runs, day_keys, since)
    for day, live in zip(day_keys, occupancy):
        by_day[day]["live"] = live

    max_live = max(occupancy) if occupancy else 0
    day_rows = []
    for d in day_keys:
        r = by_day[d]
        r["bar_pct"] = int(round(100 * r["live"] / max_live)) if max_live else 0
        day_rows.append(r)

    first_live = occupancy[0] if occupancy else 0
    last_live = occupancy[-1] if occupancy else 0
    delta = last_live - first_live

    vis_ok = sum(r["scans_ok"] for r in day_rows)
    vis_fail = sum(r["scans_fail"] for r in day_rows)

    # Current catalog (not history): devices not stale/ignored.
    live_now = stale_now = ignored_now = 0
    new_window = 0
    try:
        for state, n in session.exec(
            select(NmapDevice.state, func.count())
            .select_from(NmapDevice)
            .group_by(NmapDevice.state)
        ).all():
            st = (state or "").strip()
            c = int(n or 0)
            if st in ("new", "known", "linked"):
                live_now += c
            elif st == "stale":
                stale_now += c
            elif st == "ignored":
                ignored_now += c
        new_window = int(
            session.exec(
                select(func.count())
                .select_from(NmapDevice)
                .where(NmapDevice.first_seen_at >= since)
            ).one()
            or 0
        )
    except Exception:
        logger.debug("ops_reports nmap device counts failed", exc_info=True)

    rates = []
    for label, span in (("week", 7), ("month", 30), ("year", 365)):
        span_keys = _day_list(now, span)
        span_since = now - timedelta(days=span)
        vals = _scan_occupancy(runs, span_keys, span_since)
        avg = round(sum(vals) / len(vals), 1) if vals else 0
        rates.append(
            {
                "label": label,
                "days": span,
                "avg_live": avg,
                "min_live": min(vals) if vals else 0,
                "max_live": max(vals) if vals else 0,
            }
        )

    href = _lan_href(session)
    return {
        "days": days,
        "live_last": last_live,
        "live_delta": delta,
        "live_grew": delta > 0,
        "live_shrank": delta < 0,
        "scans_ok": vis_ok,
        "scans_fail": vis_fail,
        "live_now": live_now,
        "stale_now": stale_now,
        "ignored_now": ignored_now,
        "new_window": new_window,
        "rates": rates,
        "day_rows": day_rows,
        "empty": vis_ok == 0 and last_live == 0,
        "href": href,
        "note": (
            "Live count is nmap hosts_up from the last successful scan that day, "
            "carried forward when no scan ran. Multiple LAN integrations are summed "
            "(overlapping CIDRs can double-count)."
        ),
    }


def _fmt_duration(seconds: int) -> str:
    s = max(0, int(seconds or 0))
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h}h {m}m" if m else f"{h}h"
    d, h = divmod(h, 24)
    return f"{d}d {h}h"


def collect_docker_history(
    session: Session,
    *,
    days: int = DEFAULT_REPORT_DAYS,
    now: Optional[datetime] = None,
    jobs: Optional[list[Job]] = None,
    servers: Optional[dict[int, Server]] = None,
) -> dict[str, Any]:
    """Stack deploys/lifecycle + container patches from Jobs; inventory snapshot is now-only."""
    now = now or datetime.utcnow()
    days = max(1, int(days))
    since = now - timedelta(days=days)
    if jobs is None:
        jobs = _load_jobs(session, _DOCKER_JOB_TYPES, since)
    else:
        jobs = [
            j
            for j in jobs
            if j.job_type in _DOCKER_JOB_TYPES
            and j.finished_at
            and j.finished_at >= since
            and j.status in ("success", "failed")
        ]
    if servers is None:
        servers = {int(s.id): s for s in session.exec(select(Server)).all() if s.id}

    day_keys = _day_list(now, days)
    by_day = {
        d: {
            "day": d,
            "deploy_ok": 0,
            "deploy_fail": 0,
            "patch_ok": 0,
            "patch_fail": 0,
        }
        for d in day_keys
    }
    host_rows: dict[int, dict[str, Any]] = {}
    deploy_ok = deploy_fail = patch_ok = patch_fail = 0

    def _host(sid: Optional[int]) -> dict[str, Any]:
        key = int(sid) if sid is not None else 0
        return host_rows.setdefault(
            key,
            {
                "server_id": sid,
                "name": _host_label(servers, sid),
                "href": f"/servers/{sid}/docker" if sid else "/servers",
                "deploy_ok": 0,
                "deploy_fail": 0,
                "patch_ok": 0,
                "patch_fail": 0,
            },
        )

    for j in jobs:
        day = _app_day(j.finished_at)
        if day not in by_day:
            continue
        row = by_day[day]
        hr = _host(j.server_id)
        is_patch = j.job_type in _DOCKER_PATCH_TYPES
        ok = j.status == "success"
        if is_patch:
            if ok:
                row["patch_ok"] += 1
                hr["patch_ok"] += 1
                patch_ok += 1
            else:
                row["patch_fail"] += 1
                hr["patch_fail"] += 1
                patch_fail += 1
        else:
            if ok:
                row["deploy_ok"] += 1
                hr["deploy_ok"] += 1
                deploy_ok += 1
            else:
                row["deploy_fail"] += 1
                hr["deploy_fail"] += 1
                deploy_fail += 1

    day_rows = [by_day[d] for d in day_keys]
    hosts = sorted(host_rows.values(), key=lambda h: (h["name"] or "").lower())

    running_now = total_now = stacks_now = 0
    try:
        from .insights import _docker_host_row

        for s in servers.values():
            snap = _docker_host_row(s)
            if not snap:
                continue
            running_now += int(snap["running"] or 0)
            total_now += int(snap["containers"] or 0)
            stacks_now += int(snap["projects"] or 0)
    except Exception:
        logger.debug("ops_reports docker snapshot failed", exc_info=True)

    runs = deploy_ok + deploy_fail + patch_ok + patch_fail
    return {
        "days": days,
        "deploy_ok": deploy_ok,
        "deploy_fail": deploy_fail,
        "patch_ok": patch_ok,
        "patch_fail": patch_fail,
        "runs": runs,
        "running_now": running_now,
        "total_now": total_now,
        "stacks_now": stacks_now,
        "day_rows": day_rows,
        "hosts": hosts,
        "empty": runs == 0,
        "jobs_href": "/jobs?job_type=docker_stack_deploy",
        "note": (
            "Deploys include compose up/stop/start/restart and template deploy. "
            "Patches are container image applies. Running/total is the last inventory "
            "snapshot (not a daily census — we do not store Docker history per day)."
        ),
    }


def collect_console_history(
    session: Session,
    *,
    days: int = DEFAULT_REPORT_DAYS,
    now: Optional[datetime] = None,
    servers: Optional[dict[int, Server]] = None,
) -> dict[str, Any]:
    """Web-console sessions from AuditLog open/close (works even when transcript audit is off)."""
    now = now or datetime.utcnow()
    days = max(1, int(days))
    since = now - timedelta(days=days)
    if servers is None:
        servers = {int(s.id): s for s in session.exec(select(Server)).all() if s.id}
    try:
        logs = list(
            session.exec(
                select(AuditLog)
                .where(
                    AuditLog.action.in_(_CONSOLE_ACTIONS),  # type: ignore[attr-defined]
                    AuditLog.started_at >= since,
                )
                .order_by(AuditLog.started_at.asc())
            ).all()
        )
    except Exception:
        logger.debug("ops_reports console audit load failed", exc_info=True)
        logs = []

    day_keys = _day_list(now, days)
    by_day = {
        d: {"day": d, "opens": 0, "privileged": 0, "denied": 0, "seconds": 0}
        for d in day_keys
    }
    host_rows: dict[int, dict[str, Any]] = {}
    opens = priv = denied = 0
    seconds = 0

    def _host(sid: Optional[int]) -> dict[str, Any]:
        key = int(sid) if sid is not None else 0
        return host_rows.setdefault(
            key,
            {
                "server_id": sid,
                "name": _host_label(servers, sid),
                "href": f"/servers/{sid}/console" if sid else "/servers",
                "opens": 0,
                "privileged": 0,
                "denied": 0,
                "seconds": 0,
            },
        )

    for log in logs:
        day = _app_day(log.started_at)
        if day not in by_day:
            continue
        row = by_day[day]
        hr = _host(log.server_id)
        kv = parse_kv_details(log.details)
        if log.action == "ssh_console_open":
            row["opens"] += 1
            hr["opens"] += 1
            opens += 1
            ident = (kv.get("identity") or "").lower()
            if ident.startswith("privileged"):
                row["privileged"] += 1
                hr["privileged"] += 1
                priv += 1
        elif log.action == "ssh_console_denied":
            row["denied"] += 1
            hr["denied"] += 1
            denied += 1
        elif log.action == "ssh_console_close":
            try:
                dur = int(kv.get("duration_sec") or 0)
            except (TypeError, ValueError):
                dur = 0
            if dur > 0:
                row["seconds"] += dur
                hr["seconds"] += dur
                seconds += dur

    cmds = 0
    t_sessions = 0
    try:
        for t in session.exec(
            select(ConsoleTranscript).where(ConsoleTranscript.created_at >= since)
        ).all():
            t_sessions += 1
            cmds += int(getattr(t, "command_count", 0) or 0)
    except Exception:
        logger.debug("ops_reports transcripts failed", exc_info=True)

    for d in day_keys:
        by_day[d]["duration"] = _fmt_duration(by_day[d]["seconds"])
    for h in host_rows.values():
        h["duration"] = _fmt_duration(h["seconds"])

    hosts = sorted(host_rows.values(), key=lambda h: (h["name"] or "").lower())
    day_rows = [by_day[d] for d in day_keys]
    return {
        "days": days,
        "opens": opens,
        "privileged": priv,
        "denied": denied,
        "seconds": seconds,
        "duration": _fmt_duration(seconds),
        "cmds": cmds,
        "transcripts": t_sessions,
        "day_rows": day_rows,
        "hosts": hosts,
        "empty": opens == 0 and denied == 0,
        "href": "/audit?action=ssh_console_open",
        "note": (
            "Sessions come from Audit (ssh_console_open/close), so this works even when "
            "command audit is off. Duration is from close events. Commands logged only "
            "when Settings → Console audit is on."
        ),
    }


def collect_ops_reports(
    session: Session, *, days: int = DEFAULT_REPORT_DAYS
) -> dict[str, Any]:
    days = clamp_report_days(days)
    now = datetime.utcnow()
    since_year = now - timedelta(days=HISTORY_DAYS)
    servers = {int(s.id): s for s in session.exec(select(Server)).all() if s.id}
    year_jobs = _load_jobs(
        session,
        _BACKUP_TYPES + _OS_PATCH_TYPES + _DOCKER_JOB_TYPES,
        since_year,
    )
    backup_jobs = [j for j in year_jobs if j.job_type == "backup"]
    os_jobs = [j for j in year_jobs if j.job_type == "os_patch"]
    docker_jobs = [j for j in year_jobs if j.job_type in _DOCKER_JOB_TYPES]
    return {
        "days": days,
        "day_choices": list(REPORT_DAY_CHOICES),
        "backup": collect_backup_history(
            session, days=days, now=now, jobs=backup_jobs, servers=servers
        ),
        "os_patch": collect_os_patch_history(
            session,
            days=days,
            now=now,
            jobs=os_jobs,
            servers=servers,
            all_jobs_year=os_jobs,
        ),
        "lan": collect_lan_history(session, days=days, now=now),
        "docker": collect_docker_history(
            session, days=days, now=now, jobs=docker_jobs, servers=servers
        ),
        "console": collect_console_history(
            session, days=days, now=now, servers=servers
        ),
    }

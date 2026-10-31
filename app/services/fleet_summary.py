"""Read-only fleet summary. Counts and states. No SSH and no secrets.

This is not the cheap heartbeat at GET /api/v1/summary.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import or_
from sqlmodel import Session, select

from ..models import BackupDestination, Job, Notification, Server

SEEN_MINUTES = 15
_FAILED = "failed"
_DRIVE_ALERTS = frozenset({
    "drive_oauth_refresh_failed",
    "drive_oauth_token_aging",
    "backup_destination_auth_failed",
})


def parse_since(raw: str | None) -> Optional[datetime]:
    """None when omitted. ValueError when the text is not ISO-8601."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError("since") from exc


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _changed(moment: Optional[datetime], *stamps: Optional[datetime]) -> bool:
    if moment is None:
        return True
    for stamp in stamps:
        seen = _utc(stamp)
        if seen is not None and seen >= moment:
            return True
    return False


def _host_state(server: Server, now: datetime) -> str:
    seen = _utc(getattr(server, "last_seen", None))
    if seen is None:
        return "unknown"
    if seen >= now - timedelta(minutes=SEEN_MINUTES):
        return "seen"
    return "quiet"


def _jobs(
    session: Session,
    *,
    job_type: str | None = None,
    status: str | None = None,
    since: Optional[datetime] = None,
) -> list[Job]:
    """Jobs filtered in SQL. ``since`` omitted keeps the full status or type set."""
    stmt = select(Job)
    if job_type:
        stmt = stmt.where(Job.job_type == job_type)
    if status:
        stmt = stmt.where(Job.status == status)
    if since is not None:
        naive = since.astimezone(timezone.utc).replace(tzinfo=None)
        stmt = stmt.where(or_(Job.finished_at >= naive, Job.created_at >= naive))
    return list(session.exec(stmt).all())


def build_fleet_summary(session: Session, *, since: Optional[datetime], now: Optional[datetime] = None) -> dict[str, Any]:
    """Current snapshot, or only the areas that changed after ``since``."""
    moment = _utc(since)
    clock = _utc(now) or datetime.now(timezone.utc)
    servers = list(session.exec(select(Server)).all())
    failed_jobs_all = _jobs(session, status=_FAILED, since=moment)
    backup_jobs = _jobs(session, job_type="backup", since=moment)
    drive_job_rows = _jobs(session, job_type="backup_replicate", since=moment)
    alerts = list(
        session.exec(select(Notification).where(Notification.status == "open")).all()
    )
    drives = list(
        session.exec(select(BackupDestination).where(BackupDestination.provider == "drive")).all()
    )
    areas: dict[str, Any] = {}

    host_rows = [
        server for server in servers
        if _changed(moment, getattr(server, "created_at", None), getattr(server, "last_seen", None))
    ]
    if moment is None or host_rows:
        states: dict[str, int] = {}
        ids = []
        for server in host_rows:
            state = _host_state(server, clock)
            states[state] = states.get(state, 0) + 1
            if server.id is not None:
                ids.append(int(server.id))
        areas["hosts"] = {"count": len(host_rows), "states": states, "ids": ids}

    alert_rows = [
        row for row in alerts
        if _changed(moment, getattr(row, "created_at", None), getattr(row, "updated_at", None))
    ]
    if moment is None or alert_rows:
        by_severity: dict[str, int] = {}
        for row in alert_rows:
            key = (row.severity or "warning").strip().lower() or "warning"
            by_severity[key] = by_severity.get(key, 0) + 1
        areas["alerts"] = {"open": len(alert_rows), "states": by_severity}

    failed = [
        job for job in failed_jobs_all
        if _changed(moment, job.finished_at, job.created_at)
    ]
    if moment is None or failed:
        areas["failed_jobs"] = {
            "count": len(failed),
            "state": "failed" if failed else "none",
            "ids": [int(job.id) for job in failed if job.id is not None],
        }

    backup_hosts = [
        server for server in servers
        if _changed(moment, getattr(server, "last_backup_at", None))
    ]
    backup_changed = moment is None or backup_hosts or any(
        _changed(moment, job.finished_at, job.created_at) for job in backup_jobs
    )
    if backup_changed:
        failed_n = sum(1 for job in backup_jobs if (job.status or "") == _FAILED and _changed(moment, job.finished_at, job.created_at))
        ok_n = sum(1 for job in backup_jobs if (job.status or "") == "success" and _changed(moment, job.finished_at, job.created_at))
        if failed_n:
            state = "failed"
        elif ok_n or backup_hosts:
            state = "ok"
        else:
            state = "none"
        areas["backups"] = {
            "count": len(backup_hosts) if moment is not None else sum(1 for server in servers if getattr(server, "backup_enabled", False)),
            "state": state,
            "failed": failed_n,
        }

    drive_jobs = drive_job_rows
    drive_alerts = [row for row in alerts if (row.type or "") in _DRIVE_ALERTS]
    drive_changed = moment is None or any(
        _changed(moment, getattr(row, "updated_at", None)) for row in drives
    ) or any(_changed(moment, job.finished_at, job.created_at) for job in drive_jobs) or any(
        _changed(moment, row.updated_at, row.created_at) for row in drive_alerts
    )
    if drive_changed:
        failed_drive = sum(1 for job in drive_jobs if (job.status or "") == _FAILED and _changed(moment, job.finished_at, job.created_at))
        open_auth = len(drive_alerts) if moment is None else len([
            row for row in drive_alerts if _changed(moment, row.updated_at, row.created_at)
        ])
        if failed_drive or open_auth:
            state = "failed"
        elif drives:
            state = "ok"
        else:
            state = "none"
        areas["drive"] = {"count": len(drives), "state": state, "failed": failed_drive}

    update_hosts = [
        server for server in servers
        if _changed(
            moment,
            getattr(server, "last_os_check_at", None),
            getattr(server, "last_container_check_at", None),
        )
    ]
    if moment is None or update_hosts:
        pending = 0
        for server in servers if moment is None else update_hosts:
            try:
                os_n = int(getattr(server, "os_updates_count", 0) or 0)
            except (TypeError, ValueError):
                os_n = 0
            try:
                image_n = int(getattr(server, "container_updates_count", 0) or 0)
            except (TypeError, ValueError):
                image_n = 0
            if os_n or image_n:
                pending += 1
        areas["updates"] = {"pending": pending, "state": "pending" if pending else "none"}

    return {
        "ok": True,
        "since": moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None,
        "areas": areas,
    }

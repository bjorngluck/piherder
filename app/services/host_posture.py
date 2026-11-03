"""Per-host reachability and a one-time critical alert.

Unreachable means the host has not been seen for 24 hours. Degraded means it
has been quiet for 15 minutes, or a backup slot was missed. Two missed backup
slots raise one critical alert. A repeat of the same condition does not send
another push. Seeing the host, or a backup success, resolves the matching alert.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session

UNREACHABLE_AFTER = timedelta(hours=24)
DEGRADED_AFTER = timedelta(minutes=15)
HOST_UNREACHABLE = "host_unreachable"
BACKUP_MISSED_REPEAT = "backup_missed_repeat"


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _server_name(server) -> str:
    return (
        (getattr(server, "name", None) or "").strip()
        or (getattr(server, "hostname", None) or "").strip()
        or f"#{getattr(server, 'id', '')}"
    )


def missed_backup_streak(server, jobs, now: datetime, tz_name: str) -> tuple[int, Optional[datetime]]:
    """How many latest due backup slots were missed, and when the latest one was."""
    from .schedule_watch import backup_cron_for, classify_slot, due_slot

    cron = backup_cron_for(server)
    if not cron:
        return 0, None
    streak = 0
    latest: Optional[datetime] = None
    moment = _as_utc(now) or datetime.now(timezone.utc)
    born = getattr(server, "created_at", None)
    for _ in range(8):
        slot = due_slot(cron, tz_name, moment, born)
        if slot is None:
            break
        if classify_slot(slot, jobs) != "missed":
            break
        streak += 1
        if latest is None:
            latest = slot
        if streak >= 2:
            break
        moment = slot - timedelta(seconds=1)
    return streak, latest


def host_posture(
    server,
    now: datetime,
    *,
    missed_backups: int = 0,
    missed_at: Optional[datetime] = None,
) -> dict:
    """``status`` is healthy, degraded, or unreachable. ``duration_seconds`` is time in that status."""
    moment = _as_utc(now) or datetime.now(timezone.utc)
    seen = _as_utc(getattr(server, "last_seen", None))
    born = _as_utc(getattr(server, "created_at", None))
    anchor = seen or born
    quiet = (moment - anchor) if anchor is not None else timedelta(0)
    missed_at = _as_utc(missed_at)
    if anchor is not None and quiet >= UNREACHABLE_AFTER:
        status = "unreachable"
        since = anchor
    elif (anchor is not None and quiet >= DEGRADED_AFTER) or missed_backups >= 1:
        status = "degraded"
        marks = []
        if anchor is not None and quiet >= DEGRADED_AFTER:
            marks.append(anchor)
        if missed_backups >= 1 and missed_at is not None:
            marks.append(missed_at)
        since = min(marks) if marks else (anchor or moment)
    else:
        status = "healthy"
        since = anchor or moment
    return {
        "status": status,
        "since": since,
        "duration_seconds": max(0, int((moment - since).total_seconds())),
    }


def apply_open_alerts(posture: dict, alerts: list[dict] | None, now: datetime) -> dict:
    """An open unreachable or missed-backup alert cannot leave the host looking healthy."""
    moment = _as_utc(now) or datetime.now(timezone.utc)
    types = {str(row.get("type") or "") for row in (alerts or [])}
    status = posture["status"]
    if "host_unreachable" in types:
        status = "unreachable"
    elif status == "healthy" and (
        "backup_missed_repeat" in types or "backup_schedule_missed" in types
    ):
        status = "degraded"
    if status == posture["status"]:
        return posture
    return {
        "status": status,
        "since": posture["since"],
        "duration_seconds": posture["duration_seconds"],
        "evaluated_at": moment.isoformat(),
    }


def sync_host_posture_alerts(session: Session, now: Optional[datetime] = None) -> int:
    """Open or resolve the critical host alerts. Returns how many are still warranted."""
    from sqlmodel import select

    from ..models import Server
    from .app_settings import get_app_timezone
    from .notifications import resolve_by_fingerprint, upsert_notification
    from .schedule_watch import _jobs_by_server

    moment = now or datetime.now(timezone.utc)
    try:
        tz_name = get_app_timezone() or "UTC"
    except Exception:
        tz_name = "UTC"
    grouped = _jobs_by_server(session, moment)
    open_count = 0
    for server in session.exec(select(Server)).all():
        if not server.id:
            continue
        server_id = int(server.id)
        jobs = grouped.get((server_id, "backup"), [])
        streak, missed_at = missed_backup_streak(server, jobs, moment, tz_name)
        posture = host_posture(
            server, moment, missed_backups=streak, missed_at=missed_at
        )
        name = _server_name(server)
        unreachable_fp = f"{HOST_UNREACHABLE}:{server_id}"
        missed_fp = f"{BACKUP_MISSED_REPEAT}:{server_id}"
        if posture["status"] == "unreachable":
            hours = max(1, posture["duration_seconds"] // 3600)
            upsert_notification(
                session,
                fingerprint=unreachable_fp,
                type=HOST_UNREACHABLE,
                title=f"Host unreachable: {name}",
                body=f"{name} has not been seen for {hours} hours.",
                link_url=f"/servers/{server_id}",
                severity="critical",
                server_id=server_id,
            )
            open_count += 1
        else:
            resolve_by_fingerprint(session, unreachable_fp)
        if streak >= 2:
            upsert_notification(
                session,
                fingerprint=missed_fp,
                type=BACKUP_MISSED_REPEAT,
                title=f"Missed backups: {name}",
                body=f"{name} missed {streak} backup slots.",
                link_url=f"/servers/{server_id}/backups",
                severity="critical",
                server_id=server_id,
            )
            open_count += 1
        else:
            resolve_by_fingerprint(session, missed_fp)
    return open_count

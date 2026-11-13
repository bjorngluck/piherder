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
UNREACHABLE_AFTER_HOURS = 24
UNREACHABLE_AFTER_MAX_HOURS = 24 * 90
DEGRADED_AFTER = timedelta(minutes=15)
HOST_UNREACHABLE = "host_unreachable"
BACKUP_MISSED_REPEAT = "backup_missed_repeat"
# A successful answer. The date the host was added is not one of these.
_CONTACT_FIELDS = (
    "last_seen",
    "host_facts_at",
    "last_backup_at",
    "last_os_check_at",
    "last_container_check_at",
    "docker_inventory_at",
    "host_deps_checked_at",
)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def last_contact_at(server) -> Optional[datetime]:
    """Latest time this host answered. Empty when PiHerder has no answer yet."""
    stamps = []
    for name in _CONTACT_FIELDS:
        stamp = _as_utc(getattr(server, name, None))
        if stamp is not None:
            stamps.append(stamp)
    return max(stamps) if stamps else None


def unreachable_after_hours() -> int:
    """Quiet time before the alert. Settings stores hours. Default is 24."""
    try:
        from .alert_policy import raw_policy

        policy = raw_policy()
        types = policy.get("types") if isinstance(policy.get("types"), dict) else {}
        row = types.get(HOST_UNREACHABLE) if isinstance(types.get(HOST_UNREACHABLE), dict) else {}
        hours = int(row.get("quiet_hours"))
    except (TypeError, ValueError):
        hours = UNREACHABLE_AFTER_HOURS
    except Exception:
        hours = UNREACHABLE_AFTER_HOURS
    return max(1, min(int(hours), UNREACHABLE_AFTER_MAX_HOURS))


def quiet_hours_from_amount(amount, unit: str) -> int:
    """Hours from the Settings number and unit. Days are 24 hours each."""
    try:
        number = int(str(amount).strip())
    except (TypeError, ValueError):
        number = 1 if str(unit).strip().lower() == "days" else UNREACHABLE_AFTER_HOURS
    if str(unit).strip().lower() == "days":
        number *= 24
    return max(1, min(int(number), UNREACHABLE_AFTER_MAX_HOURS))


def host_unreachable_form() -> dict:
    """Settings card: on/off, the number, and hours or days."""
    from .alert_policy import effective, raw_policy

    hours = unreachable_after_hours()
    try:
        enabled = bool(effective(HOST_UNREACHABLE).enabled)
    except Exception:
        enabled = True
    unit = "hours"
    try:
        policy = raw_policy()
        types = policy.get("types") if isinstance(policy.get("types"), dict) else {}
        row = types.get(HOST_UNREACHABLE) if isinstance(types.get(HOST_UNREACHABLE), dict) else {}
        stored = str(row.get("quiet_unit") or "").strip().lower()
        if stored in ("hours", "days"):
            unit = stored
    except Exception:
        unit = "hours"
    amount = hours // 24 if unit == "days" else hours
    if amount < 1:
        amount = 1
    return {"enabled": enabled, "amount": amount, "unit": unit, "hours": hours}


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
    unreachable_after: Optional[timedelta] = None,
) -> dict:
    """``status`` is healthy, degraded, or unreachable. ``duration_seconds`` is time in that status.

    Quiet time starts at the latest answer. A host with no answer yet is not
    unreachable. The date the host was added is not an answer.
    """
    moment = _as_utc(now) or datetime.now(timezone.utc)
    anchor = last_contact_at(server)
    quiet = (moment - anchor) if anchor is not None else timedelta(0)
    missed_at = _as_utc(missed_at)
    limit = unreachable_after if unreachable_after is not None else timedelta(hours=unreachable_after_hours())
    if anchor is not None and quiet >= limit:
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
            row = upsert_notification(
                session,
                fingerprint=unreachable_fp,
                type=HOST_UNREACHABLE,
                title=f"Host unreachable: {name}",
                body=f"{name} has not been seen for {hours} hours.",
                link_url=f"/servers/{server_id}",
                severity="critical",
                server_id=server_id,
            )
            if row is not None and getattr(row, "status", None) == "open":
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

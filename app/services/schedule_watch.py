"""Warn when a scheduled host backup or container check does not start.

The watch looks at the latest cron slot that is already past a short grace
window. No start in that window opens one warning. A later successful run of
the same kind resolves it. Turning the schedule off resolves it too.

The Prometheus gauge uses the same rule: 1 while that slot was missed and no
success has finished since, otherwise 0 for each schedule that is still on.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import or_
from sqlmodel import Session, select

from ..models import Job, Server

logger = logging.getLogger(__name__)

MISSED_RUN_GRACE_MINUTES = 15
MISSED_RUN_LOOKBACK_DAYS = 35
MISSED_RUN_WATCH_MINUTES = 5
MISSED_SCHEDULE_WATCH_JOB_ID = "schedule_missed_watch"

# kind → job type, alert type, and the page the inbox row opens.
_KINDS = {
    "backup": {
        "job_type": "backup",
        "type_id": "backup_schedule_missed",
        "title": "Scheduled backup did not start",
        "noun": "backup",
        "link": "/servers/{server_id}/backups",
    },
    "container_check": {
        "job_type": "container_update_check",
        "type_id": "container_check_missed",
        "title": "Scheduled container check did not start",
        "noun": "container check",
        "link": "/servers/{server_id}/docker",
    },
}


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def previous_fire(cron: str, tz_name: str, moment: datetime) -> Optional[datetime]:
    """Latest cron fire at or before ``moment``. None when the expression is unusable."""
    moment = _as_utc(moment)
    if moment is None:
        return None
    text = (cron or "").strip()
    if not text:
        return None
    try:
        from .scheduler import _cron_trigger

        trigger = _cron_trigger(text, timezone=(tz_name or "UTC"))
    except Exception:
        return None
    try:
        exact = trigger.get_next_fire_time(None, moment)
    except Exception:
        return None
    if exact == moment:
        return exact

    lookback = moment - timedelta(days=MISSED_RUN_LOOKBACK_DAYS)
    try:
        first = trigger.get_next_fire_time(None, lookback)
    except Exception:
        return None
    if first is None or first > moment:
        return None

    left = first
    right = moment
    for _ in range(48):
        if right - left <= timedelta(seconds=1):
            break
        mid = left + (right - left) / 2
        try:
            fire = trigger.get_next_fire_time(None, mid)
        except Exception:
            return left
        if fire is not None and fire <= moment and fire > left:
            left = fire
            if fire == moment:
                break
        else:
            right = mid
    return left if left <= moment else None


def due_slot(
    cron: str,
    tz_name: str,
    now: datetime,
    not_before: Optional[datetime],
    *,
    grace_minutes: int = MISSED_RUN_GRACE_MINUTES,
) -> Optional[datetime]:
    """Latest fire whose grace window has elapsed, and that is not before the host existed."""
    now_utc = _as_utc(now)
    if now_utc is None:
        return None
    slot = previous_fire(cron, tz_name, now_utc)
    if slot is None:
        return None
    grace = timedelta(minutes=grace_minutes)
    if slot + grace > now_utc:
        slot = previous_fire(cron, tz_name, slot - timedelta(microseconds=1))
    if slot is None or slot + grace > now_utc:
        return None
    floor = _as_utc(not_before)
    if floor is not None and slot < floor:
        return None
    return slot


def classify_slot(
    slot: datetime,
    jobs: Iterable,
    *,
    grace_minutes: int = MISSED_RUN_GRACE_MINUTES,
) -> str:
    """``succeeded``, ``on_time``, or ``missed`` for one due slot.

    A success at or after the slot wins, including a start that was late.
    A start inside the grace window is on time even when that run has not
    finished. Anything else is missed.
    """
    slot_utc = _as_utc(slot)
    if slot_utc is None:
        return "missed"
    grace_end = slot_utc + timedelta(minutes=grace_minutes)
    on_time = False
    for job in jobs:
        started = _as_utc(getattr(job, "started_at", None))
        finished = _as_utc(getattr(job, "finished_at", None))
        status = (getattr(job, "status", "") or "").strip()
        if started is not None and slot_utc <= started <= grace_end:
            on_time = True
        success_at = finished or started
        if status == "success" and success_at is not None and success_at >= slot_utc:
            return "succeeded"
    if on_time:
        return "on_time"
    return "missed"


def backup_cron_for(server) -> Optional[str]:
    """Cron that will run, or None when backups are off."""
    if not server or not getattr(server, "backup_enabled", False):
        return None
    own = (getattr(server, "backup_schedule", None) or "").strip()
    if own:
        return own
    server_id = int(getattr(server, "id", 0) or 0)
    if not server_id:
        return None
    try:
        from .update_check_config import effective_fleet_cron

        cron = (effective_fleet_cron(server_id) or "").strip()
    except Exception:
        logger.warning("[SCHEDULE] Fleet backup cron unavailable for server %s", server_id)
        return None
    return cron or None


def container_cron_for(server) -> Optional[str]:
    """Cron for a container check that should be registered, else None."""
    if not server:
        return None
    try:
        from .scheduler import container_check_should_run

        if not container_check_should_run(server):
            return None
    except Exception:
        logger.warning("[SCHEDULE] Container check predicate unavailable")
        return None
    return (getattr(server, "container_check_schedule", None) or "").strip() or None


def _cron_for(kind: str, server) -> Optional[str]:
    if kind == "backup":
        return backup_cron_for(server)
    if kind == "container_check":
        return container_cron_for(server)
    return None


def _fingerprint(kind: str, server_id: int) -> str:
    type_id = _KINDS[kind]["type_id"]
    return f"{type_id}:{int(server_id)}"


def resolve_backup_schedule_missed(session: Session, server_id: int) -> None:
    from .notifications import resolve_by_fingerprint

    resolve_by_fingerprint(session, _fingerprint("backup", server_id))


def resolve_container_check_missed(session: Session, server_id: int) -> None:
    from .notifications import resolve_by_fingerprint

    resolve_by_fingerprint(session, _fingerprint("container_check", server_id))


def _resolve_kind(session: Session, kind: str, server_id: int) -> None:
    if kind == "backup":
        resolve_backup_schedule_missed(session, server_id)
    elif kind == "container_check":
        resolve_container_check_missed(session, server_id)


def _jobs_by_server(session: Session, now: datetime) -> dict[tuple[int, str], list]:
    floor = (_as_utc(now) or datetime.now(timezone.utc)) - timedelta(days=MISSED_RUN_LOOKBACK_DAYS)
    floor_naive = floor.replace(tzinfo=None)
    job_types = [spec["job_type"] for spec in _KINDS.values()]
    rows = list(
        session.exec(
            select(Job).where(
                Job.job_type.in_(job_types),
                or_(
                    Job.started_at >= floor_naive,
                    Job.finished_at >= floor_naive,
                    Job.created_at >= floor_naive,
                ),
            )
        ).all()
    )
    type_to_kind = {spec["job_type"]: kind for kind, spec in _KINDS.items()}
    grouped: dict[tuple[int, str], list] = {}
    for row in rows:
        if row.server_id is None:
            continue
        kind = type_to_kind.get(row.job_type or "")
        if not kind:
            continue
        grouped.setdefault((int(row.server_id), kind), []).append(row)
    return grouped


def _server_name(server) -> str:
    return (
        (getattr(server, "name", None) or "").strip()
        or (getattr(server, "hostname", None) or "").strip()
        or f"#{getattr(server, 'id', '')}"
    )


def _open_missed(
    session: Session,
    *,
    kind: str,
    server,
    slot: datetime,
) -> None:
    from .app_settings import format_datetime_in_app_tz
    from .notifications import upsert_notification

    spec = _KINDS[kind]
    server_id = int(server.id)
    name = _server_name(server)
    when = format_datetime_in_app_tz(slot)
    upsert_notification(
        session,
        fingerprint=_fingerprint(kind, server_id),
        type=spec["type_id"],
        title=f"{spec['title']}: {name}",
        body=(
            f"The {when} slot passed and no {spec['noun']} started within "
            f"{MISSED_RUN_GRACE_MINUTES} minutes."
        ),
        link_url=spec["link"].format(server_id=server_id),
        severity="warning",
        server_id=server_id,
    )


def sync_missed_schedule_alerts(session: Session, now: Optional[datetime] = None) -> int:
    """Open, refresh, or resolve missed-schedule warnings. Returns how many are open."""
    from .app_settings import get_app_timezone

    moment = now or datetime.now(timezone.utc)
    try:
        tz_name = get_app_timezone() or "UTC"
    except Exception:
        tz_name = "UTC"
    grouped = _jobs_by_server(session, moment)
    opened = 0
    servers = list(session.exec(select(Server)).all())
    for server in servers:
        if not server.id:
            continue
        jobs_for_kind = {
            kind: grouped.get((int(server.id), kind), [])
            for kind in _KINDS
        }
        for kind in _KINDS:
            cron = _cron_for(kind, server)
            if not cron:
                _resolve_kind(session, kind, int(server.id))
                continue
            slot = due_slot(cron, tz_name, moment, getattr(server, "created_at", None))
            if slot is None:
                continue
            status = classify_slot(slot, jobs_for_kind.get(kind) or [])
            if status == "missed":
                _open_missed(session, kind=kind, server=server, slot=slot)
                opened += 1
            elif status == "succeeded":
                _resolve_kind(session, kind, int(server.id))
    return opened


def _gauge_line(kind: str, server_id: int, value: int) -> str:
    return (
        f'piherder_schedule_missed{{kind="{kind}",server_id="{int(server_id)}"}} {int(value)}'
    )


def missed_schedule_lines(session: Session, now: Optional[datetime] = None) -> list[str]:
    """Gauge lines for schedules that are on. 1 means the latest due slot was missed."""
    from .app_settings import get_app_timezone

    moment = now or datetime.now(timezone.utc)
    try:
        tz_name = get_app_timezone() or "UTC"
    except Exception:
        tz_name = "UTC"
    grouped = _jobs_by_server(session, moment)
    lines: list[str] = []
    servers = list(session.exec(select(Server)).all())
    for server in servers:
        if not server.id:
            continue
        for kind in _KINDS:
            cron = _cron_for(kind, server)
            if not cron:
                continue
            slot = due_slot(
                cron,
                tz_name,
                moment,
                getattr(server, "created_at", None),
            )
            value = 0
            if slot is not None:
                status = classify_slot(
                    slot, grouped.get((int(server.id), kind), [])
                )
                value = 1 if status == "missed" else 0
            lines.append(_gauge_line(kind, int(server.id), value))
    return lines

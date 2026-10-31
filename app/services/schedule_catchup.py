"""Opt-in catch-up for one missed backup or image check.

Off unless the host schedule says so. OS patch, redeploy, and Docker prune
are never caught up. One job per missed slot. A newer success, an active run,
or a catch-up that already exists for that slot is skipped.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from ..models import Job, Server
from .audit_write import make_audit_log
from .schedule_watch import (
    _as_utc,
    _cron_for,
    _jobs_by_server,
    classify_slot,
    due_slot,
)

logger = logging.getLogger(__name__)

CATCHUP_KINDS = ("backup", "container_check")
_JOB_TYPE = {
    "backup": "backup",
    "container_check": "container_update_check",
}
_REASONS = ("scheduler_down", "overlap", "clock", "error", "unknown")
_RESULTS = ("queued", "skipped", "failed")
_SILENT = frozenset({"pending", "running", "failed"})
_COUNTS = {
    kind: {result: 0 for result in _RESULTS}
    for kind in CATCHUP_KINDS
}


def catchup_enabled(server, kind: str) -> bool:
    if kind == "backup":
        return bool(getattr(server, "backup_catchup_enabled", False))
    if kind == "container_check":
        return bool(getattr(server, "container_check_catchup_enabled", False))
    return False


def note_catchup(kind: str, result: str) -> None:
    if kind not in _COUNTS or result not in _RESULTS:
        return
    _COUNTS[kind][result] += 1


def catchup_counter_lines() -> list[str]:
    lines = []
    for kind in CATCHUP_KINDS:
        for result in _RESULTS:
            lines.append(
                f'piherder_schedule_catchup_total{{kind="{kind}",result="{result}"}} '
                f'{int(_COUNTS[kind][result])}'
            )
    return lines


def reset_catchup_counters() -> None:
    """Test helper. The live counter is process-local."""
    for kind in CATCHUP_KINDS:
        for result in _RESULTS:
            _COUNTS[kind][result] = 0


def slot_key(slot: datetime) -> str:
    moment = _as_utc(slot)
    if moment is None:
        return ""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _details(job) -> dict:
    try:
        raw = json.loads(getattr(job, "details", None) or "{}")
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _short_cadence(cron: Optional[str]) -> bool:
    """Daily and finer crons. A weekly or monthly slot is older than 26h on purpose."""
    parts = (cron or "").split()
    if len(parts) < 5:
        return True
    dom, dow = parts[2], parts[4]
    if dom not in ("*", "?") or dow not in ("*", "?"):
        return False
    return True


def miss_reason(
    slot: datetime,
    jobs,
    *,
    worker_up: Optional[bool],
    now: datetime,
    cron: Optional[str] = None,
) -> str:
    """Why the slot was missed. One of the five recorded reasons."""
    slot_utc = _as_utc(slot)
    now_utc = _as_utc(now) or datetime.now(timezone.utc)
    if slot_utc is None:
        return "unknown"
    if _short_cadence(cron) and now_utc - slot_utc > timedelta(hours=26):
        return "clock"
    saw_failed = False
    for job in jobs:
        created = _as_utc(getattr(job, "created_at", None))
        started = _as_utc(getattr(job, "started_at", None))
        finished = _as_utc(getattr(job, "finished_at", None))
        status = (getattr(job, "status", "") or "").strip()
        if created is not None and created > now_utc + timedelta(minutes=5):
            return "clock"
        if started is not None and started < slot_utc and (finished is None or finished > slot_utc):
            return "overlap"
        if status in ("pending", "running") and created is not None and created <= slot_utc:
            return "overlap"
        if status == "failed" and created is not None and created >= slot_utc:
            saw_failed = True
    if saw_failed:
        return "error"
    if worker_up is False or worker_up is None:
        return "scheduler_down"
    return "unknown"


def _slot_catchups(session: Session, server_id: int, kind: str, key: str, slot: datetime) -> list:
    """Catch-up rows for this slot, limited to jobs created around the slot."""
    job_type = _JOB_TYPE[kind]
    moment = _as_utc(slot) or datetime.now(timezone.utc)
    cutoff = (moment - timedelta(hours=2)).astimezone(timezone.utc).replace(tzinfo=None)
    rows = list(
        session.exec(
            select(Job).where(
                Job.server_id == int(server_id),
                Job.job_type == job_type,
                Job.created_at >= cutoff,
            )
        ).all()
    )
    return [row for row in rows if _details(row).get("catchup_slot") == key]


def _silent_prior(rows: list) -> bool:
    """A catch-up already pending, running, or failed for this slot. Do not record it again."""
    if not rows:
        return False
    statuses = {(getattr(row, "status", "") or "").strip() for row in rows}
    if "success" in statuses:
        return False
    return bool(statuses & _SILENT)


def _stamp(session: Session, job: Job, *, kind: str, key: str, reason: str) -> None:
    raw = _details(job)
    raw["catchup"] = True
    raw["catchup_slot"] = key
    raw["catchup_reason"] = reason if reason in _REASONS else "unknown"
    raw["catchup_kind"] = kind
    job.details = json.dumps(raw)
    session.add(job)
    session.commit()


def _audit(session: Session, server_id: int, kind: str, result: str, reason: str, key: str) -> None:
    status = "success" if result == "queued" else ("failed" if result == "failed" else "skipped")
    session.add(
        make_audit_log(
            action="schedule_catchup",
            status=status,
            server_id=int(server_id),
            details=json.dumps(
                {
                    "kind": kind,
                    "result": result,
                    "reason": reason,
                    "slot": key,
                }
            ),
        )
    )
    session.commit()


def _enqueue(session: Session, server: Server, kind: str):
    from . import jobs as jobs_mod

    if kind == "backup":
        return jobs_mod.enqueue_backup_for_server(session, server, scheduled=True)
    if kind == "container_check":
        return jobs_mod.enqueue_container_update_check(int(server.id))
    raise ValueError(kind)


def catch_up_one(
    session: Session,
    server: Server,
    kind: str,
    slot: datetime,
    jobs,
    *,
    worker_up: Optional[bool],
    now: datetime,
    cron: Optional[str] = None,
) -> str:
    """Enqueue at most one catch-up. Returns queued, skipped, or failed."""
    if kind not in CATCHUP_KINDS or not catchup_enabled(server, kind):
        return "skipped"
    key = slot_key(slot)
    reason = miss_reason(slot, jobs, worker_up=worker_up, now=now, cron=cron)
    server_id = int(server.id)
    if classify_slot(slot, jobs) == "succeeded":
        note_catchup(kind, "skipped")
        _audit(session, server_id, kind, "skipped", reason, key)
        return "skipped"
    prior = _slot_catchups(session, server_id, kind, key, slot)
    if _silent_prior(prior):
        return "skipped"
    if prior:
        note_catchup(kind, "skipped")
        _audit(session, server_id, kind, "skipped", reason, key)
        return "skipped"
    from . import jobs as jobs_mod

    active = jobs_mod._active_job_of_type(session, server_id, _JOB_TYPE[kind])
    if active is not None:
        note_catchup(kind, "skipped")
        _audit(session, server_id, kind, "skipped", "overlap", key)
        return "skipped"
    try:
        job = _enqueue(session, server, kind)
    except jobs_mod.JobAlreadyActive:
        logger.info("[SCHEDULE] Catch-up skipped, job already active kind=%s server=%s", kind, server_id)
        return "skipped"
    except Exception:
        logger.exception("[SCHEDULE] Catch-up enqueue failed kind=%s server=%s", kind, server_id)
        note_catchup(kind, "failed")
        _audit(session, server_id, kind, "failed", reason, key)
        return "failed"
    if job is None:
        note_catchup(kind, "failed")
        _audit(session, server_id, kind, "failed", reason, key)
        return "failed"
    bound = session.get(Job, job.id) or job
    if _details(bound).get("catchup_slot") == key:
        note_catchup(kind, "skipped")
        _audit(session, server_id, kind, "skipped", reason, key)
        return "skipped"
    _stamp(session, bound, kind=kind, key=key, reason=reason)
    note_catchup(kind, "queued")
    _audit(session, server_id, kind, "queued", reason, key)
    return "queued"


def run_due_catchups(session: Session, now: Optional[datetime] = None) -> int:
    """Catch up opted-in hosts whose latest due slot was missed. Returns how many queued."""
    from . import jobs as jobs_mod
    from .app_settings import get_app_timezone

    moment = now or datetime.now(timezone.utc)
    try:
        tz_name = get_app_timezone() or "UTC"
    except Exception:
        tz_name = "UTC"
    try:
        activity = jobs_mod.celery_worker_activity()
    except Exception:
        activity = None
    worker_up = None if activity is None else bool(activity[0])
    grouped = _jobs_by_server(session, moment)
    ran = 0
    servers = list(session.exec(select(Server)).all())
    for server in servers:
        if not server.id:
            continue
        for kind in CATCHUP_KINDS:
            if not catchup_enabled(server, kind):
                continue
            cron = _cron_for(kind, server)
            if not cron:
                continue
            slot = due_slot(cron, tz_name, moment, getattr(server, "created_at", None))
            if slot is None:
                continue
            jobs = grouped.get((int(server.id), kind), [])
            if classify_slot(slot, jobs) != "missed":
                continue
            result = catch_up_one(
                session,
                server,
                kind,
                slot,
                jobs,
                worker_up=worker_up,
                now=moment,
                cron=cron,
            )
            if result == "queued":
                ran += 1
    return ran

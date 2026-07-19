"""Nmap scan schedule CRUD + APScheduler sync."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from ...models import Integration, NmapScanSchedule
from .config import parse_nmap_config
from .scan import enqueue_nmap_scan

logger = logging.getLogger(__name__)

INTENSITIES_SCHEDULE = ("discovery", "inventory", "detailed")


def schedule_aps_id(schedule_id: int) -> str:
    return f"nmap_scan_{int(schedule_id)}"


def create_schedule(
    session: Session,
    *,
    integration_id: int,
    name: str,
    intensity: str = "discovery",
    cron: str | None = None,
    interval_hours: int | None = None,
    enabled: bool = False,
    scope_all: bool = True,
    cidrs: list[str] | None = None,
) -> NmapScanSchedule:
    intensity = (intensity or "discovery").strip().lower()
    if intensity not in INTENSITIES_SCHEDULE:
        raise ValueError(f"schedule intensity must be one of {INTENSITIES_SCHEDULE}")
    if not cron and not interval_hours:
        raise ValueError("provide cron (5 fields) or interval_hours")
    if cron:
        parts = cron.strip().split()
        if len(parts) != 5:
            raise ValueError("cron must have 5 fields")
    scope: dict[str, Any] = {"all_configured": True} if scope_all else {"cidrs": cidrs or []}
    now = datetime.utcnow()
    row = NmapScanSchedule(
        integration_id=integration_id,
        name=(name or intensity).strip() or intensity,
        intensity=intensity,
        cron=(cron or "").strip() or None,
        interval_hours=int(interval_hours) if interval_hours else None,
        enabled=bool(enabled),
        scope_json=json.dumps(scope, separators=(",", ":")),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_schedule(
    session: Session,
    row: NmapScanSchedule,
    *,
    name: str | None = None,
    intensity: str | None = None,
    cron: str | None = None,
    interval_hours: int | None = None,
    enabled: bool | None = None,
    clear_cron: bool = False,
    clear_interval: bool = False,
) -> NmapScanSchedule:
    if name is not None:
        row.name = name.strip() or row.name
    if intensity is not None:
        intensity = intensity.strip().lower()
        if intensity not in INTENSITIES_SCHEDULE:
            raise ValueError(f"invalid intensity {intensity}")
        row.intensity = intensity
    if clear_cron:
        row.cron = None
    elif cron is not None:
        c = cron.strip()
        if c:
            if len(c.split()) != 5:
                raise ValueError("cron must have 5 fields")
            row.cron = c
        else:
            row.cron = None
    if clear_interval:
        row.interval_hours = None
    elif interval_hours is not None:
        row.interval_hours = int(interval_hours) if interval_hours else None
    if enabled is not None:
        row.enabled = bool(enabled)
    if not row.cron and not row.interval_hours:
        raise ValueError("schedule needs cron or interval_hours")
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def delete_schedule(session: Session, row: NmapScanSchedule) -> None:
    session.delete(row)
    session.commit()


def fire_schedule(schedule_id: int) -> None:
    """APScheduler callback — enqueue scan for one schedule."""
    try:
        from ...database import engine

        with Session(engine) as db:
            row = db.get(NmapScanSchedule, schedule_id)
            if not row or not row.enabled:
                return
            integ = db.get(Integration, row.integration_id)
            if not integ or not integ.enabled or integ.type != "nmap":
                return
            cfg = parse_nmap_config(integ)
            targets: list[str] = []
            if row.scope_json:
                try:
                    scope = json.loads(row.scope_json)
                    if scope.get("all_configured"):
                        targets = list(cfg.get("cidrs") or [])
                    else:
                        targets = list(scope.get("cidrs") or [])
                except Exception:
                    targets = list(cfg.get("cidrs") or [])
            else:
                targets = list(cfg.get("cidrs") or [])
            if not targets:
                logger.info("[SCHEDULER] nmap schedule %s skipped — no targets", schedule_id)
                return
            job, run = enqueue_nmap_scan(
                db,
                integration_id=integ.id,
                intensity=row.intensity,
                targets=targets,
                schedule_id=row.id,
                user_id=None,
                vuln_scripts=False,
            )
            row.last_run_at = datetime.utcnow()
            row.last_job_id = job.id
            row.updated_at = datetime.utcnow()
            db.add(row)
            db.commit()
            logger.info(
                "[SCHEDULER] nmap schedule %s enqueued job #%s run #%s",
                schedule_id,
                job.id,
                run.id,
            )
    except Exception as e:
        logger.warning("[SCHEDULER] nmap schedule %s failed: %s", schedule_id, e)


def sync_nmap_schedules(scheduler, has_scheduler: bool) -> int:
    """Register/remove APScheduler jobs for all enabled nmap schedules."""
    if not has_scheduler or not scheduler:
        return 0
    from ...database import engine

    # remove all existing nmap_scan_* jobs first
    try:
        for job in list(scheduler.get_jobs()):
            jid = str(getattr(job, "id", "") or "")
            if jid.startswith("nmap_scan_"):
                try:
                    scheduler.remove_job(jid)
                except Exception:
                    pass
    except Exception as e:
        logger.debug("nmap schedule cleanup: %s", e)

    count = 0
    with Session(engine) as db:
        rows = list(
            db.exec(
                select(NmapScanSchedule).where(NmapScanSchedule.enabled == True)  # noqa: E712
            ).all()
        )
        for row in rows:
            jid = schedule_aps_id(row.id)
            try:
                if row.cron:
                    from ._cron import cron_trigger

                    trigger = cron_trigger(row.cron)
                    scheduler.add_job(
                        func=fire_schedule,
                        trigger=trigger,
                        id=jid,
                        args=[row.id],
                        replace_existing=True,
                        name=f"Nmap: {row.name}",
                    )
                elif row.interval_hours and row.interval_hours > 0:
                    from apscheduler.triggers.interval import IntervalTrigger

                    scheduler.add_job(
                        func=fire_schedule,
                        trigger=IntervalTrigger(hours=int(row.interval_hours)),
                        id=jid,
                        args=[row.id],
                        replace_existing=True,
                        name=f"Nmap: {row.name}",
                    )
                else:
                    continue
                count += 1
            except Exception as e:
                logger.warning("nmap schedule register %s failed: %s", row.id, e)
    logger.info("[SCHEDULER] nmap schedules registered: %s", count)
    return count

"""About-page view of APScheduler jobs.

Last fire is recorded in this process when a job runs or raises. A restart
clears it. Next fire is the scheduler's next_run_time. A missed slot is not
a fire.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

NONE_YET = "None yet"
NOT_SCHEDULED = "Not scheduled"

_lock = threading.Lock()
_last_fires: dict[str, datetime] = {}
_attached_ids: set[int] = set()


def clear_last_fires() -> None:
    """Test helper. Drops recorded fires. Does not detach listeners."""
    with _lock:
        _last_fires.clear()


def note_scheduler_event(event: Any) -> None:
    """Remember a real run. Missed slots stay out."""
    try:
        from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
    except ImportError:
        return
    code = getattr(event, "code", None)
    if code not in (EVENT_JOB_EXECUTED, EVENT_JOB_ERROR):
        return
    job_id = str(getattr(event, "job_id", "") or "").strip()
    if not job_id:
        return
    when = getattr(event, "scheduled_run_time", None)
    if not isinstance(when, datetime):
        when = datetime.now(timezone.utc)
    with _lock:
        _last_fires[job_id] = when


def attach_fire_listener(scheduler) -> None:
    """Listen once per scheduler instance. Safe to call on every startup."""
    if scheduler is None or not hasattr(scheduler, "add_listener"):
        return
    key = id(scheduler)
    with _lock:
        if key in _attached_ids:
            return
        _attached_ids.add(key)
    try:
        from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

        scheduler.add_listener(
            note_scheduler_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR
        )
    except Exception:
        with _lock:
            _attached_ids.discard(key)
        logger.exception("Could not attach the scheduler fire listener")


def _display(when: datetime | None, *, empty: str) -> str:
    if when is None:
        return empty
    try:
        from .app_settings import format_datetime_in_app_tz

        text = format_datetime_in_app_tz(when)
    except Exception:
        logger.debug("Could not format a scheduler time", exc_info=True)
        return empty
    if not text or text == "Never":
        return empty
    return text


def _sort_key(job: dict[str, Any]) -> tuple:
    nxt = job.get("next_at")
    name = str(job.get("name") or "").lower()
    if not isinstance(nxt, datetime):
        return (1, 0.0, name)
    try:
        stamp = nxt.timestamp()
    except (OSError, OverflowError, ValueError):
        stamp = 0.0
    return (0, stamp, name)


def scheduler_snapshot(scheduler, has_scheduler: bool) -> dict[str, Any]:
    """Rows for About. Empty jobs when the scheduler is down."""
    if not has_scheduler or scheduler is None:
        return {
            "available": False,
            "running": False,
            "unreadable": False,
            "jobs": [],
        }
    running = bool(getattr(scheduler, "running", False))
    if not running:
        return {
            "available": True,
            "running": False,
            "unreadable": False,
            "jobs": [],
        }
    try:
        raw = list(scheduler.get_jobs())
    except Exception:
        logger.exception("Could not list scheduler jobs")
        return {
            "available": True,
            "running": True,
            "unreadable": True,
            "jobs": [],
        }
    with _lock:
        fired = dict(_last_fires)
    rows: list[dict[str, Any]] = []
    for job in raw:
        job_id = str(getattr(job, "id", "") or "").strip()
        name = str(getattr(job, "name", "") or "").strip() or job_id or "Scheduled job"
        nxt = getattr(job, "next_run_time", None)
        if not isinstance(nxt, datetime):
            nxt = None
        last = fired.get(job_id)
        rows.append(
            {
                "id": job_id,
                "name": name,
                "last_display": _display(last, empty=NONE_YET),
                "next_display": _display(nxt, empty=NOT_SCHEDULED),
                "next_at": nxt,
            }
        )
    rows.sort(key=_sort_key)
    for row in rows:
        row.pop("next_at", None)
    return {
        "available": True,
        "running": True,
        "unreadable": False,
        "jobs": rows,
    }

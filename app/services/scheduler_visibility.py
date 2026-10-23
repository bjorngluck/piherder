"""About-page view of APScheduler jobs.

Last run is the latest matching job row (or LAN discovery run). A restart
does not clear it. Next fire is the scheduler's next_run_time. A missed slot
is not a run.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

NONE_YET = "Never"
NOT_SCHEDULED = "Not scheduled"
_RUN_STATUSES = ("success", "failed", "cancelled", "running")
_HOST_JOB_TYPES = (
    ("backup_", "backup"),
    ("os_check_", "os_update_check"),
    ("container_check_", "container_update_check"),
    ("os_apply_", "os_patch"),
    ("container_apply_", "container_patch"),
)
# One scheduler row covers every host. The last run is the newest of that type.
_FLEET_JOB_TYPES = {
    "herder_self_backup": "herder_backup",
    "stale_data_cleanup": "stale_data_cleanup",
    "host_facts_fleet": "host_facts",
    "template_drift_check": "template_drift_check",
}
_PILL = {
    "success": ("success", "OK", "status-success"),
    "failed": ("failed", "Failed", "status-failed"),
    "cancelled": ("cancelled", "Cancelled", "status-cancelled"),
    "running": ("running", "Running", "status-running"),
}
_PILL_NONE = ("none", "None", "scheduler-status-none")

# Longer id prefixes first. backup_copy_ must win over backup_.
_KINDS = (
    ("backup_copy_", "backup_copy", "Backup copy", "destination", "Backup copy "),
    ("backup_", "backup", "Backup", "host", "Backup "),
    ("os_check_", "os_check", "OS check", "host", "OS check "),
    ("container_check_", "container_check", "Container check", "host", "Container check "),
    ("os_apply_", "os_apply", "OS apply", "host", "OS apply "),
    ("container_apply_", "container_apply", "Container apply", "host", "Container apply "),
    ("nmap_scan_", "nmap", "LAN discovery", "schedule", "Nmap: "),
)
_GROUP_ORDER = (
    "backup",
    "os_check",
    "container_check",
    "os_apply",
    "container_apply",
    "backup_copy",
    "nmap",
    "instance",
)
_GROUP_META = {row[1]: (row[2], row[3]) for row in _KINDS}
_GROUP_META["instance"] = ("This instance", "instance")
_HEADING = {
    "host": "Host",
    "destination": "Destination",
    "schedule": "Schedule",
    "instance": "Job",
}

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
    """Listen once per scheduler instance. Safe to call on every startup.

    About does not read this. Last run comes from the job table.
    """
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


def _subject(name: str, prefix: str) -> str:
    if name.startswith(prefix):
        rest = name[len(prefix):].strip()
        if rest:
            return rest
    return name


def classify_scheduler_job(job_id: str, name: str) -> dict[str, str]:
    """Group a scheduler row by type. The subject is the host, or the job name."""
    jid = (job_id or "").strip()
    label = (name or "").strip() or jid or "Scheduled job"
    for prefix, key, _group_label, _kind, name_prefix in _KINDS:
        if jid.startswith(prefix):
            group_label, kind = _GROUP_META[key]
            return {
                "group_key": key,
                "group_label": group_label,
                "subject": _subject(label, name_prefix),
                "kind": kind,
            }
    group_label, kind = _GROUP_META["instance"]
    return {
        "group_key": "instance",
        "group_label": group_label,
        "subject": label,
        "kind": kind,
    }


def _pill(job_status: str | None) -> tuple[str, str, str]:
    return _PILL.get(str(job_status or ""), _PILL_NONE)


def _suffix_id(job_id: str, prefix: str) -> int | None:
    rest = job_id[len(prefix):]
    if rest.isdigit():
        return int(rest)
    return None


def _run_spec(job_id: str) -> tuple[str, str] | None:
    """How to find this scheduler id in the database.

    host:<job_type>:<server id>
    fleet:<job_type>
    copy:<destination id>
    nmap:<schedule id>
    """
    jid = (job_id or "").strip()
    if jid.startswith("backup_copy_"):
        dest = _suffix_id(jid, "backup_copy_")
        if dest is not None:
            return ("copy", str(dest))
        return None
    for prefix, job_type in _HOST_JOB_TYPES:
        if jid.startswith(prefix):
            server_id = _suffix_id(jid, prefix)
            if server_id is not None:
                return ("host", f"{job_type}:{server_id}")
            return None
    if jid.startswith("nmap_scan_"):
        schedule_id = _suffix_id(jid, "nmap_scan_")
        if schedule_id is not None:
            return ("nmap", str(schedule_id))
        return None
    fleet_type = _FLEET_JOB_TYPES.get(jid)
    if fleet_type:
        return ("fleet", fleet_type)
    return None


def _when(status: str | None, finished: datetime | None, started: datetime | None) -> datetime | None:
    if status == "running":
        return started or finished
    return finished or started


def _destination_id(details: str | None) -> int | None:
    if not details:
        return None
    try:
        data = json.loads(details)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return int(data.get("destination_id"))
    except (TypeError, ValueError):
        return None


def _latest_host_and_fleet(session: Any, types: set[str]) -> dict[tuple[str, int | None], Any]:
    """Newest started-or-finished job for each type and server."""
    from sqlalchemy import func
    from sqlmodel import col, select

    from ..models import Job

    if not types:
        return {}
    rank = func.row_number().over(
        partition_by=(Job.job_type, Job.server_id),
        order_by=Job.id.desc(),
    )
    ranked = (
        select(Job.id.label("jid"), rank.label("rn"))
        .where(col(Job.job_type).in_(types))
        .where(col(Job.status).in_(_RUN_STATUSES))
        .subquery()
    )
    ids: list[int] = []
    for item in session.exec(select(ranked.c.jid).where(ranked.c.rn == 1)).all():
        if isinstance(item, int):
            ids.append(item)
        else:
            ids.append(int(item[0]))
    if not ids:
        return {}
    found: dict[tuple[str, int | None], Any] = {}
    for job in session.exec(select(Job).where(col(Job.id).in_(ids))).all():
        found[(str(job.job_type), job.server_id)] = job
    return found


def _latest_copies(session: Any, destination_ids: set[int]) -> dict[int, Any]:
    from sqlmodel import col, select

    from ..models import Job

    if not destination_ids:
        return {}
    found: dict[int, tuple[str, datetime | None, datetime | None]] = {}
    rows = session.exec(
        select(Job.status, Job.finished_at, Job.started_at, Job.details)
        .where(col(Job.job_type) == "backup_replicate")
        .where(col(Job.status).in_(_RUN_STATUSES))
        .order_by(Job.id.desc())
    ).all()
    for row in rows:
        status, finished, started, details = row
        dest = _destination_id(details)
        if dest in destination_ids and dest not in found:
            found[dest] = (str(status), finished, started)
        if destination_ids <= found.keys():
            break
    return found


def _latest_nmap(session: Any, schedule_ids: set[int]) -> dict[int, Any]:
    from sqlmodel import col, select

    from ..models import NmapScanRun

    if not schedule_ids:
        return {}
    found: dict[int, Any] = {}
    rows = session.exec(
        select(NmapScanRun)
        .where(col(NmapScanRun.schedule_id).in_(schedule_ids))
        .where(col(NmapScanRun.status).in_(("success", "failed", "running")))
        .order_by(NmapScanRun.id.desc())
    ).all()
    for run in rows:
        sid = run.schedule_id
        if sid in schedule_ids and sid not in found:
            found[int(sid)] = run
        if len(found) == len(schedule_ids):
            break
    return found


def last_runs_for(job_ids: list[str]) -> dict[str, tuple[datetime | None, str]]:
    """Scheduler id to (when, job status). Missing ids have no database run."""
    specs: dict[str, tuple[str, str]] = {}
    host_types: set[str] = set()
    copy_ids: set[int] = set()
    nmap_ids: set[int] = set()
    for job_id in job_ids:
        spec = _run_spec(job_id)
        if spec is None:
            continue
        specs[job_id] = spec
        kind, value = spec
        if kind == "host":
            host_types.add(value.split(":", 1)[0])
        elif kind == "fleet":
            host_types.add(value)
        elif kind == "copy":
            copy_ids.add(int(value))
        elif kind == "nmap":
            nmap_ids.add(int(value))
    if not specs:
        return {}
    try:
        from sqlmodel import Session

        from ..database import get_engine

        with Session(get_engine()) as session:
            indexed = _latest_host_and_fleet(session, host_types)
            copies = _latest_copies(session, copy_ids)
            scans = _latest_nmap(session, nmap_ids)
    except Exception:
        logger.exception("Could not read the last scheduled runs")
        return {}

    fleet_latest: dict[str, Any] = {}
    for (job_type, _server_id), job in indexed.items():
        current = fleet_latest.get(job_type)
        if current is None or int(job.id) > int(current.id):
            fleet_latest[job_type] = job

    out: dict[str, tuple[datetime | None, str]] = {}
    for job_id, (kind, value) in specs.items():
        status = None
        when = None
        if kind == "host":
            job_type, server_text = value.split(":", 1)
            job = indexed.get((job_type, int(server_text)))
            if job is not None:
                status = job.status
                when = _when(job.status, job.finished_at, job.started_at)
        elif kind == "fleet":
            job = fleet_latest.get(value)
            if job is not None:
                status = job.status
                when = _when(job.status, job.finished_at, job.started_at)
        elif kind == "copy":
            hit = copies.get(int(value))
            if hit is not None:
                status, finished, started = hit
                when = _when(status, finished, started)
        elif kind == "nmap":
            run = scans.get(int(value))
            if run is not None:
                status = run.status
                when = _when(run.status, run.finished_at, run.started_at)
        if status:
            out[job_id] = (when, str(status))
    return out


def _group_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {key: [] for key in _GROUP_ORDER}
    for row in rows:
        buckets.setdefault(str(row.get("group_key") or "instance"), []).append(row)
    groups: list[dict[str, Any]] = []
    for key in _GROUP_ORDER:
        members = buckets.get(key) or []
        if not members:
            continue
        members.sort(key=lambda row: (str(row.get("subject") or "").lower(), str(row.get("id") or "")))
        label, kind = _GROUP_META.get(key, ("Scheduled", "instance"))
        groups.append(
            {
                "key": key,
                "label": label,
                "kind": kind,
                "heading": _HEADING.get(kind, "Job"),
                "count": len(members),
                "jobs": members,
            }
        )
    return groups


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
            "groups": [],
        }
    running = bool(getattr(scheduler, "running", False))
    if not running:
        return {
            "available": True,
            "running": False,
            "unreadable": False,
            "jobs": [],
            "groups": [],
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
            "groups": [],
        }
    rows: list[dict[str, Any]] = []
    for job in raw:
        job_id = str(getattr(job, "id", "") or "").strip()
        name = str(getattr(job, "name", "") or "").strip() or job_id or "Scheduled job"
        nxt = getattr(job, "next_run_time", None)
        if not isinstance(nxt, datetime):
            nxt = None
        group = classify_scheduler_job(job_id, name)
        rows.append(
            {
                "id": job_id,
                "name": name,
                "next_at": nxt,
                **group,
            }
        )
    runs = last_runs_for([str(row["id"]) for row in rows])
    for row in rows:
        when, job_status = runs.get(str(row["id"]), (None, None))
        status, status_label, status_class = _pill(job_status)
        nxt = row.get("next_at")
        row["last_display"] = _display(when, empty=NONE_YET)
        row["next_display"] = _display(nxt if isinstance(nxt, datetime) else None, empty=NOT_SCHEDULED)
        row["status"] = status
        row["status_label"] = status_label
        row["status_class"] = status_class
    rows.sort(key=_sort_key)
    for row in rows:
        row.pop("next_at", None)
    return {
        "available": True,
        "running": True,
        "unreadable": False,
        "jobs": rows,
        "groups": _group_rows(rows),
    }

"""
Backup progress tracking extracted from backup.py.

Handles:
- In-memory + Redis progress state for long-running rsync
- Throttled updates
- Buffered Job.details flushes (for Celery worker → UI)
- Log line filtering for rsync noise
- Public APIs used by web UI, tasks, jobs

Kept lightweight: no classes, pure functions + module state.
Re-exported from backup.py for full backward compatibility.
"""

import json
import os
import time
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# --- State (progress only) ---
_redis_client = None

_last_progress_update: dict[str, float] = {}
_progress_cache: dict[str, tuple[float, dict]] = {}   # (timestamp, data) for lightweight caching
_active_job_id: dict[str, int] = {}  # hostname -> Job.id (worker feeds DB)
_job_db_last_update: dict[int, float] = {}
_job_details_buffer: dict[int, dict] = {}  # in-worker buffer between DB flushes

# Min seconds between Job.details commits during long rsync runs
_JOB_DB_COMMIT_INTERVAL = 10.0
_PROGRESS_THROTTLE_SEC = 3.0
_MAX_LOG_LINE_LEN = 240
_MAX_LOG_LINES = 15

# In-memory fallback progress (hostname -> dict)
_backup_progress: dict[str, dict] = {}


def _get_redis():
    """Return a Redis client (shared with Celery) or None if unavailable."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis
        url = (
            os.getenv("CELERY_BROKER_URL")
            or os.getenv("CELERY_RESULT_BACKEND")
            or "redis://localhost:6379/0"
        )
        client = redis.from_url(url, decode_responses=True)
        client.ping()
        _redis_client = client
        logger.debug("[backup] Using Redis for cross-process progress tracking")
    except Exception as e:
        logger.debug(f"[backup] Redis not available for progress, using in-memory only: {e}")
        _redis_client = None
    return _redis_client


def get_backup_progress(hostname: str) -> dict:
    """Return current backup progress for UI polling / SSE.
    Always includes 'last_updated' (unix timestamp).
    Uses 2s in-process cache so repeated polls (every 2-3s) are almost free.
    Tries Redis first for cross-process visibility (Celery → web).
    """
    now = time.time()

    # Lightweight in-process cache (2s TTL) - makes frequent polling from frontend very cheap
    if hostname in _progress_cache:
        ts, data = _progress_cache[hostname]
        if now - ts < 2.0:
            return data

    r = _get_redis()
    if r:
        try:
            data = r.get(f"piherder:backup_progress:{hostname}")
            if data:
                parsed = json.loads(data)
                if "last_updated" not in parsed:
                    parsed["last_updated"] = now
                _progress_cache[hostname] = (now, parsed)
                return parsed
        except Exception:
            pass

    # Fallback to memory
    data = _backup_progress.get(hostname, {"current": None, "log_lines": [], "last_updated": now})
    if "last_updated" not in data:
        data["last_updated"] = now
    _progress_cache[hostname] = (now, data)
    return data


def _truncate_log_line(line: str) -> str:
    line = (line or "").strip()
    if len(line) > _MAX_LOG_LINE_LEN:
        return line[: _MAX_LOG_LINE_LEN - 3] + "..."
    return line


def _is_rsync_progress2_line(line: str) -> bool:
    """--info=progress2 emits one updating status line (xfr#, to-chk=, %, MB/s). Never log these."""
    low = (line or "").lower()
    if "to-chk=" in low or "xfr#" in low:
        return True
    if "%" in line and any(u in low for u in ("mb/s", "kb/s", "gb/s", "bytes/sec", "/s")):
        return True
    return False


def _rsync_line_worth_logging(line: str) -> bool:
    """Only real messages — never progress2 or per-file -v noise."""
    s = _truncate_log_line(line)
    if not s or _is_rsync_progress2_line(s):
        return False
    low = s.lower()
    if any(w in low for w in ("error", "fail", "denied", "warning", "rsync:", "permission")):
        return True
    if s.startswith("Backing up ") or s.startswith("Completed ") or s.startswith("Failed "):
        return True
    if s.startswith("/") or s.startswith("./"):
        return False
    return False


def _merge_progress_buffer(job_id: int, current: str | None, log_line: str | None) -> dict:
    buf = _job_details_buffer.setdefault(
        job_id, {"current": None, "log_lines": [], "last_updated": time.time()}
    )
    if current is not None:
        buf["current"] = current
    if log_line:
        line = _truncate_log_line(log_line)
        if line:
            lines = buf.setdefault("log_lines", [])
            if not lines or lines[-1] != line:
                lines.append(line)
            buf["log_lines"] = lines[-_MAX_LOG_LINES:]
    buf["last_updated"] = time.time()
    return buf


def _flush_job_progress_db(job_id: int, force: bool = False) -> None:
    """Commit buffered progress to Job.details (throttled)."""
    buf = _job_details_buffer.get(job_id)
    if not buf:
        return
    now = time.time()
    last = _job_db_last_update.get(job_id, 0)
    if not force and (now - last) < _JOB_DB_COMMIT_INTERVAL:
        return
    _job_db_last_update[job_id] = now
    try:
        from sqlmodel import Session
        from ..database import engine
        from ..models import Job
        import json as _json

        with Session(engine) as s:
            job = s.get(Job, job_id)
            if not job:
                return
            details = {}
            if job.details:
                try:
                    details = _json.loads(job.details)
                except Exception:
                    pass
            # Preserve metadata (source_filter, started_at, result_summary) — only update progress fields
            details["current"] = buf.get("current")
            details["log_lines"] = list(buf.get("log_lines", []))[-_MAX_LOG_LINES:]
            details["last_updated"] = buf.get("last_updated", now)
            job.details = _json.dumps(details)
            if job.status == "pending":
                job.status = "running"
            if job.started_at is None:
                job.started_at = datetime.utcnow()
            s.add(job)
            s.commit()
    except Exception as e:
        logger.debug(f"[backup] Job.details flush failed for job {job_id}: {e}")


def _update_job_progress_db(job_id: int, current: str | None, log_line: str | None, force: bool = False):
    """Buffer progress in worker memory; flush to DB on interval."""
    _merge_progress_buffer(job_id, current, log_line)
    _flush_job_progress_db(job_id, force=force)


def get_job_backup_progress_from_db(job) -> dict | None:
    """Slim progress read for web poll — avoids parsing huge result_summary when possible."""
    if not job:
        return None
    now = time.time()
    if not job.details:
        if job.status == "pending":
            return {
                "current": "queued",
                "log_lines": ["Waiting for worker…"],
                "last_updated": now,
                "status": job.status,
                "job_id": job.id,
            }
        if job.status == "running":
            return {
                "current": "starting",
                "log_lines": ["Backup starting…"],
                "last_updated": now,
                "status": job.status,
                "job_id": job.id,
            }
        return None
    try:
        details = json.loads(job.details)
    except Exception:
        if job.status in ("pending", "running"):
            return {
                "current": "queued" if job.status == "pending" else "starting",
                "log_lines": ["Waiting for worker…" if job.status == "pending" else "Backup starting…"],
                "last_updated": now,
                "status": job.status,
                "job_id": job.id,
            }
        return None
    return {
        "current": details.get("current"),
        "log_lines": list(details.get("log_lines", []))[-15:],
        "last_updated": details.get("last_updated") or now,
        "status": job.status,
        "job_id": job.id,
        "error": details.get("error"),
    }


def clear_job_progress_buffer(job_id: int | None):
    if job_id:
        _job_details_buffer.pop(job_id, None)
        _job_db_last_update.pop(job_id, None)


def _set_progress(hostname: str, current: str | None = None, log_line: str | None = None, force: bool = False):
    """Update progress — heavily throttled. Job.details is the UI source of truth."""
    now = time.time()
    last = _last_progress_update.get(hostname, 0)

    if hostname not in _backup_progress:
        _backup_progress[hostname] = {"current": None, "log_lines": [], "last_updated": now}

    important_log = False
    if log_line:
        low = log_line.lower()
        important_log = any(
            word in low
            for word in ("error", "fail", "denied", "complete", "finished", "skipped", "warning", "backing up", "still backing", "failed", "preparing")
        )

    if not force and not important_log and (now - last) < _PROGRESS_THROTTLE_SEC:
        return _backup_progress[hostname]

    _last_progress_update[hostname] = now
    p = _backup_progress[hostname]
    if current is not None:
        p["current"] = current
    if log_line and important_log:
        line = _truncate_log_line(log_line)
        if line:
            lines = p.setdefault("log_lines", [])
            if not lines or lines[-1] != line:
                lines.append(line)
            p["log_lines"] = lines[-_MAX_LOG_LINES:]
    p["last_updated"] = now
    _progress_cache[hostname] = (now, p)

    job_id = _active_job_id.get(hostname)
    if job_id:
        _update_job_progress_db(job_id, current, log_line if important_log else None, force=force or important_log)
        return p

    # Legacy Redis path only when no Job (non-Celery fallback)
    r = _get_redis()
    if r:
        try:
            r.set(f"piherder:backup_progress:{hostname}", json.dumps(p), ex=3600)
        except Exception:
            pass
    return p


def _clear_progress(hostname: str):
    r = _get_redis()
    if r:
        try:
            r.delete(f"piherder:backup_progress:{hostname}")
        except Exception:
            pass
    _backup_progress.pop(hostname, None)
    _last_progress_update.pop(hostname, None)
    _progress_cache.pop(hostname, None)

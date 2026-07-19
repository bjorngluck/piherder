"""Merge progress + log_lines into Job.details (Jobs UI / JobHold)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional, Sequence

from sqlmodel import Session

from ...models import Job

logger = logging.getLogger(__name__)

_MAX_LOG_LINES = 80


def merge_job_details(
    session: Session,
    job_id: int | None,
    *,
    status: str | None = None,
    current: str | None = None,
    summary: str | None = None,
    log_line: str | None = None,
    log_lines: Sequence[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Update Job row: status, timestamps, merge details (append log_lines)."""
    if not job_id:
        return
    job = session.get(Job, job_id)
    if not job:
        return
    if job.status == "cancelled":
        return
    if (
        job.status in ("success", "failed")
        and job.finished_at
        and status in ("running", "pending", None)
    ):
        return

    prev: dict[str, Any] = {}
    if job.details:
        try:
            parsed = json.loads(job.details)
            if isinstance(parsed, dict):
                prev = parsed
        except Exception:
            prev = {}

    if extra:
        for k, v in extra.items():
            if k == "log_lines":
                continue
            prev[k] = v

    if current is not None:
        prev["current"] = current
    if summary is not None:
        prev["summary"] = summary

    new_lines: list[str] = []
    if log_line:
        new_lines.append(str(log_line)[:500])
    if log_lines:
        new_lines.extend(str(x)[:500] for x in log_lines if x is not None)

    if new_lines:
        lines = list(prev.get("log_lines") or [])
        for line in new_lines:
            if not lines or lines[-1] != line:
                lines.append(line)
        prev["log_lines"] = lines[-_MAX_LOG_LINES:]

    if status:
        job.status = status
        if status == "running" and not job.started_at:
            job.started_at = datetime.utcnow()
        if status in ("success", "failed", "cancelled"):
            job.finished_at = datetime.utcnow()

    job.details = json.dumps(prev, separators=(",", ":"), default=str)
    session.add(job)
    session.commit()


def job_log_timestamp() -> str:
    return datetime.utcnow().strftime("%H:%M:%S")


def stamp_line(msg: str) -> str:
    return f"[{job_log_timestamp()}] {msg}"

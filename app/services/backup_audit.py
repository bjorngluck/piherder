"""Append-only audit trail events for backup job lifecycle."""
from __future__ import annotations

import json
from datetime import datetime

from sqlmodel import Session

from ..models import AuditLog, Job

PHASE_ACTIONS: dict[str, tuple[str, str]] = {
    "request": ("backup_request", "success"),
    "queued": ("backup_queued", "queued"),
    "running": ("backup_running", "running"),
    "success": ("backup", "success"),
    "failed": ("backup", "failed"),
}


def job_meta(job: Job) -> dict:
    try:
        return json.loads(job.details or "{}")
    except Exception:
        return {}


def record_backup_audit_event(
    session: Session,
    *,
    server_id: int,
    job_id: int,
    phase: str,
    user_id: int | None = None,
    source_filter: str | None = None,
    message: str | None = None,
    output_snippet: str | dict | None = None,
) -> AuditLog:
    if phase not in PHASE_ACTIONS:
        raise ValueError(f"Unknown backup audit phase: {phase}")
    action, status = PHASE_ACTIONS[phase]
    payload: dict = {"job_id": job_id, "phase": phase}
    if source_filter:
        payload["source_filter"] = source_filter
    if message:
        payload["message"] = message

    snippet = None
    if output_snippet is not None:
        if isinstance(output_snippet, dict):
            snippet = json.dumps(output_snippet)[:2000]
        else:
            snippet = str(output_snippet)[:2000]

    now = datetime.utcnow()
    audit = AuditLog(
        user_id=user_id,
        server_id=server_id,
        action=action,
        status=status,
        details=json.dumps(payload),
        output_snippet=snippet,
        started_at=now,
        finished_at=now,
    )
    session.add(audit)
    return audit


def record_backup_audit_from_job(
    session: Session,
    job: Job,
    phase: str,
    *,
    message: str | None = None,
    output_snippet: str | dict | None = None,
) -> AuditLog:
    meta = job_meta(job)
    return record_backup_audit_event(
        session,
        server_id=job.server_id,
        job_id=job.id,
        phase=phase,
        user_id=meta.get("user_id"),
        source_filter=meta.get("source_filter"),
        message=message,
        output_snippet=output_snippet,
    )
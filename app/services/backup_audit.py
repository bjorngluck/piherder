"""Append-only audit trail events for backup job lifecycle."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlmodel import Session

from ..models import AuditLog, Job
from .audit_write import make_audit_log, resolve_client_ip

PHASE_ACTIONS: dict[str, tuple[str, str]] = {
    "request": ("backup_request", "success"),
    "queued": ("backup_queued", "queued"),
    "running": ("backup_running", "running"),
    "success": ("backup", "success"),
    "failed": ("backup", "failed"),
    "cancelled": ("backup", "cancelled"),
}

# Compact audit payload — full result_summary can exceed DB snippet limits and
# break JSON when truncated mid-string. Keep sizes + errors for the UI summary.
_SNIPPET_MAX = 4000


def job_meta(job: Job) -> dict:
    try:
        return json.loads(job.details or "{}")
    except Exception:
        return {}


def compact_backup_snippet(summary: Any, *, ok: bool) -> dict:
    """Shrink run_backup() result for AuditLog.output_snippet (size + errors)."""
    if not isinstance(summary, dict):
        return {"ok": ok, "raw": str(summary)[:500]}
    out: dict[str, Any] = {
        "server": summary.get("server"),
        "ok": ok,
    }
    if summary.get("error"):
        out["error"] = str(summary["error"])[:500]
    if summary.get("timestamp"):
        out["timestamp"] = summary.get("timestamp")
    results_out: list[dict] = []
    total = 0
    for r in (summary.get("results") or [])[:50]:
        if not isinstance(r, dict):
            continue
        item: dict[str, Any] = {"source": r.get("source")}
        if r.get("skipped"):
            item["skipped"] = True
            if r.get("reason"):
                item["reason"] = r.get("reason")
        if r.get("error"):
            item["error"] = str(r["error"])[:240]
        if r.get("rc") is not None:
            item["rc"] = r.get("rc")
        if r.get("size_bytes") is not None:
            try:
                sb = int(r["size_bytes"])
            except (TypeError, ValueError):
                sb = 0
            item["size_bytes"] = sb
            total += sb
            if r.get("size_human"):
                item["size_human"] = r.get("size_human")
        if r.get("dest"):
            item["dest"] = str(r["dest"])[:200]
        results_out.append(item)
    out["results"] = results_out
    if total:
        out["total_size_bytes"] = total
    return out


def record_backup_audit_event(
    session: Session,
    *,
    server_id: int,
    job_id: int,
    phase: str,
    user_id: int | None = None,
    api_token_id: int | None = None,
    api_token_name: str | None = None,
    source_filter: str | None = None,
    message: str | None = None,
    output_snippet: str | dict | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    client_ip: str | None = None,
) -> AuditLog:
    if phase not in PHASE_ACTIONS:
        raise ValueError(f"Unknown backup audit phase: {phase}")
    action, status = PHASE_ACTIONS[phase]
    payload: dict = {"job_id": job_id, "phase": phase}
    if source_filter:
        payload["source_filter"] = source_filter
    if message:
        payload["message"] = message
    if api_token_id is not None:
        payload["api_token_id"] = api_token_id
    if api_token_name:
        payload["api_token_name"] = api_token_name

    snippet = None
    if output_snippet is not None:
        if isinstance(output_snippet, dict):
            snippet = json.dumps(output_snippet, default=str)[:_SNIPPET_MAX]
        else:
            snippet = str(output_snippet)[:_SNIPPET_MAX]

    now = datetime.utcnow()
    # Prefer job wall-clock for terminal events so duration/summary are meaningful
    start = started_at or now
    end = finished_at or now
    if phase in ("request", "queued", "running"):
        start = end = now

    audit = make_audit_log(
        user_id=user_id,
        server_id=server_id,
        api_token_id=api_token_id,
        api_token_name=api_token_name,
        action=action,
        status=status,
        details=json.dumps(payload),
        output_snippet=snippet,
        started_at=start,
        finished_at=end,
        client_ip=client_ip,
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
    tok_id = meta.get("api_token_id")
    try:
        tok_id = int(tok_id) if tok_id is not None else None
    except (TypeError, ValueError):
        tok_id = None
    # Prefer IP captured when the job was requested (survives Celery workers)
    job_ip = meta.get("client_ip")
    return record_backup_audit_event(
        session,
        server_id=job.server_id,
        job_id=job.id,
        phase=phase,
        user_id=meta.get("user_id"),
        api_token_id=tok_id,
        api_token_name=meta.get("api_token_name"),
        source_filter=meta.get("source_filter"),
        message=message,
        output_snippet=output_snippet,
        started_at=job.started_at or job.created_at,
        finished_at=job.finished_at,
        client_ip=resolve_client_ip(None, fallback=job_ip),
    )
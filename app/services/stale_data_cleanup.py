"""Stale Jobs / Audit / nmap-run cleanup (opt-in, configurable retention).

Fleet-level Job type ``stale_data_cleanup`` — scheduled or Run now from Settings.
Never deletes pending/running jobs. Distinct from per-server backup file retention.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session, col, select

from ..models import AuditLog, Job, NmapScanRun, NmapScriptResult
from . import app_settings as app_cfg
from .nmap.job_progress import merge_job_details, stamp_line

logger = logging.getLogger(__name__)

JOB_TYPE = "stale_data_cleanup"
TERMINAL_JOB_STATUSES = ("success", "failed", "cancelled")
MIN_DAYS = 1
MAX_DAYS = 3650
DEFAULT_DAYS = 30


def _clamp_days(raw: Any, default: int = DEFAULT_DAYS) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = default
    return max(MIN_DAYS, min(MAX_DAYS, n))


def cleanup_config(cfg: dict | None = None) -> dict[str, Any]:
    """Normalized settings for UI + runner."""
    c = cfg if cfg is not None else app_cfg.load_settings()
    return {
        "enabled": bool(c.get("data_cleanup_enabled")),
        "cron": (c.get("data_cleanup_cron") or "30 4 * * *").strip() or "30 4 * * *",
        "jobs_enabled": bool(c.get("data_cleanup_jobs_enabled", True)),
        "jobs_days": _clamp_days(c.get("data_cleanup_jobs_days"), DEFAULT_DAYS),
        "audit_enabled": bool(c.get("data_cleanup_audit_enabled", True)),
        "audit_days": _clamp_days(c.get("data_cleanup_audit_days"), DEFAULT_DAYS),
        "nmap_enabled": bool(c.get("data_cleanup_nmap_enabled", False)),
        "nmap_days": _clamp_days(c.get("data_cleanup_nmap_days"), DEFAULT_DAYS),
    }


def _cutoff(days: int) -> datetime:
    return datetime.utcnow() - timedelta(days=days)


def _job_is_stale(job: Job, cut: datetime) -> bool:
    if job.status not in TERMINAL_JOB_STATUSES:
        return False
    ts = job.finished_at or job.created_at
    return bool(ts and ts < cut)


def _nmap_run_is_stale(run: NmapScanRun, cut: datetime) -> bool:
    if run.status not in ("success", "failed", "cancelled"):
        return False
    ts = run.finished_at or run.created_at
    return bool(ts and ts < cut)


def preview_cleanup(session: Session, cfg: dict | None = None) -> dict[str, Any]:
    """Count rows that would be deleted (no writes)."""
    conf = cleanup_config(cfg)
    out: dict[str, Any] = {
        "jobs": 0,
        "audit": 0,
        "nmap_runs": 0,
        "config": conf,
    }
    if conf["jobs_enabled"]:
        cut = _cutoff(conf["jobs_days"])
        out["jobs"] = sum(
            1
            for j in session.exec(
                select(Job).where(col(Job.status).in_(list(TERMINAL_JOB_STATUSES)))
            ).all()
            if _job_is_stale(j, cut)
        )

    if conf["audit_enabled"]:
        cut = _cutoff(conf["audit_days"])
        out["audit"] = len(
            list(session.exec(select(AuditLog).where(AuditLog.started_at < cut)).all())
        )

    if conf["nmap_enabled"]:
        cut = _cutoff(conf["nmap_days"])
        out["nmap_runs"] = sum(
            1
            for r in session.exec(select(NmapScanRun)).all()
            if _nmap_run_is_stale(r, cut)
        )
    out["total"] = int(out["jobs"]) + int(out["audit"]) + int(out["nmap_runs"])
    return out


def _delete_old_jobs(session: Session, days: int, *, keep_job_id: int | None) -> int:
    cut = _cutoff(days)
    rows = list(
        session.exec(
            select(Job).where(col(Job.status).in_(list(TERMINAL_JOB_STATUSES)))
        ).all()
    )
    deleted = 0
    for j in rows:
        if keep_job_id and j.id == keep_job_id:
            continue
        if not _job_is_stale(j, cut):
            continue
        session.delete(j)
        deleted += 1
    if deleted:
        session.commit()
    return deleted


def _delete_old_audit(session: Session, days: int) -> int:
    cut = _cutoff(days)
    rows = list(session.exec(select(AuditLog).where(AuditLog.started_at < cut)).all())
    for al in rows:
        session.delete(al)
    if rows:
        session.commit()
    return len(rows)


def _delete_old_nmap_runs(session: Session, days: int) -> dict[str, int]:
    cut = _cutoff(days)
    data_root = Path(os.environ.get("DATA_ROOT") or "/data")
    rows = list(session.exec(select(NmapScanRun)).all())
    deleted = 0
    files = 0
    for run in rows:
        if not _nmap_run_is_stale(run, cut):
            continue
        # script results for this run
        for sc in session.exec(
            select(NmapScriptResult).where(NmapScriptResult.run_id == run.id)
        ).all():
            session.delete(sc)
        if run.artifact_path:
            try:
                p = Path(run.artifact_path)
                if not p.is_absolute():
                    p = data_root / p
                if p.is_file():
                    p.unlink()
                    files += 1
            except OSError as e:
                logger.debug("nmap artifact delete failed %s: %s", run.artifact_path, e)
        session.delete(run)
        deleted += 1
    if deleted:
        session.commit()
    return {"runs": deleted, "files": files}


def run_stale_data_cleanup(
    session: Session,
    *,
    job_id: int | None = None,
    dry_run: bool = False,
    cfg: dict | None = None,
) -> dict[str, Any]:
    """Execute cleanup; update Job log_lines when job_id set."""
    conf = cleanup_config(cfg)
    merge_job_details(
        session,
        job_id,
        status="running",
        current="starting",
        summary="Stale data cleanup starting…",
        log_line=stamp_line(
            f"Config: jobs={conf['jobs_enabled']}/{conf['jobs_days']}d "
            f"audit={conf['audit_enabled']}/{conf['audit_days']}d "
            f"nmap={conf['nmap_enabled']}/{conf['nmap_days']}d "
            f"dry_run={dry_run}"
        ),
    )

    prev = preview_cleanup(session, conf)
    merge_job_details(
        session,
        job_id,
        status="running",
        current="preview",
        summary=(
            f"Would purge: jobs={prev['jobs']} audit={prev['audit']} "
            f"nmap_runs={prev['nmap_runs']}"
        ),
        log_line=stamp_line(
            f"Preview: {prev['jobs']} jobs, {prev['audit']} audit, "
            f"{prev['nmap_runs']} nmap runs"
        ),
        extra={"preview": prev},
    )

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "preview": prev,
        "deleted_jobs": 0,
        "deleted_audit": 0,
        "deleted_nmap_runs": 0,
        "deleted_nmap_files": 0,
        "status": "success",
    }

    if dry_run:
        summary = (
            f"Dry-run: would delete {prev['jobs']} jobs, {prev['audit']} audit, "
            f"{prev['nmap_runs']} nmap runs"
        )
        merge_job_details(
            session,
            job_id,
            status="success",
            current="completed",
            summary=summary,
            log_line=stamp_line(summary),
            extra={"result": result, "result_snippet": summary},
        )
        return result

    try:
        if conf["jobs_enabled"]:
            n = _delete_old_jobs(session, conf["jobs_days"], keep_job_id=job_id)
            result["deleted_jobs"] = n
            merge_job_details(
                session,
                job_id,
                status="running",
                current="jobs",
                log_line=stamp_line(f"Deleted {n} job row(s) older than {conf['jobs_days']}d"),
            )
        if conf["audit_enabled"]:
            n = _delete_old_audit(session, conf["audit_days"])
            result["deleted_audit"] = n
            merge_job_details(
                session,
                job_id,
                status="running",
                current="audit",
                log_line=stamp_line(
                    f"Deleted {n} audit row(s) older than {conf['audit_days']}d"
                ),
            )
        if conf["nmap_enabled"]:
            nm = _delete_old_nmap_runs(session, conf["nmap_days"])
            result["deleted_nmap_runs"] = nm.get("runs", 0)
            result["deleted_nmap_files"] = nm.get("files", 0)
            merge_job_details(
                session,
                job_id,
                status="running",
                current="nmap",
                log_line=stamp_line(
                    f"Deleted {nm.get('runs', 0)} nmap run(s), "
                    f"{nm.get('files', 0)} XML file(s) older than {conf['nmap_days']}d"
                ),
            )

        summary = (
            f"Purged jobs={result['deleted_jobs']} audit={result['deleted_audit']} "
            f"nmap_runs={result['deleted_nmap_runs']}"
        )
        merge_job_details(
            session,
            job_id,
            status="success",
            current="completed",
            summary=summary,
            log_line=stamp_line(summary),
            extra={"result": result, "result_snippet": summary},
        )
        # Fleet audit
        try:
            from .audit_write import make_audit_log

            session.add(
                make_audit_log(
                    user_id=None,
                    server_id=None,
                    action="stale_data_cleanup",
                    status="success",
                    details=summary,
                    output_snippet=json.dumps(result, default=str)[:2000],
                    started_at=datetime.utcnow(),
                    finished_at=datetime.utcnow(),
                )
            )
            session.commit()
        except Exception as e:
            logger.debug("audit for cleanup skipped: %s", e)
        return result
    except Exception as e:
        logger.exception("stale_data_cleanup failed")
        err = str(e)[:500]
        merge_job_details(
            session,
            job_id,
            status="failed",
            current="failed",
            summary=err,
            log_line=stamp_line(f"ERROR: {err}"),
            extra={"error": err},
        )
        result["status"] = "failed"
        result["error"] = err
        return result


def enqueue_stale_data_cleanup(
    session: Session,
    *,
    user_id: int | None = None,
    dry_run: bool = False,
) -> Job:
    """Create Job and dispatch to default Celery queue."""
    from ..celery_app import celery

    conf = cleanup_config()
    job = Job(
        server_id=None,
        job_type=JOB_TYPE,
        status="pending",
        details=json.dumps(
            {
                "current": "queued",
                "summary": (
                    f"Queued stale data cleanup"
                    f"{' (dry-run)' if dry_run else ''}"
                ),
                "user_id": user_id,
                "dry_run": dry_run,
                "config": conf,
                "log_lines": [stamp_line("Queued stale data cleanup")],
            },
            separators=(",", ":"),
        ),
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    async_result = celery.send_task(
        "app.tasks.stale_data_cleanup",
        kwargs={"job_id": job.id, "dry_run": dry_run},
    )
    job.celery_task_id = async_result.id
    session.add(job)
    session.commit()
    session.refresh(job)
    return job

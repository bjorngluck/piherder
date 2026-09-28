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

from ..models import AuditLog, Job, NmapDevice, NmapScanRun, NmapScriptResult
from . import app_settings as app_cfg
from .audit_write import make_audit_log, resolve_client_ip
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


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _actor_from_job(session: Session, job_id: int | None) -> dict[str, Any]:
    """Actor snapshotted onto the job at enqueue (request is gone in the worker)."""
    actor: dict[str, Any] = {
        "user_id": None,
        "api_token_id": None,
        "api_token_name": None,
        "client_ip": None,
    }
    if not job_id:
        return actor
    job = session.get(Job, job_id)
    if not job or not job.details:
        return actor
    try:
        data = json.loads(job.details) or {}
    except Exception:
        return actor
    if not isinstance(data, dict):
        return actor
    actor["user_id"] = _opt_int(data.get("user_id"))
    actor["api_token_id"] = _opt_int(data.get("api_token_id"))
    name = data.get("api_token_name")
    if name:
        actor["api_token_name"] = str(name)[:120]
    ip = data.get("client_ip")
    if ip:
        actor["client_ip"] = str(ip)[:64]
    return actor


def _record_cleanup_audit(
    session: Session,
    job_id: int | None,
    *,
    status: str,
    summary: str,
    result: dict | None = None,
) -> None:
    """Fleet audit for a finished cleanup. Copies token + IP from the job row."""
    actor = _actor_from_job(session, job_id)
    session.add(
        make_audit_log(
            user_id=actor["user_id"],
            server_id=None,
            api_token_id=actor["api_token_id"],
            api_token_name=actor["api_token_name"],
            action=JOB_TYPE,
            status=status,
            details=summary,
            output_snippet=(
                json.dumps(result, default=str)[:2000] if result is not None else None
            ),
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
            client_ip=actor["client_ip"],
        )
    )
    session.commit()


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


def _detach_scan_runs_from_jobs(session: Session, job_ids: list[int]) -> None:
    """Null nmapscanrun.job_id so deleting old jobs does not hit that foreign key."""
    if not job_ids:
        return
    runs = list(
        session.exec(select(NmapScanRun).where(col(NmapScanRun.job_id).in_(job_ids))).all()
    )
    for run in runs:
        run.job_id = None
        session.add(run)
    if runs:
        session.flush()


def _delete_old_jobs(session: Session, days: int, *, keep_job_id: int | None) -> int:
    cut = _cutoff(days)
    rows = list(
        session.exec(
            select(Job).where(col(Job.status).in_(list(TERMINAL_JOB_STATUSES)))
        ).all()
    )
    stale: list[Job] = []
    for j in rows:
        if keep_job_id and j.id == keep_job_id:
            continue
        if not _job_is_stale(j, cut):
            continue
        stale.append(j)
    if not stale:
        return 0
    _detach_scan_runs_from_jobs(session, [int(j.id) for j in stale if j.id is not None])
    for j in stale:
        session.delete(j)
    session.commit()
    return len(stale)


def _delete_old_audit(session: Session, days: int) -> int:
    cut = _cutoff(days)
    rows = list(session.exec(select(AuditLog).where(AuditLog.started_at < cut)).all())
    for al in rows:
        session.delete(al)
    if rows:
        session.commit()
    return len(rows)


def _clear_device_last_run(session: Session, run_ids: list[int]) -> None:
    """Null nmapdevice.last_run_id before those scan runs are deleted."""
    if not run_ids:
        return
    devices = list(
        session.exec(
            select(NmapDevice).where(col(NmapDevice.last_run_id).in_(run_ids))
        ).all()
    )
    for device in devices:
        device.last_run_id = None
        session.add(device)
    if devices:
        session.flush()


def _delete_old_nmap_runs(session: Session, days: int) -> dict[str, int]:
    cut = _cutoff(days)
    data_root = Path(os.environ.get("DATA_ROOT") or "/data")
    stale = [run for run in session.exec(select(NmapScanRun)).all() if _nmap_run_is_stale(run, cut)]
    if not stale:
        return {"runs": 0, "files": 0}
    run_ids = [int(run.id) for run in stale if run.id is not None]
    _clear_device_last_run(session, run_ids)
    deleted = 0
    files = 0
    for run in stale:
        for sc in session.exec(
            select(NmapScriptResult).where(NmapScriptResult.run_id == run.id)
        ).all():
            session.delete(sc)
    session.flush()
    for run in stale:
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
        "purged_console_transcripts": 0,
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
        if job_id:
            try:
                _record_cleanup_audit(
                    session, job_id, status="success", summary=summary, result=result
                )
            except Exception as e:
                logger.debug("audit for cleanup dry-run skipped: %s", e)
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
        try:
            from . import console_audit as ca
            from . import ssh_console as cons

            n = ca.purge_transcript_bodies(
                session, older_than_days=cons.audit_retention_days()
            )
            result["purged_console_transcripts"] = n
            merge_job_details(
                session,
                job_id,
                status="running",
                current="console_audit",
                log_line=stamp_line(f"Purged {n} console transcript body(ies)"),
            )
        except Exception as e:
            logger.debug("console transcript retention skipped: %s", e)
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
        try:
            _record_cleanup_audit(
                session, job_id, status="success", summary=summary, result=result
            )
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
        try:
            _record_cleanup_audit(
                session, job_id, status="failed", summary=err, result=result
            )
        except Exception as audit_err:
            logger.debug("audit for cleanup failure skipped: %s", audit_err)
        return result


def enqueue_stale_data_cleanup(
    session: Session,
    *,
    user_id: int | None = None,
    api_token_id: int | None = None,
    api_token_name: str | None = None,
    client_ip: str | None = None,
    dry_run: bool = False,
) -> Job:
    """Create Job and dispatch to default Celery queue.

    Snapshots the HTTP actor (user, API token, client IP) onto the job so the
    worker can write the audit after the request context is gone.
    """
    from ..celery_app import celery

    conf = cleanup_config()
    ip = resolve_client_ip(client_ip)
    payload: dict[str, Any] = {
        "current": "queued",
        "summary": (
            f"Queued stale data cleanup"
            f"{' (dry-run)' if dry_run else ''}"
        ),
        "user_id": user_id,
        "dry_run": dry_run,
        "config": conf,
        "log_lines": [stamp_line("Queued stale data cleanup")],
    }
    if api_token_id is not None:
        payload["api_token_id"] = int(api_token_id)
    if api_token_name:
        payload["api_token_name"] = str(api_token_name)[:120]
    if ip:
        payload["client_ip"] = ip
    job = Job(
        server_id=None,
        job_type=JOB_TYPE,
        status="pending",
        details=json.dumps(payload, separators=(",", ":")),
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
    if user_id is not None or api_token_id is not None:
        session.add(
            make_audit_log(
                user_id=user_id,
                server_id=None,
                api_token_id=api_token_id,
                api_token_name=api_token_name,
                action="stale_data_cleanup_queued",
                status="success",
                details=f"job={job.id} dry_run={dry_run}",
                started_at=datetime.utcnow(),
                finished_at=datetime.utcnow(),
                client_ip=ip,
            )
        )
        session.commit()
    return job

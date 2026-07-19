# app/tasks.py
"""
Celery tasks for PiHerder.

Worker feeds DB (Job + Server + AuditLog) with status.
UI reads only from DB (minimal polling).

Multi-worker: backup tasks take a per-server Redis mutex so N workers can
run backups in parallel **across** hosts while serializing **per** host.
"""
from sqlmodel import Session, select
from app.celery_app import celery
from app.services.backup import (
    run_backup,
    backup_succeeded,
    backup_failure_message,
    _flush_job_progress_db,
    clear_job_progress_buffer,
)
from app.services.backup_audit import compact_backup_snippet, record_backup_audit_from_job
from app.services.server_job_lock import (
    try_acquire_server_lock,
    release_server_lock,
)
from app.database import engine
from app.models import Server, Job
from datetime import datetime
import json
import logging
import traceback

logger = logging.getLogger(__name__)

# Wait for another backup on the same server (multi-worker queue)
_LOCK_WAIT_COUNTDOWN_SEC = 20
# ~1h of waits before failing the job (20s * 180)
_LOCK_MAX_RETRIES = 180


@celery.task(name="app.tasks.nmap_scan", bind=True, max_retries=3, default_retry_delay=30)
def nmap_scan(
    self,
    run_id: int,
    job_id: int | None = None,
    vuln_scripts: bool = False,
    use_syn: bool | None = None,
):
    """LAN discovery scan — must run on celery-worker-nmap (-Q nmap).

    Web never invokes nmap; this task shells out and upserts devices.
    *use_syn* None = inherit integration Prefer SYN setting.
    """
    from app.services.nmap.scan import run_nmap_scan
    from app.services.nmap.runtime import touch_worker_heartbeat

    touch_worker_heartbeat(worker_id=str(self.request.hostname or "nmap"))
    db = Session(engine)
    try:
        if job_id:
            job = db.get(Job, job_id)
            if job and job.status == "cancelled":
                return {"status": "cancelled", "job_id": job_id, "run_id": run_id}
            if job:
                job.celery_task_id = self.request.id
                db.add(job)
                db.commit()
        return run_nmap_scan(
            db,
            run_id=run_id,
            job_id=job_id,
            use_syn=use_syn,
            vuln_scripts=vuln_scripts,
        )
    except Exception as e:
        logger.exception("nmap_scan task failed run_id=%s", run_id)
        if job_id:
            _update_job_status(job_id, "failed", {"error": str(e)[:500]})
        return {"status": "error", "message": str(e)[:500]}
    finally:
        db.close()


@celery.task(name="app.tasks.stale_data_cleanup", bind=True, max_retries=1)
def stale_data_cleanup(self, job_id: int | None = None, dry_run: bool = False):
    """Purge old Jobs / Audit / nmap runs per Settings (stream R)."""
    from app.services.stale_data_cleanup import run_stale_data_cleanup

    db = Session(engine)
    try:
        if job_id:
            job = db.get(Job, job_id)
            if job and job.status == "cancelled":
                return {"status": "cancelled", "job_id": job_id}
            if job:
                job.celery_task_id = self.request.id
                db.add(job)
                db.commit()
        return run_stale_data_cleanup(db, job_id=job_id, dry_run=bool(dry_run))
    except Exception as e:
        logger.exception("stale_data_cleanup failed")
        if job_id:
            _update_job_status(
                job_id,
                "failed",
                {"error": str(e)[:500], "current": "failed"},
            )
        return {"status": "error", "message": str(e)[:500]}
    finally:
        db.close()


@celery.task(
    name="app.tasks.nmap_vuln_db_update",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
)
def nmap_vuln_db_update(
    self,
    job_id: int | None = None,
    include_vulscan: bool = True,
    include_exploitdb: bool = True,
):
    """Download/refresh Vulners + vulscan + optional Exploit-DB index.

    Must run on celery-worker-nmap (-Q nmap). Web only enqueues.
    """
    from app.services.nmap.vuln_update import run_vuln_db_update
    from app.services.nmap.runtime import touch_worker_heartbeat

    touch_worker_heartbeat(worker_id=str(self.request.hostname or "nmap"))
    db = Session(engine)
    try:
        if job_id:
            job = db.get(Job, job_id)
            if job and job.status == "cancelled":
                return {"status": "cancelled", "job_id": job_id}
            if job:
                job.celery_task_id = self.request.id
                db.add(job)
                db.commit()
        return run_vuln_db_update(
            db,
            job_id=job_id,
            include_vulscan=bool(include_vulscan),
            include_exploitdb=bool(include_exploitdb),
        )
    except Exception as e:
        logger.exception("nmap_vuln_db_update failed")
        if job_id:
            _update_job_status(
                job_id,
                "failed",
                {"error": str(e)[:500], "current": "failed", "log_lines": [str(e)[:200]]},
            )
        return {"status": "error", "message": str(e)[:500]}
    finally:
        db.close()


@celery.task(bind=True, max_retries=_LOCK_MAX_RETRIES, default_retry_delay=30)
def backup_server(self, server_id: int, job_id: int | None = None, audit_id: int | None = None, source_filter: str | None = None):
    """
    Celery background task.
    Worker writes rich status into Job + append-only AuditLog events.

    Acquires a per-server backup mutex before rsync so concurrent workers
    never run two backups against the same host at once.
    """
    db = Session(engine)
    server = None
    lock_token: str | None = None
    work_started = False

    try:
        server = db.exec(select(Server).where(Server.id == server_id)).first()
        if not server:
            logger.error(f"Server {server_id} not found")
            if job_id:
                _update_job_status(job_id, "failed", {"error": "Server not found"})
            return {"status": "error", "message": "Server not found"}

        if job_id:
            job = db.get(Job, job_id)
            if not job or job.status not in ("pending", "running"):
                logger.info(f"[Celery] Job {job_id} no longer active (status={getattr(job, 'status', None)}), skipping")
                return {"status": "skipped", "job_id": job_id}

        holder = str(job_id or self.request.id or f"task-{server_id}")
        lock_token = try_acquire_server_lock("backup", server_id, holder=holder)
        if not lock_token:
            # Another worker holds this host — wait and retry (same celery task id for cancel)
            if job_id:
                job = db.get(Job, job_id)
                if not job or job.status not in ("pending", "running"):
                    return {"status": "skipped", "job_id": job_id}
                if job.status == "cancelled":
                    return {"status": "cancelled", "job_id": job_id, "server_id": server_id}
            if self.request.retries >= _LOCK_MAX_RETRIES:
                msg = "Timed out waiting for another backup on this server to finish"
                logger.error(f"[Celery] {msg} (server={server_id} job={job_id})")
                if job_id:
                    _update_job_status(
                        job_id,
                        "failed",
                        {"error": msg, "current": "failed", "log_lines": [msg]},
                    )
                return {"status": "failed", "job_id": job_id, "server_id": server_id, "error": msg}
            if job_id:
                _update_job_status(
                    job_id,
                    "pending",
                    {
                        "current": "waiting_for_server",
                        "log_lines": [
                            "Waiting for another backup on this server to finish…",
                        ],
                    },
                )
            logger.info(
                f"[Celery] Server {server_id} backup lock busy — retry in {_LOCK_WAIT_COUNTDOWN_SEC}s "
                f"(attempt {self.request.retries + 1}/{_LOCK_MAX_RETRIES})"
            )
            raise self.retry(countdown=_LOCK_WAIT_COUNTDOWN_SEC)

        if job_id:
            initial = {
                "current": "starting",
                "source_filter": source_filter,
                "started_at": datetime.utcnow().isoformat(),
            }
            _update_job_status(job_id, "running", initial)
            # _update_job_status uses its own Session — expire identity map
            # so subsequent db.get() sees committed status from the worker DB.
            db.expire_all()
            job = db.get(Job, job_id)
            if job:
                src = source_filter or "all sources"
                record_backup_audit_from_job(
                    db, job, "running", message=f"Backup in progress for {src}"
                )
                db.commit()

        work_started = True
        sources_override = None
        if source_filter:
            try:
                all_sources = server.get_backup_sources()
                filtered = [s for s in all_sources if s.get("source") == source_filter]
                if filtered:
                    sources_override = filtered
            except Exception as e:
                logger.warning(f"source_filter error: {e}")

        result = run_backup(server, sources_override=sources_override, job_id=job_id)

        # User may have cancelled while rsync ran
        if job_id:
            db.expire_all()
            job_now = db.get(Job, job_id)
            if job_now and job_now.status == "cancelled":
                clear_job_progress_buffer(job_id)
                logger.info(f"[Celery] Backup job {job_id} cancelled by user — not recording success/fail")
                return {"status": "cancelled", "job_id": job_id, "server_id": server_id}

        summary = result if isinstance(result, dict) else {"raw": str(result)}
        ok = backup_succeeded(summary) if isinstance(summary, dict) else False
        if job_id:
            _flush_job_progress_db(job_id, force=True)
            if ok:
                final = {"current": "completed", "result_summary": summary}
            else:
                err = backup_failure_message(summary)
                final = {
                    "current": "failed",
                    "result_summary": summary,
                    "error": err,
                    "log_lines": [f"Backup failed: {err[:240]}"],
                }
            _update_job_status(job_id, "success" if ok else "failed", final)
            clear_job_progress_buffer(job_id)
            # Critical: without expire_all, Session still holds pre-update Job
            # (status pending/running) and success audit was skipped entirely.
            db.expire_all()
            job = db.get(Job, job_id)
            if job and job.status in ("success", "failed"):
                phase = "success" if ok else "failed"
                snippet = compact_backup_snippet(summary, ok=ok)
                if not ok and "error" not in snippet:
                    snippet["error"] = backup_failure_message(summary)
                try:
                    record_backup_audit_from_job(
                        db,
                        job,
                        phase,
                        message=backup_failure_message(summary) if not ok else None,
                        output_snippet=snippet,
                    )
                    db.commit()
                except Exception as audit_exc:
                    logger.error(
                        f"Failed to record backup {phase} audit for job {job_id}: {audit_exc}"
                    )
                    try:
                        db.rollback()
                    except Exception:
                        pass
            elif job:
                logger.warning(
                    f"[Celery] Job {job_id} status={job.status} after finish — "
                    f"skipped terminal backup audit (ok={ok})"
                )

        if ok:
            try:
                server.last_backup_at = datetime.utcnow()
                db.add(server)
                db.commit()
            except Exception:
                pass
            try:
                from .services.notifications import resolve_backup_failed
                resolve_backup_failed(db, server_id)
            except Exception:
                pass
        else:
            try:
                from .services.notifications import notify_backup_failed
                # Do not re-import backup_failure_message here — a local import makes the
                # name local to the whole function and breaks uses above (UnboundLocalError).
                msg = backup_failure_message(summary) if isinstance(summary, dict) else str(summary)
                notify_backup_failed(db, server_id, server.name if server else str(server_id), msg)
            except Exception:
                pass

        logger.info(f"[Celery] Backup {'completed' if ok else 'failed'} for server {server_id}")
        return {"status": "success" if ok else "failed", "server_id": server_id, "result": result}

    except Exception as exc:
        # Celery Retry is not a failure — re-raise so the broker requeues
        from celery.exceptions import Retry

        if isinstance(exc, Retry):
            raise

        logger.error(f"Backup failed for server {server_id}: {exc}\n{traceback.format_exc()}")

        # Cancelled jobs: rsync terminate often surfaces as an exception
        if job_id:
            try:
                job_now = db.get(Job, job_id)
                if job_now and job_now.status == "cancelled":
                    clear_job_progress_buffer(job_id)
                    logger.info(f"[Celery] Backup job {job_id} already cancelled — ignoring worker error")
                    return {"status": "cancelled", "job_id": job_id, "server_id": server_id}
            except Exception:
                pass

        error_str = str(exc).lower()
        is_transient = any(x in error_str for x in ("connection", "timeout", "refused", "reset", "closed"))

        if job_id:
            _flush_job_progress_db(job_id, force=True)
            err = str(exc)[:800]
            _update_job_status(job_id, "failed", {
                "error": err,
                "current": "failed",
                "log_lines": [f"Backup failed: {err[:240]}"],
            })
            clear_job_progress_buffer(job_id)
            try:
                with Session(engine) as s:
                    job = s.get(Job, job_id)
                    if job and job.status == "failed":
                        try:
                            existing = json.loads(job.details or "{}")
                        except Exception:
                            existing = {}
                        if not existing.get("audit_failed_recorded"):
                            record_backup_audit_from_job(
                                s,
                                job,
                                "failed",
                                message=err,
                                output_snippet={"error": err},
                            )
                            existing["audit_failed_recorded"] = True
                            job.details = json.dumps(existing)
                            s.add(job)
                            s.commit()
            except Exception as audit_exc:
                logger.error(f"Failed to record backup failed audit for job {job_id}: {audit_exc}")

        if is_transient and work_started:
            # One extra attempt after work started (do not burn lock-wait budget)
            logger.warning(f"Transient error on server {server_id} - retrying once")
            # Release lock before retry so another worker is not blocked forever
            if lock_token:
                release_server_lock("backup", server_id, lock_token)
                lock_token = None
            raise self.retry(exc=exc, countdown=30, max_retries=self.request.retries + 1)
        else:
            logger.info(f"Permanent error on server {server_id} - not retrying")

    finally:
        if lock_token:
            try:
                release_server_lock("backup", server_id, lock_token)
            except Exception as e:
                logger.warning(f"[Celery] Failed to release backup lock server={server_id}: {e}")
        db.close()


def _update_job_status(job_id: int, status: str, extra: dict):
    """Update Job status + merge details JSON (worker feeds DB)."""
    try:
        with Session(engine) as s:
            job = s.get(Job, job_id)
            if job:
                # Do not clobber a user cancel (or other terminal state) from the worker
                if job.status == "cancelled":
                    return
                if (
                    job.status in ("success", "failed")
                    and job.finished_at
                    and status in ("running", "pending")
                ):
                    return
                # Waiting for lock: stay pending, only merge details
                if status == "pending" and job.status == "pending" and extra:
                    existing = {}
                    try:
                        if job.details:
                            existing = json.loads(job.details)
                    except Exception:
                        pass
                    # Merge log_lines carefully
                    new_lines = extra.pop("log_lines", None)
                    existing.update(extra)
                    if new_lines:
                        lines = list(existing.get("log_lines") or [])
                        for line in new_lines:
                            if not lines or lines[-1] != line:
                                lines.append(line)
                        existing["log_lines"] = lines[-15:]
                    job.details = json.dumps(existing)
                    s.add(job)
                    s.commit()
                    return
                job.status = status
                if status == "running" and job.started_at is None:
                    job.started_at = datetime.utcnow()
                if extra:
                    existing = {}
                    try:
                        if job.details:
                            existing = json.loads(job.details)
                    except Exception:
                        pass
                    existing.update(extra)
                    job.details = json.dumps(existing)
                if status in ("success", "failed", "cancelled"):
                    job.finished_at = datetime.utcnow()
                s.add(job)
                s.commit()
    except Exception as e:
        logger.error(f"Failed to update job {job_id} status={status}: {e}")

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
    try_acquire_dual_server_lock,
    release_server_lock,
    release_dual_server_lock,
)
from app.database import engine
from app.models import Server, Job
from datetime import datetime
import json
import logging
import traceback

logger = logging.getLogger(__name__)


def _remember_worker(job, request) -> bool:
    """Record the Celery nodename on a job the caller will commit."""
    from app.services.job_worker import remember_celery_worker

    if job is None:
        return False
    return remember_celery_worker(job, request)

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
    script_preset: str | None = None,
    timing: int | None = None,
    top_ports: int | None = None,
    include_udp: bool = False,
    port_list: str | None = None,
    port_mode: str | None = None,
):
    """LAN discovery scan — must run on celery-worker-nmap (-Q nmap).

    Web never invokes nmap; this task shells out and upserts devices.
    *use_syn* None = inherit integration Prefer SYN setting.
    Curated options: script_preset, timing, top_ports, include_udp, port_list, port_mode.
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
                _remember_worker(job, self.request)
                db.add(job)
                db.commit()
        return run_nmap_scan(
            db,
            run_id=run_id,
            job_id=job_id,
            use_syn=use_syn,
            vuln_scripts=vuln_scripts,
            script_preset=script_preset,
            timing=timing,
            top_ports=top_ports,
            include_udp=include_udp,
            port_list=port_list,
            port_mode=port_mode,
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
                _remember_worker(job, self.request)
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
                _remember_worker(job, self.request)
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
            if _remember_worker(job, self.request):
                db.add(job)
                db.commit()

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
        if ok and server is not None:
            try:
                from app.services.backup_replicate import enqueue_after_host_backup
                enqueue_after_host_backup(db, server)
            except Exception as copy_exc:
                logger.warning(
                    "[Celery] Drive copy follow-up skipped for server %s: %s",
                    server_id,
                    copy_exc,
                )
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


@celery.task(
    name="app.tasks.service_migrate",
    bind=True,
    max_retries=_LOCK_MAX_RETRIES,
    default_retry_delay=30,
)
def service_migrate(
    self,
    job_id: int,
    source_id: int,
    dest_id: int,
    project: str,
    audit_id: int,
):
    """Move a compose project host→host. Same default queue as backups.

    Holds the **backup** Redis mutex on **both** hosts (lower id first) so a
    backup cannot rsync a host while Move is copying. Recycle **web** is safe.
    Recycle **worker** after the job is ``running`` fails it (do not restart
    a half-copied pipeline). Staging is kept.
    """
    from celery.exceptions import Retry

    from app.services.jobs_migrate import (
        _execute_service_migrate,
        fail_migrate_worker_restart,
    )

    db = Session(engine)
    lock_tokens: tuple[str, str] | None = None
    try:
        job = db.get(Job, job_id)
        if not job:
            return {"status": "skipped", "job_id": job_id}
        if job.status == "cancelled":
            return {"status": "cancelled", "job_id": job_id}
        if job.status not in ("pending", "running"):
            logger.info(
                "[Celery] migrate job %s no longer active (status=%s), skipping",
                job_id,
                job.status,
            )
            return {"status": "skipped", "job_id": job_id}
        if job.status == "running":
            # Redelivery after worker death mid-copy — do not re-run the pipeline.
            db.close()
            db = None
            fail_migrate_worker_restart(job_id, audit_id)
            return {"status": "failed", "job_id": job_id, "reason": "worker_restart"}

        job.celery_task_id = self.request.id
        _remember_worker(job, self.request)
        db.add(job)
        db.commit()

        holder = str(job_id or self.request.id or f"migrate-{source_id}-{dest_id}")
        lock_tokens = try_acquire_dual_server_lock(
            "backup", source_id, dest_id, holder=holder
        )
        if not lock_tokens:
            if self.request.retries >= _LOCK_MAX_RETRIES:
                msg = "Timed out waiting for a backup or Move on the source or destination host"
                logger.error("[Celery] %s (job=%s src=%s dest=%s)", msg, job_id, source_id, dest_id)
                _update_job_status(
                    job_id,
                    "failed",
                    {"error": msg, "current": "failed", "log_lines": [msg]},
                )
                return {
                    "status": "failed",
                    "job_id": job_id,
                    "error": msg,
                }
            _update_job_status(
                job_id,
                "pending",
                {
                    "current": "waiting_for_server",
                    "log_lines": [
                        "Waiting for a backup or Move on the source or destination host…",
                    ],
                },
            )
            logger.info(
                "[Celery] migrate job %s dual backup lock busy — retry in %ss "
                "(attempt %s/%s)",
                job_id,
                _LOCK_WAIT_COUNTDOWN_SEC,
                self.request.retries + 1,
                _LOCK_MAX_RETRIES,
            )
            raise self.retry(countdown=_LOCK_WAIT_COUNTDOWN_SEC)

        _execute_service_migrate(
            job_id,
            source_id,
            dest_id,
            project,
            audit_id,
        )
        return {"status": "ok", "job_id": job_id}
    except Exception as exc:
        if isinstance(exc, Retry):
            raise
        logger.exception("service_migrate celery task failed job=%s", job_id)
        err = str(exc)[:800]
        try:
            from app.services.jobs.service import _finish, _load_server_for_job

            _src, hostname = _load_server_for_job(source_id)
            _finish(
                audit_id,
                job_id,
                "failed",
                err,
                hostname,
                "service_migrate",
            )
        except Exception:
            logger.exception("service_migrate fail-close")
            _update_job_status(
                job_id,
                "failed",
                {
                    "error": err,
                    "current": "failed",
                    "log_lines": [err[:240]],
                },
            )
        return {"status": "error", "job_id": job_id, "error": err[:500]}
    finally:
        if lock_tokens:
            try:
                release_dual_server_lock(
                    "backup",
                    source_id,
                    lock_tokens[0],
                    dest_id,
                    lock_tokens[1],
                )
            except Exception as e:
                logger.warning(
                    "[Celery] Failed to release migrate dual lock job=%s: %s",
                    job_id,
                    e,
                )
        if db is not None:
            db.close()


def _migrate_dual_lock_task(
    self,
    job_id: int,
    source_id: int,
    dest_id: int,
    audit_id: int,
    *,
    job_type: str,
    execute,
    running_message: str,
    log_name: str,
):
    """Shared Celery body for undo and dest-up recover. Never dest down -v."""
    from celery.exceptions import Retry

    db = Session(engine)
    lock_tokens: tuple[str, str] | None = None
    try:
        job = db.get(Job, job_id)
        if not job:
            return {"status": "skipped", "job_id": job_id}
        if job.status == "cancelled":
            return {"status": "cancelled", "job_id": job_id}
        if job.status not in ("pending", "running"):
            return {"status": "skipped", "job_id": job_id, "reason": job.status}
        if job.status == "running":
            db.close()
            db = None
            from app.services.jobs.service import _finish, _load_server_for_job

            _src, hostname = _load_server_for_job(source_id)
            _finish(audit_id, job_id, "failed", running_message, hostname, job_type)
            return {"status": "failed", "job_id": job_id, "reason": "worker_restart"}

        job.celery_task_id = self.request.id
        _remember_worker(job, self.request)
        db.add(job)
        db.commit()
        holder = str(job_id or self.request.id or f"{job_type}-{source_id}-{dest_id}")
        lock_tokens = try_acquire_dual_server_lock(
            "backup", source_id, dest_id, holder=holder
        )
        if not lock_tokens:
            if self.request.retries >= _LOCK_MAX_RETRIES:
                msg = "Timed out waiting for a backup or Move on the source or destination host"
                _update_job_status(
                    job_id,
                    "failed",
                    {"error": msg, "current": "failed", "log_lines": [msg]},
                )
                return {"status": "failed", "job_id": job_id, "error": msg}
            _update_job_status(
                job_id,
                "pending",
                {
                    "current": "waiting_for_server",
                    "log_lines": ["Waiting for a backup or Move on the source or destination host…"],
                },
            )
            raise self.retry(countdown=_LOCK_WAIT_COUNTDOWN_SEC)
        execute(job_id, source_id, dest_id, audit_id)
        return {"status": "ok", "job_id": job_id}
    except Exception as exc:
        if isinstance(exc, Retry):
            raise
        logger.exception("%s celery task failed job=%s", log_name, job_id)
        err = str(exc)[:800]
        try:
            from app.services.jobs.service import _finish, _load_server_for_job

            _src, hostname = _load_server_for_job(source_id)
            _finish(audit_id, job_id, "failed", err, hostname, job_type)
        except Exception:
            logger.exception("%s fail-close", log_name)
            _update_job_status(
                job_id,
                "failed",
                {"error": err, "current": "failed", "log_lines": [err[:240]]},
            )
        return {"status": "error", "job_id": job_id, "error": err[:500]}
    finally:
        if lock_tokens:
            try:
                release_dual_server_lock(
                    "backup", source_id, lock_tokens[0], dest_id, lock_tokens[1]
                )
            except Exception as e:
                logger.warning(
                    "[Celery] Failed to release %s dual lock job=%s: %s",
                    log_name,
                    job_id,
                    e,
                )
        if db is not None:
            db.close()


@celery.task(
    name="app.tasks.service_migrate_undo",
    bind=True,
    max_retries=_LOCK_MAX_RETRIES,
    default_retry_delay=30,
)
def service_migrate_undo(self, job_id: int, source_id: int, dest_id: int, audit_id: int):
    """Fail-path undo of a Move. Same dual-host backup mutex. Never dest down -v."""
    from app.services.jobs_migrate import _execute_service_migrate_undo

    return _migrate_dual_lock_task(
        self,
        job_id,
        source_id,
        dest_id,
        audit_id,
        job_type="service_migrate_undo",
        execute=_execute_service_migrate_undo,
        running_message=(
            "Worker restarted during Undo. Dest directory was left in place. "
            "Inspect both hosts before retrying."
        ),
        log_name="service_migrate_undo",
    )


@celery.task(
    name="app.tasks.service_migrate_dest_recover",
    bind=True,
    max_retries=_LOCK_MAX_RETRIES,
    default_retry_delay=30,
)
def service_migrate_dest_recover(
    self, job_id: int, source_id: int, dest_id: int, audit_id: int
):
    """Stop dest and start source after a dest_up worker death. No DNS revert."""
    from app.services.jobs_migrate import _execute_dest_up_recover

    return _migrate_dual_lock_task(
        self,
        job_id,
        source_id,
        dest_id,
        audit_id,
        job_type="service_migrate_dest_recover",
        execute=_execute_dest_up_recover,
        running_message=(
            "Worker restarted while stopping dest or starting source. "
            "Inspect both hosts before trying again. Names were not changed."
        ),
        log_name="service_migrate_dest_recover",
    )


@celery.task(
    name="app.tasks.exclusive_job",
    bind=True,
    max_retries=3000,
    default_retry_delay=30,
)
def exclusive_job(
    self,
    job_id: int,
    server_id: int,
    audit_id: int,
    job_type: str,
    payload: dict | None = None,
):
    """Patch, checks, stack, template, and host-facts jobs on the default queue.

    Does not take the backup / Move mutex. Host-down stays pending and
    redelivers. A redelivery that finds the job already running fails it.
    """
    from celery.exceptions import Retry

    from app.services.jobs_exclusive import run_exclusive_job

    try:
        return run_exclusive_job(
            self, job_id, server_id, audit_id, job_type, payload or {}
        )
    except Retry:
        raise


# Same 2h cap as host jobs, and under the 3h Redis visibility window, so a
# still-running archive is not started twice. Ack stays late: a dead worker
# redelivers, and the body fails a row that was already running.
_HOUSEKEEPING_TIME_LIMIT = 7200


@celery.task(
    name="app.tasks.housekeeping_job",
    bind=True,
    acks_late=True,
    time_limit=_HOUSEKEEPING_TIME_LIMIT,
)
def housekeeping_job(
    self,
    job_id: int,
    server_id: int,
    audit_id: int,
    job_type: str,
):
    """retention and herder_backup on the default queue.

    Not a host exclusive slot. Does not retry the work. A redelivery that
    finds the job already running fails it.
    """
    from app.services.jobs import run_housekeeping_job

    return run_housekeeping_job(
        self, job_id, int(server_id or 0), audit_id, job_type or ""
    )


def _housekeeping_failure_message(exception: BaseException | None) -> str:
    name = type(exception).__name__ if exception else ""
    if name in ("TimeLimitExceeded", "HardTimeLimitExceeded") or "time limit" in str(
        exception or ""
    ).lower():
        return "Worker hit the time limit while this job was running. It was not resumed."
    return "Worker restarted while this job was running. It was not resumed."


def _on_housekeeping_task_failure(
    sender=None,
    exception=None,
    args=None,
    **kwargs,
) -> None:
    """The hard time limit never reaches the task body."""
    if getattr(sender, "name", "") != "app.tasks.housekeeping_job":
        return
    if not args or len(args) < 4:
        return
    try:
        job_id = int(args[0])
        audit_id = int(args[2])
        job_type = str(args[3] or "")
    except (TypeError, ValueError):
        return
    from app.services.jobs import fail_housekeeping_from_signal

    fail_housekeeping_from_signal(
        job_id, audit_id, job_type, _housekeeping_failure_message(exception)
    )


try:
    from celery.signals import task_failure

    task_failure.connect(_on_housekeeping_task_failure)
except Exception:
    logger.debug("Housekeeping failure signal not connected", exc_info=True)


# Host rsync stays on the 2h global limit. A Drive copy can run much longer.
# Ack on receive so Redis does not start a second rclone while the first is still going.
# 7 days is the safety stop; the failure signal below records it on the job row.
_DRIVE_COPY_TIME_LIMIT = 7 * 24 * 3600


@celery.task(
    bind=True,
    name="app.tasks.replicate_backup",
    acks_late=False,
    time_limit=_DRIVE_COPY_TIME_LIMIT,
)
def replicate_backup(self, job_id: int):
    """Copy checked /backups paths to a fleet destination. Default Celery queue."""
    db = Session(engine)
    try:
        job = db.get(Job, job_id)
        if not job or job.status not in ("pending", "running"):
            return {"status": "skipped", "job_id": job_id}
        if _remember_worker(job, self.request):
            db.add(job)
            db.commit()
        try:
            details = json.loads(job.details or "{}")
        except Exception:
            details = {}
        from app.models import BackupDestination
        from app.services.backup_replicate import execute

        dest = db.get(BackupDestination, details.get("destination_id"))
        _update_job_status(job_id, "running", {"current": "copying"})
        if not dest:
            _update_job_status(job_id, "failed", {"error": "Destination missing", "current": "failed"})
            return {"status": "failed", "job_id": job_id}
        result = execute(dest, details.get("scope"))
        if result.get("ok"):
            _update_job_status(
                job_id,
                "success",
                {"current": "completed", "copied": result.get("copied") or []},
            )
            return {"status": "success", "job_id": job_id}
        _update_job_status(
            job_id,
            "failed",
            {"error": result.get("error") or "copy failed", "current": "failed"},
        )
        return {"status": "failed", "job_id": job_id, "error": result.get("error")}
    except Exception as exc:
        logger.error("Backup copy job %s failed: %s", job_id, exc)
        _update_job_status(job_id, "failed", {"error": str(exc)[:500], "current": "failed"})
        return {"status": "failed", "job_id": job_id, "error": str(exc)[:500]}
    finally:
        db.close()


def fail_replicate_job(job_id: int, message: str) -> bool:
    """Mark a Drive copy failed when the worker process dies outside execute()."""
    text = (message or "Backup copy stopped").strip()[:500]
    try:
        with Session(engine) as s:
            job = s.get(Job, job_id)
            if not job or job.job_type != "backup_replicate":
                return False
            if job.status in ("success", "failed", "cancelled"):
                return False
            existing: dict = {}
            try:
                if job.details:
                    existing = json.loads(job.details)
            except Exception:
                existing = {}
            lines = list(existing.get("log_lines") or [])
            if not lines or lines[-1] != text:
                lines.append(text)
            existing["log_lines"] = lines[-15:]
            existing["error"] = text
            existing["current"] = "failed"
            existing["done"] = True
            job.details = json.dumps(existing)
            job.status = "failed"
            job.finished_at = datetime.utcnow()
            s.add(job)
            s.commit()
            return True
    except Exception as exc:
        logger.error("Backup copy job %s could not be marked failed: %s", job_id, exc)
        return False


def _drive_copy_failure_message(exception: BaseException | None) -> str:
    name = type(exception).__name__ if exception else ""
    if name in ("TimeLimitExceeded", "HardTimeLimitExceeded") or "time limit" in str(exception or "").lower():
        return (
            "Backup copy hit the worker time limit and was stopped. "
            "Start it again. Files already copied stay."
        )
    return (
        "The worker stopped during the backup copy. "
        "Start it again. Files already copied stay."
    )


def _on_drive_copy_task_failure(
    sender=None,
    exception=None,
    args=None,
    **kwargs,
) -> None:
    """SIGKILL and the hard time limit never reach the task's own except."""
    if getattr(sender, "name", "") != "app.tasks.replicate_backup":
        return
    if not args:
        return
    try:
        job_id = int(args[0])
    except (TypeError, ValueError):
        return
    fail_replicate_job(job_id, _drive_copy_failure_message(exception))


try:
    from celery.signals import task_failure

    task_failure.connect(_on_drive_copy_task_failure)
except Exception:
    logger.debug("Drive copy failure signal not connected", exc_info=True)


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

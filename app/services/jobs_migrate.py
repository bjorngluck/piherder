"""Move job enqueue / execute / worker-restart fail (Celery-owned).

Lives beside ``jobs.service`` so the jobs god-file does not grow more migrate
orchestration. ``jobs.service`` re-exports these names for historical patches.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

from fastapi import BackgroundTasks

from ..models import Job, Server
from .server_job_lock import (
    release_dual_server_lock,
    try_acquire_dual_server_lock,
)

logger = logging.getLogger(__name__)


def _jobs():
    from . import jobs as js

    return js


def _migrate_run_inline() -> bool:
    """Unit tests have no Celery worker; run the pipeline in-process.

    Production always enqueues ``app.tasks.service_migrate``. Override with
    ``PIHERDER_MIGRATE_INLINE=1`` only for local debugging.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    flag = (os.environ.get("PIHERDER_MIGRATE_INLINE") or "").strip().lower()
    return flag in ("1", "true", "yes")


def enqueue_service_migrate(
    source_id: int,
    dest_id: int,
    project: str,
    *,
    user_id: int | None = None,
    background_tasks: BackgroundTasks | None = None,
    leftover: str = "stopped",
    devices_ack: bool = False,
    adopt_fabric: bool = False,
    dest_project: str | None = None,
    port_map: dict | None = None,
    bind_map: dict | None = None,
    skip_binds: list | None = None,
) -> Job:
    """Queue stop-first copy + dest up on Celery. Raises JobAlreadyActive if either host is busy.

    ``background_tasks`` is ignored (kept so callers do not break). Under pytest
    the pipeline runs inline so unit tests need no worker.
    """
    js = _jobs()
    from .service_migrate.host_lock import HostLockError, compose_project_name
    from .service_migrate.overrides import normalize_dest_project, validate_port_map

    try:
        name = compose_project_name(project)
    except HostLockError as e:
        raise ValueError(e.message) from e
    dest_name, dest_err = normalize_dest_project(name, dest_project)
    if dest_err:
        raise ValueError(dest_err)
    clean_map, map_errs = validate_port_map(port_map)
    if map_errs:
        raise ValueError("; ".join(map_errs[:4]))
    if int(source_id) == int(dest_id):
        raise ValueError("destination must differ from source")
    with js._get_fresh_session() as session:
        source = session.get(Server, source_id)
        dest = session.get(Server, dest_id)
        if not source or not dest:
            raise ValueError("server not found")
        for sid in (source_id, dest_id):
            active = js._active_stack_mutating_job(session, sid)
            if not active:
                active = js._active_migrate_as_dest(session, sid)
            if not active:
                backups = js.get_active_backup_jobs(session, sid)
                active = backups[0] if backups else None
            if active:
                session.expunge(active)
                raise js.JobAlreadyActive(active)
        from .service_migrate.leftover import normalize_leftover

        left = normalize_leftover(leftover)
        job, audit = js._create_queued_job_with_audit(
            session,
            server_id=source.id,
            job_type="service_migrate",
            queue_message=f"Migrate {name} queued → {dest.name}…",
            user_id=user_id,
            audit_details=f"Job #{{job_id}} · migrate {name} → {dest.name}",
            dest_server_id=dest.id,
            dest_name=dest.name,
            project=name,
            dest_project=dest_name,
            port_map=clean_map,
            bind_map=bind_map or {},
            skip_binds=list(skip_binds or []),
            leftover=left,
            devices_ack=bool(devices_ack),
            adopt_fabric=bool(adopt_fabric),
        )
        jid, aid = job.id, audit.id
    if js._migrate_run_inline():
        _run_migrate_holding_locks(jid, source_id, dest_id, name, aid)
    elif not js.HAS_CELERY or not js.service_migrate_task:
        msg = "Celery worker required for Move — start celery-worker container"
        with js._get_fresh_session() as session:
            job = session.get(Job, jid)
            if job:
                js._mark_job_terminal(job, msg, session, status="failed", record_audit=True)
                session.commit()
        raise RuntimeError(msg)
    else:
        try:
            async_result = js.service_migrate_task.delay(
                jid, source_id, dest_id, name, aid
            )
            with js._get_fresh_session() as session:
                job = session.get(Job, jid)
                if job:
                    job.celery_task_id = async_result.id
                    session.add(job)
                    session.commit()
        except Exception as e:
            msg = f"Failed to enqueue Move to Celery: {e}"
            logger.exception("[Jobs] %s", msg)
            with js._get_fresh_session() as session:
                job = session.get(Job, jid)
                if job:
                    js._mark_job_terminal(
                        job, msg, session, status="failed", record_audit=True
                    )
                    session.commit()
            raise RuntimeError(msg) from e
    with js._get_fresh_session() as session:
        job = session.get(Job, jid)
        if job:
            session.expunge(job)
        return job


def fail_migrate_worker_restart(job_id: int, audit_id: int) -> None:
    """Mark a running Move failed after Celery redelivery (worker recycle).

    Does not re-run the pipeline. Staging under ``BACKUP_ROOT/_migrate/{job_id}``
    is kept. **Start source stack** only when ``migrate_step`` is stop/copy/dest_up.
    """
    from .service_migrate.pipeline import RECOVER_SOURCE_STEPS

    js = _jobs()
    hostname = ""
    with js._get_fresh_session() as session:
        job = session.get(Job, job_id)
        if not job or job.status in ("success", "failed", "cancelled"):
            return
        source = session.get(Server, job.server_id) if job.server_id else None
        hostname = (source.hostname if source else "") or ""
        project = ""
        step = ""
        try:
            data = json.loads(job.details or "{}") or {}
            project = str(data.get("project") or "")
            step = str(data.get("migrate_step") or data.get("failed_step") or "")
        except Exception:
            data = {}
        if source and project and step in RECOVER_SOURCE_STEPS:
            try:
                from .service_migrate.leftover import jailed_source_project_path

                path = jailed_source_project_path(source, project)
                js._merge_job_details(
                    job,
                    failed_step="worker_restart",
                    recover_source={
                        "server_id": int(source.id),
                        "project": project,
                        "project_path": path,
                    },
                    log_line="Worker restarted — Move is no longer running. Staging kept.",
                )
                session.add(job)
                session.commit()
            except Exception:
                logger.exception("migrate worker-restart recover_source")
        else:
            try:
                js._merge_job_details(
                    job,
                    failed_step="worker_restart",
                    log_line="Worker restarted — Move is no longer running. Staging kept.",
                )
                session.add(job)
                session.commit()
            except Exception:
                pass
    js._finish(
        audit_id,
        job_id,
        "failed",
        "Worker restarted — Move was no longer running. Staging kept.",
        hostname,
        "service_migrate",
    )


def _run_migrate_holding_locks(
    job_id: int,
    source_id: int,
    dest_id: int,
    project: str,
    audit_id: int,
) -> None:
    """Inline/pytest path: take the dual-host backup mutex, then run the pipeline."""
    js = _jobs()
    lock_tokens = try_acquire_dual_server_lock(
        "backup", source_id, dest_id, holder=str(job_id)
    )
    if not lock_tokens:
        source, hostname = js._load_server_for_job(source_id)
        js._finish(
            audit_id,
            job_id,
            "failed",
            "A backup or Move is already using the source or destination host",
            hostname,
            "service_migrate",
        )
        return
    try:
        _execute_service_migrate(job_id, source_id, dest_id, project, audit_id)
    finally:
        release_dual_server_lock(
            "backup",
            source_id,
            lock_tokens[0],
            dest_id,
            lock_tokens[1],
        )


def _execute_service_migrate(
    job_id: int,
    source_id: int,
    dest_id: int,
    project: str,
    audit_id: int,
) -> None:
    """Run the pipeline. Caller already holds (or skipped) the dual-host lock."""
    js = _jobs()
    source, hostname = js._load_server_for_job(source_id)
    dest, _ = js._load_server_for_job(dest_id)
    _run_service_migrate_pipeline(
        job_id,
        source_id,
        dest_id,
        project,
        audit_id,
        source=source,
        dest=dest,
        hostname=hostname,
    )


def _run_service_migrate_pipeline(
    job_id: int,
    source_id: int,
    dest_id: int,
    project: str,
    audit_id: int,
    *,
    source,
    dest,
    hostname: str,
) -> None:
    js = _jobs()
    from .service_migrate.facts import herder_free_bytes, probe_host_facts, refresh_host_inventory
    from .service_migrate.leftover import normalize_leftover
    from .service_migrate.pipeline import (
        RECOVER_SOURCE_STEPS,
        MigrateError,
        run_copy_and_start,
        wipe_staging,
    )

    leftover = "stopped"
    devices_ack = False
    adopt_fabric = False
    dest_project = project
    port_map: dict = {}
    bind_map: dict = {}
    skip_binds: list = []
    with js._get_fresh_session() as session:
        job = session.get(Job, job_id)
        if job:
            try:
                data = json.loads(job.details or "{}") or {}
                leftover = normalize_leftover(data.get("leftover"))
                devices_ack = bool(data.get("devices_ack"))
                adopt_fabric = bool(data.get("adopt_fabric"))
                dest_project = data.get("dest_project") or project
                raw_map = data.get("port_map") or {}
                if isinstance(raw_map, dict):
                    port_map = {str(k): str(v) for k, v in raw_map.items()}
                raw_binds = data.get("bind_map") or {}
                if isinstance(raw_binds, dict):
                    bind_map = {str(k): str(v) for k, v in raw_binds.items()}
                raw_skip = data.get("skip_binds") or []
                if isinstance(raw_skip, list):
                    skip_binds = [str(x) for x in raw_skip if str(x).strip()]
            except Exception:
                pass
            job.status = "running"
            job.started_at = datetime.utcnow()
            js._merge_job_details(
                job,
                current="migrating",
                log_line=f"Migrating {project}…",
                done=False,
            )
            session.add(job)
            session.commit()
    if not source or not dest:
        js._finish(audit_id, job_id, "failed", "Server not found", hostname, "service_migrate")
        return

    def log_line(msg: str) -> None:
        js._flush_job_progress(job_id, "migrating", msg, default_current="migrating")

    def on_step(step: str) -> None:
        js._flush_job_progress(
            job_id,
            step,
            f"Step: {step}",
            default_current=step,
            migrate_step=step,
        )

    try:
        with js._get_fresh_session() as session:
            src = session.get(Server, source_id)
            dst = session.get(Server, dest_id)
            if not src or not dst:
                raise MigrateError("Server not found")
            log_line("Refreshing dest Docker inventory from the host…")
            try:
                refresh_host_inventory(dst.id)
                session.refresh(dst)
            except Exception:
                pass
            result = run_copy_and_start(
                session,
                source=src,
                dest=dst,
                project=project,
                job_id=job_id,
                source_facts=probe_host_facts(src),
                dest_facts=probe_host_facts(dst),
                herder_free=herder_free_bytes(),
                log=log_line,
                leftover=leftover,
                devices_ack=devices_ack,
                adopt_fabric=adopt_fabric,
                dest_project=dest_project,
                port_map=port_map,
                bind_map=bind_map,
                skip_binds=skip_binds,
                live_inspect=True,
                on_step=on_step,
            )
        wipe_staging(job_id)
        js._finish(
            audit_id,
            job_id,
            "success",
            json.dumps({"ok": True, "project": project, "dest_id": dest_id}),
            hostname,
            "service_migrate",
        )
        log_line("Done.")
        logger.info("[migrate] job %s ok %s", job_id, result)
    except Exception as e:
        logger.exception("service_migrate failed")
        step = getattr(e, "failed_step", None)
        if step in RECOVER_SOURCE_STEPS and source:
            try:
                from .service_migrate.leftover import jailed_source_project_path

                path = jailed_source_project_path(source, project)
                with js._get_fresh_session() as session:
                    job = session.get(Job, job_id)
                    if job:
                        js._merge_job_details(
                            job,
                            failed_step=step,
                            recover_source={
                                "server_id": int(source_id),
                                "project": project,
                                "project_path": path,
                            },
                        )
                        session.add(job)
                        session.commit()
            except Exception:
                logger.exception("migrate recover_source details")
        js._finish(audit_id, job_id, "failed", str(e)[:800], hostname, "service_migrate")

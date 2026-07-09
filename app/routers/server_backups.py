"""
Backups sub-router for PiHerder.

Extracted from routers/servers.py.
Handles:
- /servers/{id}/backups page
- run/stop/retention backup actions
- backup progress + streaming
- backup config + source management
"""

from fastapi import APIRouter, Depends, Form, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool
from ..database import get_session, engine
from ..models import Server, AuditLog, Job
from datetime import datetime
import json
from typing import Optional
import asyncio
import time
import logging

from ..services import jobs as job_service
from ..services import backup as backup_svc
from ..services.herder_backup import format_datetime_in_app_tz
from ..services.server_audit import record_server_audit
from .. import templates as templates_mod
from ..security.auth import get_current_user
from ..models import User
from ..config import settings

try:
    import pycron
except ImportError:
    pycron = None

router = APIRouter()
logger = logging.getLogger("piherder.servers")

# Short-lived cache so modal polls don't hammer Postgres during long rsync jobs.
_backup_progress_http_cache: dict[str, tuple[float, dict]] = {}


def _server_redirect(server_id: int) -> str:
    return f"/servers/{server_id}"


@router.get("/{server_id}/backups", response_class=HTMLResponse)
async def server_backups(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    show_ssh_key = request.query_params.get("show_ssh_key") == "1"
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    server_dict = server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"})

    active_backup_jobs = job_service.get_active_backup_jobs(session, server.id)
    running_backup_job = job_service.get_running_backup_job(session, server.id)
    current_backup_job = running_backup_job or (active_backup_jobs[-1] if active_backup_jobs else None)
    backup_running = bool(active_backup_jobs)

    backup_profiles = []
    last_backup_status = None
    global_backup_defaults = {}
    current_sources = []

    try:
        backup_profiles = job_service.attach_source_job_states(
            backup_svc.get_backup_profiles_db(server),
            active_backup_jobs,
        )

        last_backup_log = session.exec(
            select(AuditLog)
            .where(AuditLog.server_id == server.id, AuditLog.action == "backup")
            .order_by(AuditLog.started_at.desc())
            .limit(1)
        ).first()
        last_backup_status = (
            backup_svc.effective_backup_status(
                last_backup_log.status, last_backup_log.output_snippet
            )
            if last_backup_log
            else None
        )

        global_backup_defaults = backup_svc.global_backup_defaults_from_server(server)
        current_sources = [p.get("source") for p in backup_profiles]
    except Exception:
        pass

    from ..services.backup_path_policy import parse_rules, DEFAULT_DENY_PREFIXES

    path_rules = parse_rules(getattr(server, "backup_path_rules", None))

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_backups.html",
        context={
            "title": f"Backups - {server.name}",
            "server": server_dict,
            "backup_profiles": backup_profiles,
            "last_backup_status": last_backup_status,
            "user": user,
            "settings": settings,
            "global_backup_defaults": global_backup_defaults,
            "current_sources": current_sources,
            "show_ssh_key": show_ssh_key,
            "backup_running": backup_running,
            "current_backup_job": current_backup_job,
            "running_backup_job": running_backup_job,
            "active_backup_jobs": active_backup_jobs,
            "path_rules": path_rules,
            "default_deny_paths": list(DEFAULT_DENY_PREFIXES),
            "lean_page": True,
        }
    )


@router.post("/{server_id}/run/backup")
async def run_backup(
    request: Request,
    server_id: int,
    background_tasks: BackgroundTasks,
    source: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    _backup_progress_http_cache.clear()

    async_mode = request.headers.get("X-PiHerder-Async") == "1"

    try:
        if source:
            job = job_service.create_job_and_run(
                background_tasks, session, server, "backup", user_id=user.id, source_filter=source
            )
        else:
            job = job_service.create_job_and_run(
                background_tasks, session, server, "backup", user_id=user.id
            )
    except job_service.BackupAlreadyRunning as exc:
        return JSONResponse(
            {
                "detail": "Backup already queued or running for this source",
                "job_id": exc.job.id,
                "active": True,
                "source_filter": job_service.job_source_filter(exc.job),
                "status": exc.job.status,
            },
            status_code=409,
        )

    if async_mode:
        return JSONResponse(
            {
                "job_id": job.id,
                "status": job.status,
                "source_filter": job_service.job_source_filter(job),
            }
        )

    referer = request.headers.get("referer") or ""
    if f"/servers/{server_id}/backups" in referer:
        return RedirectResponse(f"/servers/{server_id}/backups", status_code=303)
    return RedirectResponse(_server_redirect(server_id), status_code=303)


@router.post("/{server_id}/backup/stop")
async def stop_backup(
    server_id: int,
    job_id: Optional[int] = Form(None),
    source: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    _backup_progress_http_cache.clear()
    target = job_service.resolve_backup_job(
        session, server_id, job_id=job_id, source_filter=source
    )
    job_service.stop_active_backup(session, server, job=target)
    audit = AuditLog(
        user_id=user.id,
        server_id=server.id,
        action="backup_stop",
        status="success",
        details="Backup stopped by user",
    )
    session.add(audit)
    session.commit()
    return RedirectResponse(_server_redirect(server_id), status_code=303)


@router.get("/{server_id}/backup-progress", response_class=JSONResponse)
async def get_backup_progress(
    server_id: int,
    job_id: Optional[int] = None,
    source: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """Thin read: prefer Job.details from DB; Redis is legacy fallback only."""
    cache_key = f"{server_id}:{job_id or source or 'default'}"

    def _read_progress():
        with Session(engine) as db:
            server = db.get(Server, server_id)
            if not server:
                return None
            job = job_service.resolve_backup_job(
                db, server_id, job_id=job_id, source_filter=source
            )
            if job:
                prog = backup_svc.get_job_backup_progress_from_db(job)
                if prog:
                    prog["hostname"] = server.hostname
                    prog["source_filter"] = job_service.job_source_filter(job)
                    prog["source"] = "db"
                    return prog
            prog = backup_svc.get_backup_progress(server.hostname)
            return {
                "current": prog.get("current"),
                "log_lines": prog.get("log_lines", [])[-15:],
                "last_updated": prog.get("last_updated"),
                "hostname": server.hostname,
                "source": "redis",
            }

    now = time.time()
    cached = _backup_progress_http_cache.get(cache_key)
    if cached and (now - cached[0]) < 3.0:
        return cached[1]

    payload = await run_in_threadpool(_read_progress)
    if payload is None:
        raise HTTPException(404)
    _backup_progress_http_cache[cache_key] = (now, payload)
    return payload


@router.get("/{server_id}/backup/logs/stream")
async def stream_backup_logs(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    async def event_generator():
        last_index = 0
        while True:
            prog = backup_svc.get_backup_progress(server.hostname)
            lines = prog.get("log_lines", [])
            for line in lines[last_index:]:
                yield f"data: {line}\n\n"
            last_index = len(lines)
            await asyncio.sleep(2.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.post("/{server_id}/backup-config")
async def update_backup_config(
    server_id: int,
    backup_paths: str = Form(""),
    retention_days: int = Form(None),
    backup_schedule: str = Form(None),
    dest_root: str = Form(""),
    folder_name: str = Form(""),
    scope: str = Form("this_host"),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    if backup_schedule is not None:
        cron = backup_schedule.strip() or None
        if cron and pycron:
            try:
                parts = cron.split()
                if len(parts) != 5:
                    raise ValueError("not 5 fields")
                pycron.is_now(cron, datetime.now())
            except Exception:
                raise HTTPException(400, "Invalid cron expression. Example: '0 2 * * *' for 2am daily.")
        backup_schedule = cron

    if scope == "global":
        paths = [p.strip() for p in backup_paths.replace(",", "\n").splitlines() if p.strip()] or server.get_backup_paths()
        global_config = {
            "sources": paths,
            "dest_root": dest_root.strip() or None,
        }
        # Note: global config saving is stubbed in current backup_svc
        backup_svc.save_global_backup_defaults(global_config)
    else:
        if backup_paths:
            # Update per-server paths (supports both old and new format via service)
            try:
                from ..services.backup_path_policy import validate_backup_path, parse_rules

                rules = parse_rules(getattr(server, "backup_path_rules", None))
                lines = [p.strip() for p in backup_paths.replace(",", "\n").splitlines() if p.strip()]
                if lines:
                    bad = []
                    for p in lines:
                        ok, reason = validate_backup_path(p, rules)
                        if not ok:
                            bad.append(f"{p}: {reason}")
                    if bad:
                        raise HTTPException(
                            400,
                            "Backup path policy rejected: " + "; ".join(bad[:5]),
                        )
                    server.backup_paths = json.dumps(
                        [{"source": p, "dest_name": None, "enabled": True} for p in lines]
                    )
            except HTTPException:
                raise
            except Exception:
                server.backup_paths = json.dumps(backup_paths.split())

        if dest_root.strip():
            server.backup_dest_root = dest_root.strip()
            session.add(server)
            session.commit()

        if folder_name.strip():
            server.backup_folder_name = folder_name.strip()
            session.add(server)
            session.commit()

    if retention_days is not None:
        server.retention_days = retention_days
        session.add(server)
        session.commit()

    if backup_schedule is not None:
        server.backup_schedule = backup_schedule
        if backup_schedule:
            server.backup_enabled = True
        session.add(server)
        session.commit()

    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_backup_config",
        details={"scope": scope, "message": "Backup configuration updated"},
    )
    session.commit()

    # Live re-register APScheduler jobs (no web restart required)
    try:
        from ..main import scheduler, HAS_SCHEDULER
        from ..services.scheduler import sync_server_cron_jobs
        session.refresh(server)
        sync_server_cron_jobs(scheduler, HAS_SCHEDULER, server)
    except Exception as e:
        logger.warning(f"Could not sync backup schedule for server {server_id}: {e}")

    return RedirectResponse(f"/servers/{server_id}/backups", status_code=303)


@router.post("/{server_id}/backup-path-rules")
async def update_backup_path_rules(
    server_id: int,
    allow_paths: str = Form(""),
    deny_paths: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Save per-server backup source allow/deny path lists."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    from ..services.backup_path_policy import rules_to_json, _as_list

    allow = _as_list(allow_paths)
    deny = _as_list(deny_paths)
    server.backup_path_rules = rules_to_json(allow, deny)
    session.add(server)
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_backup_config",
        details={
            "message": "Backup path allow/deny rules updated",
            "allow": allow,
            "deny": deny,
        },
    )
    session.commit()
    return RedirectResponse(f"/servers/{server_id}/backups", status_code=303)


@router.post("/{server_id}/backup/add")
async def add_backup_source(
    server_id: int,
    new_path: str = Form(...),
    dest_name: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    if not new_path or not new_path.strip():
        raise HTTPException(400, "Source path is required")
    dn = dest_name.strip() if dest_name else None
    try:
        added = backup_svc.add_backup_source(server, new_path, dn, session)
    except ValueError as e:
        raise HTTPException(400, str(e))
    logger.info(f"[backup-add] add_backup_source returned {added} for server {server_id} path={new_path}")
    session.refresh(server)
    logger.info(f"[backup-add] after refresh backup_paths={server.backup_paths[:120]}...")
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_backup_source_add",
        status="success" if added else "failed",
        details={"source": new_path.strip(), "message": f"Added backup source {new_path.strip()}"},
    )
    session.commit()
    return RedirectResponse(f"/servers/{server_id}/backups", status_code=303)


@router.post("/{server_id}/backup/remove")
async def remove_backup_source(
    server_id: int,
    path: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    backup_svc.remove_backup_source(server, path, session)
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_backup_source_remove",
        details={"source": path.strip(), "message": f"Removed backup source {path.strip()}"},
    )
    session.commit()
    return RedirectResponse(f"/servers/{server_id}/backups", status_code=303)


@router.post("/{server_id}/run/retention")
async def run_retention(
    request: Request,
    server_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    job = job_service.create_job_and_run(
        background_tasks, session, server, "retention", user_id=user.id
    )
    if request.headers.get("X-PiHerder-Async") == "1":
        return JSONResponse({"job_id": job.id, "status": job.status, "job_type": "retention"})
    return RedirectResponse(_server_redirect(server_id), status_code=303)

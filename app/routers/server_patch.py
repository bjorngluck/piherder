"""OS/container patch runs, checks, schedules, diagnostics, job status (from servers.py)."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, Form, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from ..database import get_session, engine
from ..models import Server, AuditLog, Job, User
from ..security.auth import get_current_user
from ..services import jobs as job_service
from ..services import os_patching
from ..services import diagnostics as diag_svc
from ..services.app_settings import format_datetime_in_app_tz
from ..services.server_audit import record_server_audit
from .server_common import server_redirect

try:
    import pycron
except ImportError:
    pycron = None

router = APIRouter()
logger = logging.getLogger("piherder.servers")

@router.get("/{server_id}/os-patch-progress", response_class=JSONResponse)
async def get_os_patch_progress(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    prog = os_patching.get_os_patch_progress(server.hostname)
    return {
        "current": prog.get("current"),
        "log_lines": prog.get("log_lines", []),
        "done": bool(prog.get("done")),
        "finished_ok": prog.get("finished_ok"),
        "hostname": server.hostname,
    }


@router.get("/{server_id}/os-patch/logs/stream")
async def stream_os_patch_logs(
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
            prog = os_patching.get_os_patch_progress(server.hostname)
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


@router.get("/{server_id}/diagnostics", response_class=JSONResponse)
async def get_server_diagnostics(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    force: bool = False,
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        data = await run_in_threadpool(diag_svc.run_diagnostics, server, bool(force))
        return data
    except Exception as e:
        return {"error": str(e)[:200], "hostname": server.hostname
    }


@router.post("/{server_id}/run/container_patch")
async def run_container_patch(
    request: Request,
    server_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        job = job_service.create_job_and_run(
            background_tasks, session, server, "container_patch", user_id=user.id
        )
        reused = False
    except job_service.JobAlreadyActive as e:
        job = e.job
        reused = True
    if request.headers.get("X-PiHerder-Async") == "1":
        return JSONResponse(
            {
                "job_id": job.id,
                "status": job.status,
                "job_type": "container_patch",
                "already_active": reused,
            },
            status_code=409 if reused else 200,
        )
    return RedirectResponse(f"/servers/{server_id}", status_code=303)


@router.post("/{server_id}/run/os_patch")
async def run_os_patch(
    request: Request,
    server_id: int,
    background_tasks: BackgroundTasks,
    steps: list[str] = Form([]),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    # Normalize: allowed steps only; upgrade XOR full-upgrade; default if empty
    steps = os_patching.normalize_os_patch_steps(steps or None)
    if not steps:
        steps = ["update", "upgrade", "autoremove"]
    try:
        job = job_service.create_job_and_run(
            background_tasks, session, server, "os_patch", user_id=user.id, os_steps=steps
        )
        reused = False
    except job_service.JobAlreadyActive as e:
        job = e.job
        reused = True
    if request.headers.get("X-PiHerder-Async") == "1":
        return JSONResponse(
            {
                "job_id": job.id,
                "status": job.status,
                "job_type": "os_patch",
                "already_active": reused,
            },
            status_code=409 if reused else 200,
        )
    return RedirectResponse(server_redirect(server_id), status_code=303)


@router.get("/{server_id}/jobs", response_class=JSONResponse)
async def list_server_jobs(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    limit: int = 25,
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    active_only: bool = False,
):
    """Queue + history for a server (running/pending and recent finished)."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    jobs = job_service.list_jobs_for_server(
        session,
        server_id,
        limit=limit,
        status=status,
        job_type=job_type,
        active_only=active_only,
    )
    return {
        "server_id": server_id,
        "jobs": [job_service.job_public_dict(j) for j in jobs],
        "active_count": sum(1 for j in jobs if j.status in ("pending", "running")),
    }


@router.get("/{server_id}/jobs/{job_id}", response_class=JSONResponse)
async def get_server_job_status(
    server_id: int,
    job_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Poll endpoint for holding modal: status + log_lines + summary."""
    job = session.get(Job, job_id)
    if not job or job.server_id != server_id:
        raise HTTPException(404)
    server = session.get(Server, server_id)
    details: dict = {}
    if job.details:
        try:
            parsed = json.loads(job.details)
            if isinstance(parsed, dict):
                details = parsed
        except Exception:
            details = {"raw": (job.details or "")[:400]}
    log_lines = list(details.get("log_lines") or [])
    current = details.get("current")
    # Merge live in-memory tails so JobHold shows progress before DB flush catches up
    if server and job.status in ("pending", "running"):
        host = server.hostname or ""
        if job.job_type == "container_patch":
            try:
                from ..services import container_patching
                prog = container_patching.get_container_patch_progress(host)
                if prog.get("log_lines"):
                    log_lines = prog["log_lines"]
                if prog.get("current"):
                    current = prog["current"]
            except Exception:
                pass
        elif job.job_type == "os_patch":
            try:
                prog = os_patching.get_os_patch_progress(host)
                if prog.get("log_lines"):
                    log_lines = prog["log_lines"]
                if prog.get("current"):
                    current = prog["current"]
            except Exception:
                pass
    done = job.status in ("success", "failed")
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "done": done or bool(details.get("done")),
        "current": current,
        "log_lines": log_lines,
        "summary": details.get("summary") or "",
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def validate_cron(cron: str | None) -> str | None:
    if not cron:
        return None
    cron = cron.strip() or None
    if not cron:
        return None
    if pycron:
        try:
            parts = cron.split()
            if len(parts) != 5:
                raise ValueError("not 5 fields")
            pycron.is_now(cron, datetime.now())
        except Exception:
            raise HTTPException(400, "Invalid cron expression. Example: '0 6 * * *'")
    return cron


def sync_server_schedules(server: Server):
    try:
        from ..main import scheduler, HAS_SCHEDULER
        from ..services.scheduler import sync_server_cron_jobs
        sync_server_cron_jobs(scheduler, HAS_SCHEDULER, server)
    except Exception as e:
        logger.warning(f"Could not sync schedules for server {server.id}: {e}")


@router.post("/{server_id}/check/os-updates")
async def check_os_updates_now(
    request: Request,
    server_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    # Queue on shared pool (same as scheduled checks)
    job = job_service.enqueue_os_update_check(server.id, user_id=user.id)
    if not job:
        raise HTTPException(500, "Could not queue OS update check")
    if request.headers.get("X-PiHerder-Async") == "1":
        return JSONResponse({"job_id": job.id, "status": job.status, "job_type": "os_update_check"})
    return RedirectResponse(f"/servers/{server_id}?os_check=1", status_code=303)


@router.post("/{server_id}/check/container-updates")
async def check_container_updates_now(
    request: Request,
    server_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    job = job_service.enqueue_container_update_check(server.id, user_id=user.id)
    if not job:
        raise HTTPException(500, "Could not queue container update check")
    if request.headers.get("X-PiHerder-Async") == "1":
        return JSONResponse({"job_id": job.id, "status": job.status, "job_type": "container_update_check"})
    return RedirectResponse(f"/servers/{server_id}?container_check=1", status_code=303)


@router.post("/{server_id}/schedule/os-check")
async def save_os_check_schedule(
    server_id: int,
    os_check_enabled: Optional[str] = Form(None),
    os_check_schedule: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    cron = validate_cron(os_check_schedule)
    server.os_check_schedule = cron
    server.os_check_enabled = os_check_enabled in ("1", "on", "true") and bool(cron)
    session.add(server)
    session.commit()
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_os_check_schedule",
        details={"enabled": server.os_check_enabled, "cron": cron},
    )
    session.commit()
    sync_server_schedules(server)
    return RedirectResponse(server_redirect(server_id), status_code=303)


@router.post("/{server_id}/schedule/container-check")
async def save_container_check_schedule(
    server_id: int,
    container_check_enabled: Optional[str] = Form(None),
    container_check_schedule: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    cron = validate_cron(container_check_schedule)
    server.container_check_schedule = cron
    server.container_check_enabled = container_check_enabled in ("1", "on", "true") and bool(cron)
    session.add(server)
    session.commit()
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_container_check_schedule",
        details={"enabled": server.container_check_enabled, "cron": cron},
    )
    session.commit()
    sync_server_schedules(server)
    return RedirectResponse(server_redirect(server_id), status_code=303)


@router.post("/{server_id}/schedule/os-apply")
async def save_os_apply_schedule(
    server_id: int,
    os_apply_enabled: Optional[str] = Form(None),
    os_apply_schedule: str = Form(""),
    os_apply_only_if_updates: Optional[str] = Form(None),
    os_apply_use_full_upgrade: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Opt-in scheduled OS patch apply. Requires os_patch_enabled. Default steps: update+upgrade+autoremove."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    if not server.os_patch_enabled:
        raise HTTPException(400, "Enable OS patch feature on this server first")
    cron = validate_cron(os_apply_schedule)
    want = os_apply_enabled in ("1", "on", "true") and bool(cron)
    use_full = os_apply_use_full_upgrade in ("1", "on", "true")
    steps = ["update", "full-upgrade" if use_full else "upgrade", "autoremove"]
    server.os_apply_schedule = cron
    server.os_apply_enabled = want
    # Unchecked checkbox is omitted from form → False
    server.os_apply_only_if_updates = os_apply_only_if_updates in ("1", "on", "true")
    server.os_apply_steps = json.dumps(steps)
    session.add(server)
    session.commit()
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_os_apply_schedule",
        details={
            "enabled": server.os_apply_enabled,
            "cron": cron,
            "steps": steps,
            "only_if_updates": server.os_apply_only_if_updates,
        },
    )
    session.commit()
    sync_server_schedules(server)
    return RedirectResponse(server_redirect(server_id), status_code=303)


@router.post("/{server_id}/schedule/container-apply")
async def save_container_apply_schedule(
    server_id: int,
    container_apply_enabled: Optional[str] = Form(None),
    container_apply_schedule: str = Form(""),
    container_apply_only_if_updates: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Opt-in scheduled container patch (pull + up -d when IDs change)."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    if not server.container_patch_enabled:
        raise HTTPException(400, "Enable container patch feature on this server first")
    cron = validate_cron(container_apply_schedule)
    want = container_apply_enabled in ("1", "on", "true") and bool(cron)
    server.container_apply_schedule = cron
    server.container_apply_enabled = want
    server.container_apply_only_if_updates = container_apply_only_if_updates in ("1", "on", "true")
    session.add(server)
    session.commit()
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_container_apply_schedule",
        details={
            "enabled": server.container_apply_enabled,
            "cron": cron,
            "only_if_updates": server.container_apply_only_if_updates,
        },
    )
    session.commit()
    sync_server_schedules(server)
    return RedirectResponse(server_redirect(server_id), status_code=303)


# Docker routes extracted to server_docker.py (sub-router included at top of file)


from fastapi import APIRouter, Depends, Form, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from sqlmodel import Session, select
from sqlalchemy import func
import json
from typing import Optional, List
from starlette.concurrency import run_in_threadpool
from ..database import get_session, engine
from ..models import Server, AuditLog, Job
from datetime import datetime
from ..security import encryption
import asyncio
from ..services import ssh as ssh_service
from ..services import jobs as job_service
from ..services import docker_management as docker_svc
from ..services import backup as backup_svc
from ..services import diagnostics as diag_svc
from ..services import os_patching
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
from datetime import datetime
import time
import logging

router = APIRouter()
logger = logging.getLogger("piherder.servers")

# Short-lived cache so modal polls don't hammer Postgres during long rsync jobs.
_backup_progress_http_cache: dict[str, tuple[float, dict]] = {}


@router.get("", response_class=HTMLResponse)
async def list_servers(request: Request, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    """Extremely lean Servers list - pure DB read.
    last_backup_at is populated by the worker on success.
    No extra grouped queries, no SSH, no FS work.
    """
    start = time.time()

    try:
        rows = session.exec(select(Server).order_by(Server.sort_order, Server.name)).all()
    except Exception:
        rows = session.exec(select(Server).order_by(Server.name)).all()

    running_backup_ids = set(
        session.exec(
            select(Job.server_id).where(
                Job.job_type == "backup",
                Job.status.in_(["pending", "running"]),
            )
        ).all()
    )

    servers = []
    for row in rows:
        d = row.model_dump(exclude={"audit_logs", "jobs"})
        if row.last_backup_at:
            d["last_backup"] = row.last_backup_at
            d["last_backup_str"] = format_datetime_in_app_tz(row.last_backup_at)
        d["backup_running"] = row.id in running_backup_ids
        servers.append(d)

    total = time.time() - start
    if total > 0.3:
        logger.warning(f"[list_servers] Total render took {total:.2f}s for {len(servers)} server(s)")
    else:
        logger.debug(f"[list_servers] Total render took {total:.2f}s")

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_list.html",
        context={"title": "Servers", "servers": servers, "user": user, "lean_page": True}
    )


@router.post("/{server_id}/move/{direction}")
async def move_server(
    server_id: int,
    direction: str,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    if direction not in ("up", "down"):
        raise HTTPException(400)
    try:
        servers = list(session.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
    except Exception:
        servers = list(session.exec(select(Server).order_by(Server.name)).all())
    try:
        idx = next(i for i, s in enumerate(servers) if s.id == server_id)
    except StopIteration:
        raise HTTPException(404)
    if direction == "up" and idx > 0:
        servers[idx], servers[idx-1] = servers[idx-1], servers[idx]
    elif direction == "down" and idx < len(servers) - 1:
        servers[idx], servers[idx+1] = servers[idx+1], servers[idx]
    for i, s in enumerate(servers):
        s.sort_order = i * 10
        session.add(s)
    record_server_audit(
        session,
        server_id=server_id,
        user_id=user.id,
        action="server_move",
        details={"direction": direction, "message": f"Moved {direction} in server list"},
    )
    session.commit()
    return RedirectResponse("/servers", status_code=303)


# Roadmap: guided onboarding wizard (SPEC.md) — deploy SSH key via password session,
# provision least-privilege backup user + sudoers, rotate keys from server settings.


@router.get("/add", response_class=HTMLResponse)
async def add_server_form(request: Request, user: User = Depends(get_current_user)):
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="add_server.html",
        context={"title": "Add Server", "user": user}
    )


@router.post("/add")
async def add_server(
    name: str = Form(...),
    hostname: str = Form(...),
    ssh_username: str = Form("bjorn"),
    ssh_port: int = Form(22),
    key_mode: str = Form("generate"),
    private_key: str = Form(""),
    ssh_password: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    pub = None
    priv_enc = None

    priv_enc = None
    pub = None
    pw_enc = None

    if key_mode == "generate":
        pub, priv = ssh_service.generate_keypair()
        priv_enc = encryption.encrypt_str(priv)
    elif key_mode == "password":
        if not ssh_password or not ssh_password.strip():
            raise HTTPException(400, "Password required when using password auth")
        pub = "(password auth - no public key)"
        pw_enc = encryption.encrypt_str(ssh_password.strip())
    else:
        if not private_key.strip():
            raise HTTPException(400, "Private key required for upload mode")
        priv_enc = encryption.encrypt_str(private_key.strip())
        pub = "(provided with private key - test connection to verify)"

    current_max = session.scalar(select(func.max(Server.sort_order)))
    next_sort = int(current_max or 0) + 10
    server = Server(
        name=name,
        hostname=hostname,
        ssh_username=ssh_username,
        ssh_port=ssh_port,
        ssh_private_key_encrypted=priv_enc,
        ssh_public_key=pub,
        ssh_password_encrypted=pw_enc,
        sort_order=next_sort,
        backup_enabled=True,
    )
    session.add(server)
    session.commit()
    session.refresh(server)

    auth_method = {"generate": "generated_key", "upload": "uploaded_key", "password": "password_auth"}.get(
        key_mode, key_mode
    )
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_create",
        details={
            "name": server.name,
            "hostname": server.hostname,
            "ssh_username": server.ssh_username,
            "auth_method": auth_method,
            "message": f"Server {server.name} added",
        },
    )
    if key_mode == "password":
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_password_set",
            message="SSH password set on server create",
        )
    session.commit()

    redirect_url = f"/servers/{server.id}"
    if key_mode == "generate":
        redirect_url += "?show_ssh_key=1"
    return RedirectResponse(redirect_url, status_code=303)


@router.get("/{server_id}", response_class=HTMLResponse)
async def server_detail(server_id: int, request: Request, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    show_ssh_key = request.query_params.get("show_ssh_key") == "1"
    edit_mode = request.query_params.get("edit") == "1"

    server_dict = server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"})

    reboot_initiated = request.query_params.get("rebooted") == "1"

    backup_profiles = []
    overall_last_backup = None
    last_backup_status = None
    recent_backups = []
    global_backup_defaults = {}
    current_sources = []
    diagnostics = {"error": "n/a"}
    current_backup_job = None   # DB-backed status (worker writes here)

    try:
        backup_profiles = backup_svc.get_backup_profiles_db(server)
        overall_last_backup = server.last_backup_at

        # Latest backup Job from DB (source of truth for running state)
        current_backup_job = session.exec(
            select(Job)
            .where(Job.server_id == server.id, Job.job_type == "backup")
            .order_by(Job.created_at.desc())
            .limit(1)
        ).first()

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

        recent_backups = session.exec(
            select(AuditLog)
            .where(AuditLog.server_id == server.id, AuditLog.action == "backup")
            .order_by(AuditLog.started_at.desc())
            .limit(10)
        ).all()

        for log in recent_backups:
            object.__setattr__(log, 'parsed', None)
            fj = (log.output_snippet or '').strip() or '{}'
            object.__setattr__(log, 'full_json', fj)
            if log.output_snippet:
                try:
                    data = json.loads(log.output_snippet)
                    results = data.get("results", [])
                    total_bytes = sum(r.get("size_bytes", 0) for r in results)
                    object.__setattr__(log, 'parsed', {
                        "sources": len(results),
                        "success_count": sum(1 for r in results if backup_svc.backup_source_ok(r)),
                        "total_size": total_bytes,
                        "total_size_human": backup_svc.human_size(total_bytes),
                    })
                    object.__setattr__(log, 'full_json', json.dumps(data, indent=2))
                except Exception:
                    pass

        global_backup_defaults = backup_svc.global_backup_defaults_from_server(server)
        current_sources = [p.get("source") for p in backup_profiles]

        # Skip SSH diagnostics on page load — backup pages must stay fast.
    except Exception:
        diagnostics = {"error": "Could not load server details"}

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_detail.html",
        context={
            "title": server.name,
            "server": server_dict,
            "backup_profiles": backup_profiles,
            "overall_last_backup": overall_last_backup,
            "last_backup_status": last_backup_status,
            "recent_backups": recent_backups,
            "current_backup_job": current_backup_job,
            "running_backup_job": job_service.get_running_backup_job(session, server.id),
            "full_backup_job": job_service.get_active_job_for_source(session, server.id, None),
            "active_backup_jobs": job_service.get_active_backup_jobs(session, server.id),
            "backup_active": bool(job_service.get_active_backup_jobs(session, server.id)),
            "user": user,
            "settings": settings,
            "global_backup_defaults": global_backup_defaults,
            "current_sources": current_sources,
            "diagnostics": diagnostics,
            "reboot_initiated": reboot_initiated,
            "show_ssh_key": show_ssh_key,
            "edit_mode": edit_mode,
            "lean_page": True,
        }
    )


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
            "lean_page": True,
        }
    )


def _server_redirect(server_id: int) -> str:
    return f"/servers/{server_id}"


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


@router.post("/{server_id}/reboot")
async def reboot_server(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    success = False
    details = "Reboot initiated"
    try:
        client = ssh_service.get_ssh_client(server)
        try:
            client.exec_command("sudo reboot", timeout=3)
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass
        success = True
    except Exception as e:
        details = f"Reboot command failed to send: {e}"

    try:
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="reboot",
            status="success" if success else "failed",
            message=details,
        )
        session.commit()
    except Exception:
        pass

    return RedirectResponse(f"/servers/{server_id}?rebooted=1", status_code=303)


@router.post("/{server_id}/update")
async def update_server(
    server_id: int,
    name: str = Form(...),
    hostname: str = Form(...),
    ssh_username: str = Form(...),
    ssh_port: int = Form(22),
    ssh_password: str = Form(""),
    clear_password: Optional[str] = Form(None),
    backup_enabled: bool = Form(False),
    container_patch_enabled: bool = Form(False),
    os_patch_enabled: bool = Form(False),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404, "Server not found")

    changed: list[str] = []
    new_name = name.strip()
    new_host = hostname.strip()
    new_user = ssh_username.strip()
    if server.name != new_name:
        changed.append("name")
    if server.hostname != new_host:
        changed.append("hostname")
    if server.ssh_username != new_user:
        changed.append("ssh_username")
    if server.ssh_port != ssh_port:
        changed.append("ssh_port")
    if server.backup_enabled != backup_enabled:
        changed.append("backup_enabled")
    if server.container_patch_enabled != container_patch_enabled:
        changed.append("container_patch_enabled")
    if server.os_patch_enabled != os_patch_enabled:
        changed.append("os_patch_enabled")

    server.name = new_name
    server.hostname = new_host
    server.ssh_username = new_user
    server.ssh_port = ssh_port

    if clear_password:
        server.ssh_password_encrypted = None
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_password_clear",
            message="SSH password cleared",
        )
    elif ssh_password and ssh_password.strip():
        try:
            server.ssh_password_encrypted = encryption.encrypt_str(ssh_password.strip())
        except Exception as e:
            raise HTTPException(500, f"Failed to encrypt password: {e}")
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_password_set",
            message="SSH password updated",
        )

    server.backup_enabled = backup_enabled
    server.container_patch_enabled = container_patch_enabled
    server.os_patch_enabled = os_patch_enabled

    if changed:
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_update",
            details={"fields": changed, "message": f"Updated {', '.join(changed)}"},
        )

    session.add(server)
    session.commit()

    return RedirectResponse(_server_redirect(server_id), status_code=303)


@router.post("/{server_id}/audit/ssh-key-viewed", response_class=JSONResponse)
async def audit_ssh_key_viewed(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_ssh_key_viewed",
        message=f"SSH public key viewed for {server.name}",
    )
    session.commit()
    return {"ok": True}


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
        "log_lines": prog.get("log_lines", [])[-15:],
        "hostname": server.hostname
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
async def run_container_patch(server_id: int, background_tasks: BackgroundTasks, session: Session = Depends(get_session), user: User = Depends(get_current_user)):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    job_service.create_job_and_run(background_tasks, session, server, "container_patch", user_id=user.id)
    return RedirectResponse(f"/servers/{server_id}", status_code=303)


@router.post("/{server_id}/run/os_patch")
async def run_os_patch(server_id: int, background_tasks: BackgroundTasks, steps: list[str] = Form([]), session: Session = Depends(get_session),
    user: User = Depends(get_current_user)):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    if not steps:
        steps = ["update", "upgrade", "autoremove"]
    job_service.create_job_and_run(background_tasks, session, server, "os_patch", user_id=user.id, os_steps=steps)
    return RedirectResponse(_server_redirect(server_id), status_code=303)


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
            "folder_name": folder_name.strip() or None,
        }
        backup_svc.save_global_backup_defaults(global_config)

        if dest_root.strip():
            server.backup_dest_root = dest_root.strip()
        if folder_name.strip():
            server.backup_folder_name = folder_name.strip()
        if backup_paths.strip():
            existing = {s["source"]: s for s in server.get_backup_sources()}
            lines = [p.strip() for p in backup_paths.replace(",", "\n").splitlines() if p.strip()]
            new_sources = []
            for line in lines:
                if line in existing:
                    new_sources.append(existing[line])
                else:
                    new_sources.append({"source": line, "dest_name": None, "enabled": True})
            server.backup_paths = json.dumps(new_sources)
        if retention_days is not None:
            server.retention_days = retention_days
        if backup_schedule is not None:
            server.backup_schedule = backup_schedule
            if backup_schedule:
                server.backup_enabled = True
        session.add(server)
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_backup_config",
            details={"scope": "global", "message": "Global backup defaults updated"},
        )
        session.commit()
        return RedirectResponse(f"/servers/{server_id}/backups", status_code=303)

    # this_host only - force write + commit + debug
    if backup_paths.strip():
        existing = {s["source"]: s for s in server.get_backup_sources()}
        lines = [p.strip() for p in backup_paths.replace(",", "\n").splitlines() if p.strip()]
        new_sources = []
        for line in lines:
            if line in existing:
                new_sources.append(existing[line])
            else:
                    new_sources.append({"source": line, "dest_name": None, "enabled": True})
            server.backup_paths = json.dumps(new_sources)
            logger.info(f"[backup-config] FORCING commit for server {server_id} backup_paths={server.backup_paths[:120]}...")
            session.add(server)
            session.commit()
            logger.info(f"[backup-config] COMMIT DONE for server {server_id}")

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
    added = backup_svc.add_backup_source(server, new_path, dn, session)
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
async def run_retention(server_id: int, background_tasks: BackgroundTasks, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    job_service.create_job_and_run(background_tasks, session, server, "retention", user_id=user.id)
    return RedirectResponse(_server_redirect(server_id), status_code=303)


# =====================
# Docker Management
# =====================

@router.get("/{server_id}/docker", response_class=HTMLResponse)
async def docker_page(server_id: int, request: Request, session: Session = Depends(get_session), user: User = Depends(get_current_user)):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        import app.services.docker_management as _dm
        if request.query_params.get("nocache"):
            _dm._CACHE.clear()
    except:
        pass

    try:
        containers = docker_svc.list_containers(server)
    except Exception as e:
        containers = [{"name": "error", "status": str(e), "state": "error"}]

    try:
        projects = docker_svc.list_compose_projects(server)
    except Exception:
        projects = []

    update_check = request.query_params.get("update_check")
    update_status = request.query_params.get("status")
    build_status = request.query_params.get("build_status")

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="docker.html",
        context={
            "title": f"Docker - {server.name}",
            "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}),
            "containers": containers,
            "projects": projects,
            "user": user,
            "update_check": update_check,
            "update_status": update_status,
            "build_status": build_status
        }
    )


@router.post("/{server_id}/docker/container/{action}")
async def docker_container_action(
    server_id: int,
    action: str,
    name: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    result = docker_svc.container_action(server, name, action)
    try:
        audit = AuditLog(
            user_id=user.id if user else None,
            server_id=server_id,
            action=f"docker_container_{action}",
            status="success" if result.get("success") else "failed",
            details=f"Container {name}",
            output_snippet=str(result)[:500],
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        session.add(audit)
        session.commit()
    except Exception:
        pass
    return RedirectResponse(f"/servers/{server_id}/docker", status_code=303)


@router.get("/{server_id}/docker/compose/{project}/file-content", response_class=JSONResponse)
async def get_file_content(
    server_id: int,
    project: str,
    file: str = "compose",
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj:
        raise HTTPException(404)

    if file == "dockerfile":
        if not proj.get("dockerfile_path"):
            raise HTTPException(404, "No Dockerfile for this project")
        content = docker_svc.read_dockerfile(server, proj["dockerfile_path"])
        return {"ok": True, "file": "dockerfile", "content": content}
    else:
        live_files = docker_svc.get_project_live_files(server, proj["path"])
        for key in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            if key in live_files:
                return {"ok": True, "file": key, "content": live_files[key]}
        content = next(iter(live_files.values()), "") if live_files else ""
        return {"ok": True, "file": key, "content": content}
    content = next(iter(live_files.values()), "") if live_files else ""
    return {"ok": True, "file": key, "content": content}


@router.get("/{server_id}/docker/compose/{project}/edit", response_class=HTMLResponse)
async def edit_compose(
    server_id: int,
    project: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj:
        raise HTTPException(404)

    live_files = docker_svc.get_project_live_files(server, proj["path"])
    live_compose = live_files.get("docker-compose.yml") or live_files.get("docker-compose.yaml") or live_files.get("compose.yml") or live_files.get("compose.yaml") or ""
    content = live_compose
    drafts = docker_svc.get_versions(server.id, project, limit=10)

    load_draft_id = request.query_params.get("load_draft")
    editing_version_id = None
    if load_draft_id:
        try:
            dv = next((d for d in drafts if str(d.id) == load_draft_id), None)
            if dv:
                f = json.loads(dv.files)
                content = f.get('Dockerfile') or content
                if dv.is_draft:
                    editing_version_id = dv.id
        except:
            pass

    live_version = None
    live_clean = live_compose.strip() if live_compose else ''
    for d in drafts:
        if not d.is_draft:
            try:
                f = json.loads(d.files or '{}')
                c = f.get('Dockerfile') or ''
                if c.strip() == live_clean:
                    live_version = d
                    break
            except:
                pass

    errors_param = request.query_params.get("errors")
    errors = []
    if errors_param:
        try:
            import json as _json
            errors = sorted(_json.loads(errors_param) or [], key=lambda e: e.get("line", 0))
        except:
            errors = []

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="docker_compose_edit.html",
        context={
            "title": f"Edit {project}",
            "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}),
            "project": project,
            "content": content,
            "user": user,
            "errors": errors,
            "is_dockerfile": False,
            "drafts": drafts,
            "live_version": live_version,
            "editing_version_id": editing_version_id,
        }
    )


@router.get("/{server_id}/docker/compose/{project}/dockerfile/edit", response_class=HTMLResponse)
async def edit_dockerfile(
    server_id: int,
    project: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj or not proj.get("dockerfile_path"):
        raise HTTPException(404, "No Dockerfile path resolved for this project")

    live_content = docker_svc.read_dockerfile(server, proj["dockerfile_path"])
    content = live_content
    all_drafts = docker_svc.get_versions(server.id, project, limit=10)
    drafts = []
    for d in all_drafts:
        try:
            f = json.loads(d.files or '{}')
            if 'Dockerfile' in f:
                drafts.append(d)
        except:
            pass

    load_draft_id = request.query_params.get("load_draft")
    editing_version_id = None
    if load_draft_id:
        try:
            dv = next((d for d in drafts if str(d.id) == load_draft_id), None)
            if dv and dv.is_draft:
                editing_version_id = dv.id
                f = json.loads(dv.files or '{}')
                content = f.get('Dockerfile') or content
        except:
            pass

    live_version = None
    live_clean = live_content.strip() if live_content else ''
    for d in drafts:
        if not d.is_draft:
            try:
                f = json.loads(d.files or '{}')
                c = f.get('Dockerfile') or ''
                if c.strip() == live_clean:
                    live_version = d
                    break
            except:
                pass

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="docker_compose_edit.html",
        context={
            "title": f"Edit Dockerfile - {project}",
            "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}),
            "project": {"name": project, "path": proj["dockerfile_path"]},  # reuse fields
            "content": content,
            "user": user,
            "errors": [],
            "is_dockerfile": True,
            "drafts": df_drafts,
            "live_version": live_version,
            "editing_version_id": editing_version_id,
        }
    )


@router.post("/{server_id}/docker/compose/{project}/dockerfile/save")
async def save_dockerfile(
    server_id: int,
    project: str,
    content: str = Form(...),
    action: str = Form("deploy"),
    editing_version_id: Optional[int] = Form(None),
    via_modal: bool = Form(False),
    request: Request = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj or not proj.get("dockerfile_path"):
        raise HTTPException(404)

    if via_modal:
        return JSONResponse({"ok": False, "message": "Dockerfile editing is temporarily disabled."})

    return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/dockerfile/edit?disabled=1", status_code=303)


@router.get("/{server_id}/docker/new-project", response_class=HTMLResponse)
async def new_docker_project_form(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="new_docker_project.html",
        context={"title": "New Docker Service", "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}), "user": user}
    )


@router.post("/{server_id}/docker/new-project")
async def create_docker_project(
    server_id: int,
    project_name: str = Form(...),
    compose_content: str = Form(...),
    dockerfile_content: str = Form(""),
    git_url: str = Form(""),
    deploy_now: str = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    base_files = {"docker-compose.yml": compose_content}
    if dockerfile_content.strip():
        base_files["Dockerfile"] = dockerfile_content

    ok = docker_svc.create_new_docker_project(server, project_name, base_files, git_url=git_url or None)
    if ok and deploy_now:
        try:
            full = f"{server.docker_base_dir.replace('~', f'/home/{server.ssh_username}')}/{project_name}"
            docker_svc.redeploy_project(server, full, pull=True)
        except:
            pass

    try:
        from datetime import datetime as dt
        dv = docker_svc.save_draft_version(server.id, project_name, base_files, session)
        if ok and deploy_now:
            dv.is_draft = False
            dv.deployed_at = dt.utcnow()
            session.add(dv)
            session.commit()
    except:
        pass

    return RedirectResponse(f"/servers/{server_id}/docker?new_project={project_name}", status_code=303)


@router.post("/{server_id}/docker/compose/{project}/save-draft")
async def save_draft(
    server_id: int,
    project: str,
    content: str = Form(...),
    editing_version_id: Optional[int] = Form(None),
    via_modal: bool = Form(False),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    if via_modal:
        return JSONResponse({"ok": False, "message": "Draft saving temporarily disabled."})

    return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/edit?draft_disabled=1", status_code=303)


@router.post("/{server_id}/docker/compose/{project}/deploy-version")
async def deploy_version_route(
    server_id: int,
    project: str,
    version_id: int = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj:
        raise HTTPException(404)

    ok = docker_svc.deploy_version(server.id, version_id, server, proj["path"], session)
    status = "deployed" if ok else "failed"
    return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/edit?deploy_status={status}", status_code=303)


@router.get("/{server_id}/docker/compose/{project}/rollback/{version_id}")
async def rollback_version(
    server_id: int,
    project: str,
    version_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj:
        raise HTTPException(404)

    ok = docker_svc.deploy_version(server.id, version_id, server, proj["path"], session)
    return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/edit?rollback={ 'ok' if ok else 'fail'}", status_code=303)


@router.post("/{server_id}/docker/compose/{project}/validate", response_class=JSONResponse)
async def validate_compose(
    server_id: int,
    project: str,
    content: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    result = docker_svc.validate_compose_content(content)
    return JSONResponse(result)


@router.post("/{server_id}/docker/compose/{project}/save")
async def save_compose(
    server_id: int,
    project: str,
    content: str = Form(...),
    editing_version_id: Optional[int] = Form(None),
    via_modal: bool = Form(False),
    request: Request = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj:
        raise HTTPException(404)

    if via_modal:
        return JSONResponse({"ok": False, "message": "Compose saving temporarily disabled."})

    return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/edit?compose_disabled=1", status_code=303)


@router.post("/{server_id}/docker/redeploy")
async def redeploy(
    server_id: int,
    project_path: str = Form(...),
    pull: str = Form("true"),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    docker_svc.redeploy_project(server, project_path, pull=(pull == "true"))
    try:
        audit = AuditLog(
            user_id=user.id if user else None,
            server_id=server_id,
            action="docker_redeploy",
            status="success",
            details=f"Project {project_path}",
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        session.add(audit)
        session.commit()
    except Exception:
        pass
    return RedirectResponse(f"/servers/{server_id}/docker", status_code=303)


@router.post("/{server_id}/docker/compose/{action}")
async def compose_project_action(
    server_id: int,
    action: str,
    project_path: str = Form(...),
    service: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    svc = service or None
    res = docker_svc.compose_action(server, project_path, action, service=svc)
    try:
        details = f"Project {project_path}"
        if svc:
            details += f" service={svc}"
        audit = AuditLog(
            user_id=user.id if user else None,
            server_id=server_id,
            action=f"docker_compose_{action}",
            status="success" if res.get("success") else "failed",
            details=details,
            output_snippet=str(res)[:500],
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        session.add(audit)
        session.commit()
    except Exception:
        pass
    return RedirectResponse(f"/servers/{server_id}/docker", status_code=303)


@router.get("/{server_id}/docker/logs/{container}")
async def get_docker_logs(
    server_id: int,
    container: str,
    lines: int = 200,
    format: str = None,
    request: Request = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    project_path = request.query_params.get("project_path") if request else None
    logs = docker_svc.get_logs(server, container, lines=lines, project_path=project_path)

    is_json = (format == "json") or (request and "application/json" in (request.headers.get("accept") or "").lower())
    if is_json:
        return JSONResponse({"container": container, "logs": logs})

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="docker_logs.html",
        context={
            "title": f"Logs - {container}",
            "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}),
            "container": container,
            "logs": logs,
            "lines": lines,
            "user": user
        }
    )


@router.get("/{server_id}/docker/containers-fragment", response_class=HTMLResponse)
async def containers_fragment(server_id: int, request: Request, session: Session = Depends(get_session), user: User = Depends(get_current_user)):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        import app.services.docker_management as _dm
        if request.query_params.get("nocache"):
            _dm._CACHE.clear()
    except:
        pass

    try:
        interval = max(60, int(request.query_params.get("refresh", "120")))
    except:
        interval = 120

    try:
        containers = docker_svc.list_containers(server)
    except Exception as e:
        containers = [{"name": "error", "status": str(e), "state": "error", "image": "", "version": "", "ports_display": "", "running": False}]

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="docker_containers_table.html",
        context={"server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}), "containers": containers, "refresh": interval}
    )


@router.post("/{server_id}/docker/check-updates")
async def check_updates(
    server_id: int,
    project_path: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    result = docker_svc.check_compose_updates(server, project_path)
    try:
        audit = AuditLog(
            user_id=user.id if user else None,
            server_id=server_id,
            action="docker_check-updates",
            status="success",
            details=f"Project {project_path}",
            output_snippet=str(result)[:300],
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        session.add(audit)
        session.commit()
    except Exception:
        pass
    return RedirectResponse(f"/servers/{server_id}/docker?update_check={project_path}&status={status}", status_code=303)


@router.get("/{server_id}/docker/logs/{container}/stream")
async def stream_container_logs(server_id: int, container: str, lines: int = 30, project_path: str = None, session: Session = Depends(get_session)):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        docker_svc.stream_logs(server, container, lines=lines, project_path=project_path),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )

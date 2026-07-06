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
import time
import logging

router = APIRouter()

# Mount sub-routers (keep paths unchanged)
from .server_docker import router as docker_router
from .server_backups import router as backups_router
router.include_router(docker_router, prefix="")
router.include_router(backups_router, prefix="")
logger = logging.getLogger("piherder.servers")


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


# (backup routes moved to server_backups.py sub-router)


# (run_backup / stop_backup moved to server_backups.py)


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


# (backup progress + logs stream moved to server_backups.py)

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


# Docker routes extracted to server_docker.py (sub-router included at top of file)


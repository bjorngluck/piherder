from fastapi import APIRouter, Depends, Form, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from sqlmodel import Session, select
from sqlalchemy import func
import json
from typing import Optional, List
from starlette.concurrency import run_in_threadpool
from ..database import get_session, ensure_server_columns
from ..models import Server, AuditLog
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


@router.get("", response_class=HTMLResponse)
async def list_servers(request: Request, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    start = time.time()

    try:
        ensure_server_columns()
        rows = session.exec(select(Server).order_by(Server.sort_order, Server.name)).all()
    except Exception:
        # Fallback if column not present yet or DB issue
        rows = session.exec(select(Server).order_by(Server.name)).all()

    servers = []
    for row in rows:
        d = row.model_dump(exclude={"audit_logs", "jobs"})
        try:
            t0 = time.time()
            profs = backup_svc.get_backup_profiles(row)
            took = time.time() - t0
            if took > 0.5:
                logger.warning(f"[list_servers] get_backup_profiles for {row.hostname} took {took:.2f}s")

            times = [p["last_backup"] for p in profs if p.get("last_backup")]
            if times:
                d["last_backup"] = format_datetime_in_app_tz(max(times), "%Y-%m-%d")
        except Exception as e:
            logger.warning(f"[list_servers] get_backup_profiles failed for {row.hostname}: {e}")

        servers.append(d)

    total = time.time() - start
    if total > 1.0:
        logger.warning(f"[list_servers] Total render took {total:.2f}s for {len(servers)} server(s)")
    else:
        logger.debug(f"[list_servers] Total render took {total:.2f}s")

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_list.html",
        context={"title": "Servers", "servers": servers, "user": user}
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
        ensure_server_columns()
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
    # re-assign dense sort_order so manual order is preserved
    for i, s in enumerate(servers):
        s.sort_order = i * 10
        session.add(s)
    session.commit()
    return RedirectResponse("/servers", status_code=303)


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

    # set a high sort_order so new servers appear at bottom, manual reorder can move them
    ensure_server_columns()
    max_order = session.exec(select(func.max(Server.sort_order))).first()
    max_order = max_order[0] if max_order and max_order[0] is not None else 0
    server = Server(
        name=name,
        hostname=hostname,
        ssh_username=ssh_username,
        ssh_port=ssh_port,
        ssh_private_key_encrypted=priv_enc,
        ssh_public_key=pub,
        ssh_password_encrypted=pw_enc,
        sort_order = (max_order or 0) + 10,
    )
    session.add(server)
    session.commit()
    session.refresh(server)

    # After adding (especially with generated key), redirect with flag so we can auto-show
    # the SSH public key modal. This is the critical one-time copy step for the user.
    redirect_url = f"/servers/{server.id}"
    if key_mode == "generate":
        redirect_url += "?show_ssh_key=1"
    return RedirectResponse(redirect_url, status_code=303)


@router.get("/{server_id}", response_class=HTMLResponse)
async def server_detail(server_id: int, request: Request, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    ensure_server_columns()
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    server_dict = server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"})

    reboot_initiated = request.query_params.get("rebooted") == "1"
    show_ssh_key = request.query_params.get("show_ssh_key") == "1"

    # Safe defaults (in case of any error below)
    backup_profiles = []
    overall_last_backup = None
    last_backup_status = None
    recent_backups = []
    global_backup_defaults = {}
    current_sources = []
    diagnostics = {"error": "n/a"}

    try:
        backup_profiles = backup_svc.get_backup_profiles(server)
        last_backup_times = [p["last_backup"] for p in backup_profiles if p.get("last_backup")]
        overall_last_backup = max(last_backup_times) if last_backup_times else None

        # Get last backup status from AuditLog
        last_backup_log = session.exec(
            select(AuditLog)
            .where(AuditLog.server_id == server.id, AuditLog.action == "backup")
            .order_by(AuditLog.started_at.desc())
            .limit(1)
        ).first()
        last_backup_status = last_backup_log.status if last_backup_log else None

        # Recent backup history (last 10)
        recent_backups = session.exec(
            select(AuditLog)
            .where(AuditLog.server_id == server.id, AuditLog.action == "backup")
            .order_by(AuditLog.started_at.desc())
            .limit(10)
        ).all()

        for log in recent_backups:
            # Use object.__setattr__ because AuditLog (SQLModel/Pydantic) forbids arbitrary extra fields via normal setattr
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
                        "success_count": sum(1 for r in results if r.get("rc", 0) == 0 or r.get("skipped")),
                        "total_size": total_bytes,
                        "total_size_human": backup_svc.human_size(total_bytes),
                    })
                    object.__setattr__(log, 'full_json', json.dumps(data, indent=2))
                except Exception:
                    pass

        global_backup_defaults = backup_svc.get_global_backup_defaults()
        current_sources = [p.get("source") for p in backup_profiles] or global_backup_defaults

        # Fetch live system info (OS, kernel, disks, reboot status).
        # Run in threadpool so a slow/unreachable host does not block the web server
        # for other requests (was a source of "PiHerder becomes unresponsive").
        try:
            diagnostics = await run_in_threadpool(diag_svc.run_diagnostics, server)
        except Exception:
            diagnostics = {"error": "Could not fetch diagnostics"}
    except Exception:
        # Keep the safe defaults set above; don't crash the page
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
            "user": user,
            "settings": settings,
            "global_backup_defaults": global_backup_defaults,
            "current_sources": current_sources,
            "diagnostics": diagnostics,
            "reboot_initiated": reboot_initiated,
            "show_ssh_key": show_ssh_key,
        }
    )


@router.get("/{server_id}/backups", response_class=HTMLResponse)
async def server_backups(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    """Dedicated page for all backup details: sources, config, add/remove, schedules, runs.
    Main server screen now only shows compact status + link here.
    """
    ensure_server_columns()
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    server_dict = server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"})

    backup_profiles = []
    last_backup_status = None
    global_backup_defaults = {}
    current_sources = []

    try:
        backup_profiles = backup_svc.get_backup_profiles(server)

        last_backup_log = session.exec(
            select(AuditLog)
            .where(AuditLog.server_id == server.id, AuditLog.action == "backup")
            .order_by(AuditLog.started_at.desc())
            .limit(1)
        ).first()
        last_backup_status = last_backup_log.status if last_backup_log else None

        global_backup_defaults = backup_svc.get_global_backup_defaults()
        current_sources = [p.get("source") for p in backup_profiles] or global_backup_defaults
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
        }
    )


@router.post("/{server_id}/run/backup")
async def run_backup(
    server_id: int,
    background_tasks: BackgroundTasks,
    source: Optional[str] = Form(None),  # optional: run only this source
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    # Guard against duplicate submits while one is active (lock inside run_backup would serialize,
    # but queuing extra jobs can make the UI feel stuck/"failed to submit").
    if backup_svc.is_backup_running(server.hostname):
        # Let the UI banner + polling handle it; just redirect back.
        return RedirectResponse(f"/servers/{server_id}", status_code=303)

    # If specific source, temporarily filter for this job run (DB not mutated)
    if source:
        job_service.create_job_and_run(background_tasks, session, server, "backup", user_id=user.id, source_filter=source)
    else:
        job_service.create_job_and_run(background_tasks, session, server, "backup", user_id=user.id)

    return RedirectResponse(f"/servers/{server_id}", status_code=303)


@router.post("/{server_id}/backup/stop")
async def stop_backup(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    backup_svc.stop_backup(server.hostname)
    # log the stop
    audit = AuditLog(
        user_id=user.id,
        server_id=server.id,
        action="backup_stop",
        status="success",
        details="Backup stopped by user",
    )
    session.add(audit)
    session.commit()
    return RedirectResponse(f"/servers/{server_id}", status_code=303)


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
            # Fire the reboot. Connection will drop as the host restarts.
            client.exec_command("sudo reboot", timeout=3)
        except Exception:
            # Expected: the SSH session is terminated by the reboot.
            pass
        try:
            client.close()
        except Exception:
            pass
        success = True
    except Exception as e:
        details = f"Reboot command failed to send: {e}"

    # Audit the attempt
    try:
        audit = AuditLog(
            user_id=user.id,
            server_id=server.id,
            action="reboot",
            status="success" if success else "failed",
            details=details,
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        session.add(audit)
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

    server.name = name.strip()
    server.hostname = hostname.strip()
    server.ssh_username = ssh_username.strip()
    server.ssh_port = ssh_port

    # Handle password (discouraged but supported for legacy)
    if clear_password:
        server.ssh_password_encrypted = None
    elif ssh_password and ssh_password.strip():
        try:
            server.ssh_password_encrypted = encryption.encrypt_str(ssh_password.strip())
        except Exception as e:
            raise HTTPException(500, f"Failed to encrypt password: {e}")

    server.backup_enabled = backup_enabled
    server.container_patch_enabled = container_patch_enabled
    server.os_patch_enabled = os_patch_enabled

    session.add(server)
    session.commit()

    return RedirectResponse(f"/servers/{server_id}", status_code=303)


@router.get("/{server_id}/backup-progress", response_class=JSONResponse)
async def get_backup_progress(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    prog = backup_svc.get_backup_progress(server.hostname)
    return {
        "current": prog.get("current"),
        "log_lines": prog.get("log_lines", [])[-15:],
        "last_updated": prog.get("last_updated"),
        "hostname": server.hostname
    }


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
            # Calmer sleep for stability (was 1s). Frontend should poll every 2-3s.
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

    # Important: no buffering for live logs behind Caddy/nginx
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
    """On-demand diagnostics for modals / refresh buttons.
    Supports ?force=1 to bypass cache.
    """
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        data = await run_in_threadpool(diag_svc.run_diagnostics, server, bool(force))
        return data
    except Exception as e:
        return {"error": str(e)[:200], "hostname": server.hostname}


@router.post("/{server_id}/run/container_patch")
async def run_container_patch(server_id: int, background_tasks: BackgroundTasks, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    job_service.create_job_and_run(background_tasks, session, server, "container_patch", user_id=user.id)
    return RedirectResponse(f"/servers/{server_id}", status_code=303)


@router.post("/{server_id}/run/os_patch")
async def run_os_patch(server_id: int, background_tasks: BackgroundTasks, steps: list[str] = Form([]), session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    if not steps:
        steps = ["update", "upgrade", "autoremove"]
    job_service.create_job_and_run(background_tasks, session, server, "os_patch", user_id=user.id, os_steps=steps)
    return RedirectResponse(f"/servers/{server_id}", status_code=303)


# --- Backup configuration flexibility ---

@router.post("/{server_id}/backup-config")
async def update_backup_config(
    server_id: int,
    backup_paths: str = Form(""),          # kept for bulk compat (rare)
    retention_days: int = Form(None),
    backup_schedule: str = Form(None),
    dest_root: str = Form(""),
    folder_name: str = Form(""),
    scope: str = Form("this_host"),  # "this_host" or "global"
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    # Validate cron if provided
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
        # Save as global defaults (new servers will use these)
        paths = [p.strip() for p in backup_paths.replace(",", "\n").splitlines() if p.strip()] or server.get_backup_paths()
        global_config = {
            "sources": paths,
            "dest_root": dest_root.strip() or None,
            "folder_name": folder_name.strip() or None,
        }
        backup_svc.save_global_backup_defaults(global_config)

        # Also apply the dest config + paths to current host
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
        session.commit()
        return RedirectResponse(f"/servers/{server_id}/backups", status_code=303)

    # Per host (default)
    updated = False
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
        updated = True

    if dest_root.strip():
        server.backup_dest_root = dest_root.strip()
        updated = True
    if folder_name.strip():
        server.backup_folder_name = folder_name.strip()
        updated = True

    if retention_days is not None:
        server.retention_days = retention_days
        updated = True

    if backup_schedule is not None:
        server.backup_schedule = backup_schedule
        if backup_schedule:
            server.backup_enabled = True
        updated = True

    if updated:
        session.add(server)
        session.commit()

    return RedirectResponse(f"/servers/{server_id}", status_code=303)


# More flexible per-path management
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
    backup_svc.add_backup_source(server, new_path, dn, session)
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
    return RedirectResponse(f"/servers/{server_id}/backups", status_code=303)



@router.post("/{server_id}/run/retention")
async def run_retention(server_id: int, background_tasks: BackgroundTasks, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    job_service.create_job_and_run(background_tasks, session, server, "retention", user_id=user.id)
    return RedirectResponse(f"/servers/{server_id}", status_code=303)


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

    # Simple feedback from update check or build
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
    # Audit UI action
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
    file: str = "compose",  # "compose" or "dockerfile"
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
        # compose
        live_files = docker_svc.get_project_live_files(server, proj["path"])
        for key in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            if key in live_files:
                return {"ok": True, "file": key, "content": live_files[key]}
        # fallback to first available or empty
        content = next(iter(live_files.values()), "") if live_files else ""
        return {"ok": True, "file": "docker-compose.yml", "content": content}


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

    # Find project path
    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj:
        raise HTTPException(404)

    # Load live from host (short session) + drafts from DB
    live_files = docker_svc.get_project_live_files(server, proj["path"])
    live_compose = live_files.get("docker-compose.yml") or live_files.get("docker-compose.yaml") or live_files.get("compose.yml") or live_files.get("compose.yaml") or ""
    content = live_compose
    drafts = docker_svc.get_versions(server.id, project, limit=10)

    # Load specific draft if requested (for editing a previous draft)
    load_draft_id = request.query_params.get("load_draft")
    editing_version_id = None
    if load_draft_id:
        try:
            dv = next((d for d in drafts if str(d.id) == load_draft_id), None)
            if dv:
                f = json.loads(dv.files)
                content = f.get("docker-compose.yml") or f.get("compose.yml") or list(f.values())[0] if f else content
                if dv.is_draft:
                    editing_version_id = dv.id
        except:
            pass

    # Find if live matches a deployed version
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

    # Support passing errors via query (simple json encoded) or empty
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
            "project": proj,
            "content": content,
            "user": user,
            "errors": errors,
            "is_dockerfile": False,
            "live_files": live_files,
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
    # filter to dockerfile versions for this edit
    drafts = []
    for d in all_drafts:
        try:
            f = json.loads(d.files or '{}')
            if 'Dockerfile' in f:
                drafts.append(d)
        except:
            pass

    # Load specific draft if requested
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

    # live version match for df (always against what is actually on host)
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
        name="docker_compose_edit.html",  # reuse the nice editor UI
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

    # Dockerfile editing temporarily disabled (per request to deprioritize)
    # We will bring this back later in a clean, isolated module.
    # For now we short-circuit to a clear message.

    if via_modal:
        return JSONResponse({"ok": False, "message": "Dockerfile editing is temporarily disabled."})

    return RedirectResponse(
        f"/servers/{server_id}/docker/compose/{project}/dockerfile/edit?disabled=1",
        status_code=303
    )


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
        name="new_docker_project.html",  # we'll create simple template or reuse
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
        # initial deploy
        try:
            full = f"{server.docker_base_dir.replace('~', f'/home/{server.ssh_username}')}/{project_name}"
            docker_svc.redeploy_project(server, full, pull=True)
        except:
            pass

    # create initial version record
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


# Versioning / drafts endpoints
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
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    # For simplicity version the main compose; extend to multi-file dict later
    files = {"docker-compose.yml": content}
    # If editing an existing draft (from ?load_draft), update in place.
    # If live (or none), this will create a new draft (live records are protected).
    dv = docker_svc.save_draft_version(server.id, project, files, session, update_existing_draft_id=editing_version_id)

    if via_modal:
        return JSONResponse({"ok": True, "saved_draft": dv.version, "id": dv.id, "message": f"Draft v{dv.version} saved."])

    # redirect back to edit, load the (new or updated) draft for editing
    return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/edit?load_draft={dv.id}&saved_draft={dv.version}", status_code=303)


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
    # FastAPI injects Request for handlers even with default=None (common pattern)
    pass  # request is used below for TemplateResponse on validation failure
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj:
        raise HTTPException(404)

    validation = docker_svc.validate_compose_content(content)
    if not validation.get("valid"):
        errs = sorted(validation.get("errors", []), key=lambda e: e.get("line", 0))
        if via_modal:
            return JSONResponse({"ok": False, "errors": errs, "message": "Validation failed."])
        # reload versions for the bar on validation error re-render
        try:
            err_drafts = docker_svc.get_versions(server.id, project, limit=10)
        except:
            err_drafts = []
        # Re-render editor with errors and the submitted content
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="docker_compose_edit.html",
            context={
                "title": f"Edit {project}",
                "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}),
                "project": proj,
                "content": content,
                "user": user,
                "errors": errs,
                "drafts": err_drafts,
                "is_dockerfile": False,
                "editing_version_id": editing_version_id,
            }
        )

    docker_svc.write_compose_file(server, proj["path"], content)
    # record as deployed version
    try:
        from datetime import datetime as dt
        files = {"docker-compose.yml": content}
        dv = docker_svc.save_draft_version(server.id, project, files, session)
        dv.is_draft = False
        dv.deployed_at = dt.utcnow()
        session.add(dv)
        session.commit()
    except:
        pass

    if via_modal:
        return JSONResponse({"ok": True, "deployed": True, "version": dv.version, "message": f"Deployed as v{dv.version}."])

    return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/edit?saved=1&version={dv.version}", status_code=303)


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
    # Audit UI action
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


@router.post("/{server_id}/docker/compose/{project}/build")
async def build_compose(
    server_id: int,
    project: str,
    services: List[str] = Form([]),
    no_cache: str = Form(None),
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

    svc_list = [s for s in (services or []) if s]
    if not svc_list:
        svc_list = None  # build all

    # Redirect to streaming progress page instead of sync build
    params = f"project={project}"
    if svc_list:
        params += f"&services={','.join(svc_list)}"
    if no_cache == "on" or no_cache == "true":
        params += "&no_cache=true"
    # Audit UI action (build starts)
    try:
        audit = AuditLog(
            user_id=user.id if user else None,
            server_id=server_id,
            action="docker_build",
            status="running",
            details=f"Project {project} services={svc_list or 'all'}",
            started_at=datetime.utcnow(),
        )
        session.add(audit)
        session.commit()
    except Exception:
        pass
    return RedirectResponse(f"/servers/{server_id}/docker/build-progress?{params}", status_code=303)


@router.get("/{server_id}/docker/build-progress", response_class=HTMLResponse)
async def build_progress(
    server_id: int,
    project: str,
    services: str = "",
    no_cache: bool = False,
    request: Request = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    svc_list = services.split(",") if services else []
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="docker_build_progress.html",
        context={
            "title": f"Build - {project}",
            "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}),
            "user": user,
            "project": project,
            "services": svc_list,
            "no_cache": no_cache,
            "server_id": server_id
        }
    )


@router.get("/{server_id}/docker/build-stream")
async def build_stream(
    server_id: int,
    project: str,
    services: str = "",
    no_cache: bool = False,
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
    svc_list = [s for s in services.split(",") if s] if services else None
    return StreamingResponse(
        docker_svc.stream_compose_build(server, proj["path"], svc_list, no_cache),
        media_type="text/event-stream"
    )


@router.get("/{server_id}/docker/unused", response_class=HTMLResponse)
async def list_unused(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    data = docker_svc.list_unused_images_and_containers(server)
    html = "<div class='text-xs'>"
    html += "<b>Dangling images (prunable):</b><br>" + ("<br>".join(data.get("dangling_images", [])) or "none") + "<br><br>"
    html += "<b>Exited containers (prunable):</b><br>" + ("<br>".join(data.get("exited_containers", [])) or "none")
    html += "</div>"
    return HTMLResponse(html)


@router.post("/{server_id}/docker/prune-unused")
async def prune_unused_route(
    server_id: int,
    prune_type: str = Form("both"),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    res = docker_svc.prune_unused(server, prune_type=prune_type)
    # Audit UI action
    try:
        audit = AuditLog(
            user_id=user.id if user else None,
            server_id=server_id,
            action=f"docker_prune_{prune_type}",
            status="success" if res.get("success") else "failed",
            details="Prune unused",
            output_snippet=str(res)[:300],
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        session.add(audit)
        session.commit()
    except Exception:
        pass
    return RedirectResponse(f"/servers/{server_id}/docker?prune={ 'ok' if res.get('success') else 'fail' }&prune_type={prune_type}", status_code=303)


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
    # Audit UI action
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

    # Return JSON for API (used by logs modal), HTML for browser
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


# --- HTMX partials and new features ---

@router.get("/{server_id}/docker/containers-fragment", response_class=HTMLResponse)
async def containers_fragment(server_id: int, request: Request, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    """HTMX fragment for auto-refreshing container list. Min 60s to limit SSH load."""
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
    # Audit UI action
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
    # For now, just redirect back with flash via query (simple)
    status = "updates" if result.get("has_updates") else "up-to-date"
    return RedirectResponse(f"/servers/{server_id}/docker?update_check={project_path}&status={status}", status_code=303)


@router.get("/{server_id}/docker/logs/{container}/stream")
async def stream_container_logs(server_id: int, container: str, lines: int = 30, project_path: str = None, session: Session = Depends(get_session)):
    """Server-Sent Events endpoint for live logs."""
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

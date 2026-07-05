from fastapi import APIRouter, Depends, Form, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from sqlmodel import Session, select
from sqlalchemy import func
import json
from typing import Optional, List
from starlette.concurrency import run_in_threadpool
from ..database import get_session, ensure_server_columns
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
    rows = session.exec(select(Server).order_by(Server.sort_order, Server.name)).all()
    servers = []
    for row in rows:
        d = row.model_dump(exclude={"audit_logs", "jobs"})
        if row.last_backup_at:
            d["last_backup_str"] = format_datetime_in_app_tz(row.last_backup_at)
        servers.append(d)

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_list.html",
        context={"title": "Servers", "servers": servers, "user": user}
    )


@router.get("/{server_id}", response_class=HTMLResponse)
async def server_detail(request: Request, server_id: int, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_detail.html",
        context={"title": server.name, "server": server, "user": user}
    )


@router.get("/{server_id}/backups", response_class=HTMLResponse)
async def server_backups(request: Request, server_id: int, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    # TODO: load backup sources from server.get_backup_sources() or DB
    sources = []
    try:
        sources = server.get_backup_sources() if hasattr(server, 'get_backup_sources') else []
    except Exception:
        sources = []
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_backups.html",
        context={"title": f"Backups - {server.name}", "server": server, "sources": sources, "user": user}
    )


@router.get("/{server_id}/backup-progress", response_class=JSONResponse)
async def get_backup_progress(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    latest_job = session.exec(
        select(Job)
        .where(Job.server_id == server.id, Job.job_type == "backup")
        .order_by(Job.started_at.desc())
        .limit(1)
    ).first()

    prog = backup_svc.get_backup_progress(server.hostname)

    data = {
        "current": prog.get("current"),
        "log_lines": prog.get("log_lines", [])[-15:],
        "last_updated": prog.get("last_updated"),
        "hostname": server.hostname
    }

    if latest_job:
        data["job_status"] = latest_job.status
        try:
            if latest_job.details:
                job_details = json.loads(latest_job.details)
                if job_details.get("current"):
                    data["current"] = job_details["current"]
        except Exception:
            pass

    return data

# Note: Full backup source add/edit/delete routes and other functions are expected in the complete file.

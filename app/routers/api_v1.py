"""Token-authenticated REST API for automation (n8n, HA, scripts).

Auth: Authorization: Bearer ph_…
Scopes: read | jobs
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ..database import get_session
from ..models import ApiToken, Job, Server, User
from ..security.auth import get_admin_user
from ..services import api_tokens as tok_svc
from ..services import jobs as job_service
from ..services import os_patching

router = APIRouter()

ALLOWED_JOB_TYPES = frozenset(
    {
        "backup",
        "retention",
        "os_patch",
        "container_patch",
        "os_update_check",
        "container_update_check",
    }
)


class ApiAuth:
    """Resolved API token + optional acting user id for audit."""

    def __init__(self, token: ApiToken):
        self.token = token
        self.user_id = token.created_by_user_id
        self.scopes = tok_svc.parse_scopes(token.scopes)

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API token missing scope: {scope}",
            )


def get_api_auth(
    request: Request,
    session: Session = Depends(get_session),
    authorization: Optional[str] = Header(None),
) -> ApiAuth:
    raw = authorization or ""
    if raw.lower().startswith("bearer "):
        plain = raw.split(" ", 1)[1].strip()
    else:
        # Also accept raw token in Authorization without Bearer (some n8n setups)
        plain = raw.strip() if raw.startswith(tok_svc.TOKEN_PREFIX) else ""
    if not plain:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization: Bearer ph_…",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = tok_svc.verify_bearer_token(session, plain)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return ApiAuth(token)


def _server_public(s: Server) -> dict[str, Any]:
    inv_status = getattr(s, "docker_inventory_status", None) or "never"
    return {
        "id": s.id,
        "name": s.name,
        "hostname": s.hostname,
        "ip_address": s.ip_address,
        "ssh_port": s.ssh_port,
        "ssh_username": s.ssh_username,
        "os_type": s.os_type,
        "last_seen": s.last_seen.isoformat() if s.last_seen else None,
        "features": {
            "backup": bool(s.backup_enabled),
            "os_patch": bool(s.os_patch_enabled),
            "docker": bool(s.container_patch_enabled),
        },
        "last_backup_at": s.last_backup_at.isoformat() if s.last_backup_at else None,
        "os_updates_count": s.os_updates_count,
        "container_updates_count": s.container_updates_count,
        "reboot_pending": bool(s.reboot_pending),
        "last_os_check_at": s.last_os_check_at.isoformat() if s.last_os_check_at else None,
        "last_container_check_at": (
            s.last_container_check_at.isoformat() if s.last_container_check_at else None
        ),
        "docker_inventory_status": inv_status,
        "docker_inventory_at": (
            s.docker_inventory_at.isoformat() if s.docker_inventory_at else None
        ),
    }


# ---------- Fleet read ----------


@router.get("/health")
def api_health(auth: ApiAuth = Depends(get_api_auth)):
    auth.require("read")
    return {"ok": True, "scopes": sorted(auth.scopes)}


@router.get("/servers")
def list_servers(
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    auth.require("read")
    rows = list(session.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
    return {"servers": [_server_public(s) for s in rows]}


@router.get("/servers/{server_id}")
def get_server(
    server_id: int,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    auth.require("read")
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404, detail="Server not found")
    return _server_public(server)


@router.get("/servers/{server_id}/jobs")
def list_server_jobs(
    server_id: int,
    limit: int = 25,
    status_filter: Optional[str] = None,
    job_type: Optional[str] = None,
    active_only: bool = False,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    auth.require("read")
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404, detail="Server not found")
    jobs = job_service.list_jobs_for_server(
        session,
        server_id,
        limit=limit,
        status=status_filter,
        job_type=job_type,
        active_only=active_only,
    )
    return {
        "server_id": server_id,
        "jobs": [job_service.job_public_dict(j) for j in jobs],
    }


@router.get("/jobs/{job_id}")
def get_job(
    job_id: int,
    detail: bool = False,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    auth.require("read")
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    return job_service.job_public_dict(job, detail=detail)


@router.get("/jobs")
def list_jobs(
    server_id: Optional[int] = None,
    status_filter: Optional[str] = None,
    job_type: Optional[str] = None,
    active_only: bool = False,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    auth.require("read")
    jobs = job_service.list_jobs(
        session,
        server_id=server_id,
        status=status_filter,
        job_type=job_type,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )
    return {"jobs": [job_service.job_public_dict(j) for j in jobs]}


# ---------- Job triggers ----------


class JobCreateBody(BaseModel):
    job_type: str = Field(..., description="backup | retention | os_patch | container_patch | os_update_check | container_update_check")
    source_filter: Optional[str] = None
    os_steps: Optional[list[str]] = None


@router.post("/servers/{server_id}/jobs", status_code=202)
async def create_server_job(
    server_id: int,
    body: JobCreateBody,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    auth.require("jobs")
    job_type = (body.job_type or "").strip().lower()
    if job_type not in ALLOWED_JOB_TYPES:
        raise HTTPException(
            400,
            detail=f"Unsupported job_type. Allowed: {sorted(ALLOWED_JOB_TYPES)}",
        )
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404, detail="Server not found")

    # Feature gates (mirror UI intent)
    if job_type in ("backup", "retention") and not server.backup_enabled:
        raise HTTPException(400, detail="Backups feature is disabled for this server")
    if job_type in ("os_patch", "os_update_check") and not server.os_patch_enabled:
        raise HTTPException(400, detail="OS patch feature is disabled for this server")
    if job_type in ("container_patch", "container_update_check") and not server.container_patch_enabled:
        raise HTTPException(400, detail="Docker / containers feature is disabled for this server")

    os_steps = None
    if job_type == "os_patch":
        os_steps = os_patching.normalize_os_patch_steps(body.os_steps or None)
        if not os_steps:
            os_steps = ["update", "upgrade", "autoremove"]

    try:
        job = job_service.create_job_and_run(
            background_tasks,
            session,
            server,
            job_type,
            user_id=auth.user_id,
            source_filter=body.source_filter,
            os_steps=os_steps,
        )
    except job_service.BackupAlreadyRunning as e:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Backup already running",
                "job": job_service.job_public_dict(e.job),
            },
        )
    except RuntimeError as e:
        raise HTTPException(503, detail=str(e)[:200]) from e

    return JSONResponse(
        status_code=202,
        content={
            "job_id": job.id,
            "status": job.status,
            "job_type": job.job_type,
            "job": job_service.job_public_dict(job),
        },
    )


# ---------- Token admin (session cookie / JWT, admin only) ----------


class TokenCreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    scopes: Optional[list[str]] = None


@router.get("/tokens")
def admin_list_tokens(
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    rows = tok_svc.list_api_tokens(session, include_revoked=True)
    return {"tokens": [tok_svc.token_public_dict(t) for t in rows]}


@router.post("/tokens", status_code=201)
def admin_create_token(
    body: TokenCreateBody,
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    row, plain = tok_svc.create_api_token(
        session,
        name=body.name,
        created_by=user,
        scopes=body.scopes,
    )
    return {
        "token": tok_svc.token_public_dict(row),
        "secret": plain,  # shown once
        "warning": "Store this secret now; it cannot be retrieved again.",
    }


@router.delete("/tokens/{token_id}")
def admin_revoke_token(
    token_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    row = tok_svc.get_api_token(session, token_id)
    if not row:
        raise HTTPException(404, detail="Token not found")
    tok_svc.revoke_api_token(session, row)
    return {"ok": True, "token": tok_svc.token_public_dict(row)}

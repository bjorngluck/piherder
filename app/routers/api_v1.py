"""Token-authenticated REST API for automation (n8n, HA, scripts).

Auth: Authorization: Bearer ph_…
Admin-managed instance tokens. See docs/API.md and GET /api/v1.
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

ALLOWED_JOB_TYPES = frozenset(tok_svc.JOB_FEATURE_KEY.keys())


class ApiAuth:
    """Resolved API token + optional acting user id for audit."""

    def __init__(self, token: ApiToken, client_ip: str | None = None):
        self.token = token
        self.user_id = token.created_by_user_id
        self.scopes = tok_svc.parse_scopes(token.scopes)
        self.client_ip = client_ip

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API token missing scope: {scope}",
            )

    def require_feature(self, feature_key: str) -> None:
        if not tok_svc.token_allows_feature(self.scopes, feature_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API token not allowed for feature: {feature_key}",
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
        plain = raw.strip() if raw.startswith(tok_svc.TOKEN_PREFIX) else ""
    if not plain:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization: Bearer ph_…",
            headers={"WWW-Authenticate": "Bearer"},
        )
    peer = request.client.host if request.client else None
    client_ip = tok_svc.extract_client_ip(dict(request.headers), peer)
    token = tok_svc.lookup_active_token(session, plain)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    cidrs = tok_svc.parse_allowed_cidrs(getattr(token, "allowed_cidrs", None))
    if cidrs and not tok_svc.client_ip_allowed(cidrs, client_ip):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client IP not allowed for this API token",
        )
    tok_svc.touch_token_last_used(session, token)
    return ApiAuth(token, client_ip=client_ip)


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


# ---------- Meta / health ----------


@router.get(
    "",
    summary="API catalog",
    description="Machine-readable scope and endpoint catalog (requires read).",
)
def api_root(auth: ApiAuth = Depends(get_api_auth)):
    auth.require(tok_svc.SCOPE_READ)
    meta = tok_svc.api_meta_dict()
    feat = tok_svc.feature_keys_allowed(auth.scopes)
    meta["token"] = {
        "scopes": sorted(auth.scopes),
        "allowed_features": sorted(feat) if feat is not None else None,
        "client_ip": auth.client_ip,
    }
    return meta


@router.get("/health", summary="Token health check")
def api_health(auth: ApiAuth = Depends(get_api_auth)):
    auth.require(tok_svc.SCOPE_READ)
    feat = tok_svc.feature_keys_allowed(auth.scopes)
    return {
        "ok": True,
        "scopes": sorted(auth.scopes),
        "allowed_features": sorted(feat) if feat is not None else None,
        "client_ip": auth.client_ip,
    }


# ---------- Fleet read ----------


@router.get("/servers", summary="List servers")
def list_servers(
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    auth.require(tok_svc.SCOPE_READ)
    rows = list(session.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
    return {"servers": [_server_public(s) for s in rows]}


@router.get("/servers/{server_id}", summary="Get server")
def get_server(
    server_id: int,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    auth.require(tok_svc.SCOPE_READ)
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404, detail="Server not found")
    return _server_public(server)


class ServerFeaturesBody(BaseModel):
    """Toggle server feature flags. Omitted fields are left unchanged."""

    backup: Optional[bool] = Field(None, description="Enable backups feature")
    os_patch: Optional[bool] = Field(None, description="Enable OS patch feature")
    docker: Optional[bool] = Field(None, description="Enable Docker / containers feature")


@router.patch(
    "/servers/{server_id}/features",
    summary="Update server feature flags",
    description="Requires scope `edit`. Optional feature:* scopes further restrict which flags may change.",
)
def patch_server_features(
    server_id: int,
    body: ServerFeaturesBody,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    auth.require(tok_svc.SCOPE_EDIT)
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404, detail="Server not found")

    changed: dict[str, bool] = {}
    if body.backup is not None:
        auth.require_feature("backup")
        server.backup_enabled = bool(body.backup)
        changed["backup"] = server.backup_enabled
    if body.os_patch is not None:
        auth.require_feature("os")
        server.os_patch_enabled = bool(body.os_patch)
        changed["os_patch"] = server.os_patch_enabled
    if body.docker is not None:
        auth.require_feature("docker")
        server.container_patch_enabled = bool(body.docker)
        changed["docker"] = server.container_patch_enabled

    if not changed:
        raise HTTPException(400, detail="No feature fields provided (backup, os_patch, docker)")

    session.add(server)
    session.commit()
    session.refresh(server)
    return {"ok": True, "changed": changed, "server": _server_public(server)}


@router.get("/servers/{server_id}/jobs", summary="List jobs for a server")
def list_server_jobs(
    server_id: int,
    limit: int = 25,
    status_filter: Optional[str] = None,
    job_type: Optional[str] = None,
    active_only: bool = False,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    auth.require(tok_svc.SCOPE_READ)
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


@router.get("/jobs/{job_id}", summary="Get job")
def get_job(
    job_id: int,
    detail: bool = False,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    auth.require(tok_svc.SCOPE_READ)
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    return job_service.job_public_dict(job, detail=detail)


@router.get("/jobs", summary="List fleet jobs")
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
    auth.require(tok_svc.SCOPE_READ)
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
    job_type: str = Field(
        ...,
        description="backup | retention | os_patch | container_patch | os_update_check | container_update_check",
    )
    source_filter: Optional[str] = None
    os_steps: Optional[list[str]] = None


@router.post(
    "/servers/{server_id}/jobs",
    status_code=202,
    summary="Trigger a job",
    description="Requires scope `jobs` and matching feature:* if the token is feature-restricted.",
)
async def create_server_job(
    server_id: int,
    body: JobCreateBody,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    auth.require(tok_svc.SCOPE_JOBS)
    job_type = (body.job_type or "").strip().lower()
    if job_type not in ALLOWED_JOB_TYPES:
        raise HTTPException(
            400,
            detail=f"Unsupported job_type. Allowed: {sorted(ALLOWED_JOB_TYPES)}",
        )
    feature_key = tok_svc.JOB_FEATURE_KEY[job_type]
    auth.require_feature(feature_key)

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404, detail="Server not found")

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
    scopes: Optional[list[str]] = Field(
        None,
        description="read, jobs, edit, feature:backup, feature:os, feature:docker",
    )
    allowed_cidrs: Optional[list[str]] = Field(
        None,
        description="Optional IP/CIDR allowlist, e.g. [\"10.0.0.0/8\", \"192.168.1.10\"]",
    )


@router.get("/tokens", summary="List API tokens (admin session)")
def admin_list_tokens(
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    rows = tok_svc.list_api_tokens(session, include_revoked=True)
    return {"tokens": [tok_svc.token_public_dict(t) for t in rows]}


@router.post("/tokens", status_code=201, summary="Create API token (admin session)")
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
        allowed_cidrs=body.allowed_cidrs,
    )
    return {
        "token": tok_svc.token_public_dict(row),
        "secret": plain,
        "warning": "Store this secret now; it cannot be retrieved again.",
    }


@router.delete("/tokens/{token_id}", summary="Revoke API token (admin session)")
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

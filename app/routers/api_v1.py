"""Token-authenticated REST API for automation (n8n, HA, scripts).

Auth: Authorization: Bearer ph_…
Admin-managed instance tokens. See docs/API.md and GET /api/v1.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ..database import get_session
from ..models import ApiToken, Job, Server, User
from ..security.auth import get_admin_user
from ..services import api_tokens as tok_svc
from ..services import jobs as job_service
from ..services import os_patching
from ..services.server_audit import record_server_audit

router = APIRouter()

ALLOWED_JOB_TYPES = frozenset(tok_svc.JOB_FEATURE_KEY.keys())


class ApiAuth:
    """Resolved API token + optional acting user id for audit."""

    def __init__(self, token: ApiToken, client_ip: str | None = None):
        self.token = token
        self.user_id = token.created_by_user_id
        self.token_id = token.id
        self.token_name = token.name
        self.scopes = tok_svc.parse_scopes(token.scopes)
        self.client_ip = client_ip

    def require(self, scope: str) -> None:
        """Enforce a capability scope (read | jobs | edit)."""
        if scope not in tok_svc.CAPABILITY_SCOPES:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Invalid capability scope check: {scope}",
            )
        if scope not in self.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=tok_svc.missing_scope_message(scope),
            )

    def require_feature(self, feature_key: str) -> None:
        """Enforce optional feature:* allowlist on the token."""
        if feature_key not in tok_svc.FEATURE_SCOPE_BY_KEY:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown feature: {feature_key}",
            )
        if not tok_svc.token_allows_feature(self.scopes, feature_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=tok_svc.feature_scope_denied_message(feature_key),
            )

    def require_server_feature(self, server: Server, feature_key: str) -> None:
        """Server feature flag must be on (independent of token scopes)."""
        if not tok_svc.server_feature_enabled(server, feature_key):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=tok_svc.feature_disabled_message(feature_key),
            )

    def require_job_access(self, server: Server, job_type: str) -> str:
        """Validate jobs scope + token feature allowlist + server feature flag.

        Returns the feature key for the job type.
        """
        self.require(tok_svc.SCOPE_JOBS)
        if job_type not in tok_svc.JOB_FEATURE_KEY:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported job_type. Allowed: {sorted(ALLOWED_JOB_TYPES)}",
            )
        feature_key = tok_svc.JOB_FEATURE_KEY[job_type]
        self.require_feature(feature_key)
        self.require_server_feature(server, feature_key)
        return feature_key


def get_api_auth(
    request: Request,
    session: Session = Depends(get_session),
    authorization: Optional[str] = Header(None),
) -> ApiAuth:
    from ..services.demo import reject_if_demo

    demo_block = reject_if_demo("api_use")
    if demo_block:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=demo_block,
        )
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
    q: str = "",
    limit: int = 100,
    offset: int = 0,
):
    auth.require(tok_svc.SCOPE_READ)
    from ..services import list_query as lq

    rows = list(session.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
    q = (q or "").strip()
    if q:
        rows = [s for s in rows if lq.match_server(s, q)]
    total = len(rows)
    lim = lq.clamp_api_limit(limit)
    off = lq.parse_offset(offset)
    page_rows = rows[off : off + lim]
    return {
        "servers": [_server_public(s) for s in page_rows],
        "total": total,
        "limit": lim,
        "offset": off,
        "q": q,
    }


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

    # Map body field → feature key used by token allowlist
    pending: list[tuple[str, str, bool]] = []
    if body.backup is not None:
        pending.append(("backup", "backup", bool(body.backup)))
    if body.os_patch is not None:
        pending.append(("os_patch", "os", bool(body.os_patch)))
    if body.docker is not None:
        pending.append(("docker", "docker", bool(body.docker)))

    if not pending:
        raise HTTPException(400, detail="No feature fields provided (backup, os_patch, docker)")

    # Validate every feature scope before applying any change
    for _field, feature_key, _val in pending:
        auth.require_feature(feature_key)

    changed: dict[str, bool] = {}
    for field, feature_key, val in pending:
        if feature_key == "backup":
            server.backup_enabled = val
            changed["backup"] = server.backup_enabled
        elif feature_key == "os":
            server.os_patch_enabled = val
            changed["os_patch"] = server.os_patch_enabled
        elif feature_key == "docker":
            server.container_patch_enabled = val
            changed["docker"] = server.container_patch_enabled

    session.add(server)
    record_server_audit(
        session,
        server_id=server.id,
        user_id=auth.user_id,
        action="server_features_updated",
        message=f"API feature flags: {', '.join(f'{k}={v}' for k, v in changed.items())}",
        details={"changed": changed, "via": "api"},
        api_token_id=auth.token_id,
        api_token_name=auth.token_name,
    )
    session.commit()
    session.refresh(server)
    return {"ok": True, "changed": changed, "server": _server_public(server)}


def _api_files_server(session: Session, server_id: int) -> Server:
    from ..services import host_files as hf
    from ..services.demo import demo_mode

    if demo_mode() or not hf.files_enabled():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Host Files is disabled")
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404, detail="Server not found")
    if not hf.files_supported(server):
        raise HTTPException(400, detail="Host has no SSH identity for Files")
    return server


def _api_files_http(err) -> HTTPException:
    code = getattr(err, "code", "") or ""
    status_map = {"not_found": 404, "ssh": 502, "denied": 403, "escape": 400, "jail": 400}
    return HTTPException(status_map.get(code, 400), detail=getattr(err, "message", str(err)))


@router.get("/servers/{server_id}/files", summary="List a jail-relative directory (fleet)")
def api_files_list(
    server_id: int,
    p: str = "",
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    from ..services import host_files as hf
    from ..services.audit_write import make_audit_log
    from datetime import datetime as _dt

    auth.require(tok_svc.SCOPE_FILES)
    server = _api_files_server(session, server_id)
    try:
        listing = hf.list_dir(server, p, role=hf.ROLE_FLEET, identity=None)
    except hf.FilesError as e:
        raise _api_files_http(e) from e
    session.add(
        make_audit_log(
            user_id=auth.user_id,
            server_id=server.id,
            api_token_id=auth.token_id,
            api_token_name=auth.token_name,
            action="host_file_list",
            details=f"identity=fleet via=api dir={listing.get('rel') or '.'} count={len(listing.get('entries') or [])}",
            started_at=_dt.utcnow(),
            finished_at=_dt.utcnow(),
        )
    )
    session.commit()
    entries = []
    for e in listing.get("entries") or []:
        entries.append(
            {
                "name": e.get("name"),
                "rel": e.get("rel"),
                "kind": e.get("kind"),
                "size": e.get("size"),
                "secretish": e.get("secretish"),
                "escaped": e.get("escaped"),
            }
        )
    return {
        "jail": listing.get("jail"),
        "rel": listing.get("rel"),
        "truncated": listing.get("truncated"),
        "entries": entries,
    }


@router.get("/servers/{server_id}/files/download", summary="Download one file (fleet)")
def api_files_download(
    server_id: int,
    p: str = "",
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    import hashlib as _hashlib
    from datetime import datetime as _dt

    from ..services import host_files as hf
    from ..services.audit_write import make_audit_log

    auth.require(tok_svc.SCOPE_FILES)
    server = _api_files_server(session, server_id)
    try:
        info = hf.stat_file(server, p, role=hf.ROLE_FLEET)
    except hf.FilesError as e:
        raise _api_files_http(e) from e
    hasher = _hashlib.sha256()
    nbytes = 0

    def gen():
        nonlocal nbytes
        try:
            for chunk in hf.iter_file(server, p, role=hf.ROLE_FLEET):
                hasher.update(chunk)
                nbytes += len(chunk)
                yield chunk
        finally:
            try:
                session.add(
                    make_audit_log(
                        user_id=auth.user_id,
                        server_id=server.id,
                        api_token_id=auth.token_id,
                        api_token_name=auth.token_name,
                        action="host_file_get",
                        details=f"identity=fleet via=api path={info.get('rel')} bytes={nbytes} sha256={hasher.hexdigest()}",
                        started_at=_dt.utcnow(),
                        finished_at=_dt.utcnow(),
                    )
                )
                session.commit()
            except Exception:
                pass

    filename = (info.get("rel") or "download").rsplit("/", 1)[-1]
    return StreamingResponse(
        gen(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/servers/{server_id}/files", summary="Upload one file (fleet)")
async def api_files_upload(
    server_id: int,
    file: UploadFile = File(...),
    p: str = Form(""),
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    from datetime import datetime as _dt

    from ..services import host_files as hf
    from ..services.audit_write import make_audit_log

    auth.require(tok_svc.SCOPE_FILES)
    server = _api_files_server(session, server_id)
    name = hf.sanitize_basename(file.filename or "upload.bin")
    try:
        result = hf.put_file(server, p, name, file.file, size=getattr(file, "size", None), role=hf.ROLE_FLEET)
    except hf.FilesError as e:
        raise _api_files_http(e) from e
    session.add(
        make_audit_log(
            user_id=auth.user_id,
            server_id=server.id,
            api_token_id=auth.token_id,
            api_token_name=auth.token_name,
            action="host_file_put",
            details=(
                f"identity=fleet via=api path={result['rel']} bytes={result['bytes']} "
                f"sha256={result['sha256']} overwrite={int(result['overwrite'])}"
            ),
            started_at=_dt.utcnow(),
            finished_at=_dt.utcnow(),
        )
    )
    session.commit()
    return {"ok": True, "rel": result["rel"], "bytes": result["bytes"], "sha256": result["sha256"], "overwrite": result["overwrite"]}


class FilesMkdirBody(BaseModel):
    p: str = ""
    name: str


class FilesRenameBody(BaseModel):
    p: str = ""
    src: str
    dest: str


@router.post("/servers/{server_id}/files/mkdir", summary="Create a directory (fleet)")
def api_files_mkdir(
    server_id: int,
    body: FilesMkdirBody,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    from datetime import datetime as _dt

    from ..services import host_files as hf
    from ..services.audit_write import make_audit_log

    auth.require(tok_svc.SCOPE_FILES)
    server = _api_files_server(session, server_id)
    try:
        result = hf.mkdir(server, body.p, body.name, role=hf.ROLE_FLEET)
    except hf.FilesError as e:
        raise _api_files_http(e) from e
    session.add(
        make_audit_log(
            user_id=auth.user_id,
            server_id=server.id,
            api_token_id=auth.token_id,
            api_token_name=auth.token_name,
            action="host_file_mkdir",
            details=f"identity=fleet via=api path={result['rel']}",
            started_at=_dt.utcnow(),
            finished_at=_dt.utcnow(),
        )
    )
    session.commit()
    return {"ok": True, "rel": result["rel"]}


@router.post("/servers/{server_id}/files/rename", summary="Rename in the current directory (fleet)")
def api_files_rename(
    server_id: int,
    body: FilesRenameBody,
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    from datetime import datetime as _dt

    from ..services import host_files as hf
    from ..services.audit_write import make_audit_log

    auth.require(tok_svc.SCOPE_FILES)
    server = _api_files_server(session, server_id)
    try:
        result = hf.rename(server, body.p, body.src, body.dest, role=hf.ROLE_FLEET)
    except hf.FilesError as e:
        raise _api_files_http(e) from e
    session.add(
        make_audit_log(
            user_id=auth.user_id,
            server_id=server.id,
            api_token_id=auth.token_id,
            api_token_name=auth.token_name,
            action="host_file_rename",
            details=f"identity=fleet via=api from={result['from']} to={result['to']}",
            started_at=_dt.utcnow(),
            finished_at=_dt.utcnow(),
        )
    )
    session.commit()
    return {"ok": True, "from": result["from"], "to": result["to"]}


@router.delete("/servers/{server_id}/files", summary="Delete a file or empty directory (fleet)")
def api_files_delete(
    server_id: int,
    p: str = "",
    session: Session = Depends(get_session),
    auth: ApiAuth = Depends(get_api_auth),
):
    from datetime import datetime as _dt

    from ..services import host_files as hf
    from ..services.audit_write import make_audit_log

    auth.require(tok_svc.SCOPE_FILES)
    server = _api_files_server(session, server_id)
    try:
        result = hf.remove(server, p, role=hf.ROLE_FLEET)
    except hf.FilesError as e:
        raise _api_files_http(e) from e
    session.add(
        make_audit_log(
            user_id=auth.user_id,
            server_id=server.id,
            api_token_id=auth.token_id,
            api_token_name=auth.token_name,
            action="host_file_delete",
            details=f"identity=fleet via=api path={result['rel']}",
            started_at=_dt.utcnow(),
            finished_at=_dt.utcnow(),
        )
    )
    session.commit()
    return {"ok": True, "rel": result["rel"]}


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
    job_type = (body.job_type or "").strip().lower()
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404, detail="Server not found")

    # Capability scope + token feature allowlist + server feature flag
    auth.require_job_access(server, job_type)

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
            api_token_id=auth.token_id,
            api_token_name=auth.token_name,
        )
    except job_service.BackupAlreadyRunning as e:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Backup already running",
                "job": job_service.job_public_dict(e.job),
            },
        )
    except job_service.JobAlreadyActive as e:
        return JSONResponse(
            status_code=409,
            content={
                "detail": f"{job_type} already active for this server",
                "job": job_service.job_public_dict(e.job),
                "already_active": True,
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
    status: Optional[str] = "all",
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    """List tokens. status: active | revoked | all (default all for API completeness).

    Soft-revoked rows are retained for audit trail; they never disappear from the DB.
    """
    filt = tok_svc.normalize_token_list_status(status)
    # API default historically returned everything; keep that when status omitted
    if status is None or str(status).strip() == "":
        filt = "all"
    rows = tok_svc.list_api_tokens(session, status=filt)
    counts = tok_svc.count_api_tokens_by_status(session)
    return {
        "status": filt,
        "counts": counts,
        "tokens": [tok_svc.token_public_dict(t) for t in rows],
    }


@router.post("/tokens", status_code=201, summary="Create API token (admin session)")
def admin_create_token(
    body: TokenCreateBody,
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    from ..services.demo import DemoBlocked, http_403_if_demo

    http_403_if_demo("api_token")
    try:
        row, plain = tok_svc.create_api_token(
            session,
            name=body.name,
            created_by=user,
            scopes=body.scopes,
            allowed_cidrs=body.allowed_cidrs,
        )
    except DemoBlocked as e:
        raise HTTPException(status_code=403, detail=e.message) from e
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

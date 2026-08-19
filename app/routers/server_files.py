"""Host Files dest-card — jailed SFTP browser (v1.3 Stream F)."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlmodel import Session

from .. import templates as templates_mod
from ..database import get_session
from ..models import Server, User
from ..security.auth import (
    cookie_auth_kwargs,
    get_operator_user,
    user_session_version,
)
from ..services import host_files as hf
from ..services import ssh_console as cons
from ..services import ssh_identities as idents
from ..services.audit_write import make_audit_log
from ..services.demo import demo_mode
from ..services.nav_shortcuts import host_feature_context
from ..services.request_ip import client_ip_from_request

router = APIRouter()
logger = logging.getLogger("piherder.files")

_STATUS = {
    "disabled": 403,
    "demo": 403,
    "privileged_forbidden": 403,
    "privileged_confirm": 403,
    "not_found": 404,
    "ssh": 502,
}


def _files_gate() -> None:
    if demo_mode():
        raise HTTPException(status_code=403, detail="Files is off in demo")
    if not hf.files_enabled():
        raise HTTPException(status_code=404)


def _http(err: hf.FilesError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS.get(err.code, 400),
        detail=err.message,
    )


def _redirect(server_id: int, *, p: str = "", identity: str = "fleet", **params: str) -> RedirectResponse:
    q = {"p": p or "", "identity": identity or "fleet"}
    for k, v in params.items():
        if v is not None:
            q[k] = str(v)
    return RedirectResponse(
        f"/servers/{int(server_id)}/files?{urlencode(q)}",
        status_code=303,
    )


def _audit(
    session: Session,
    *,
    user_id: Optional[int],
    server_id: Optional[int],
    action: str,
    details: str,
    status: str = "success",
) -> None:
    session.add(
        make_audit_log(
            user_id=user_id,
            server_id=server_id,
            action=action,
            status=status,
            details=details,
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
    )
    session.commit()


def _has_grant(request: Request, user: User) -> bool:
    ip = client_ip_from_request(request) or ""
    device_id = cons.ensure_device_id(request.cookies.get(cons.CONSOLE_DEVICE_COOKIE))
    return cons.grant_valid(
        request.cookies.get(cons.CONSOLE_GRANT_COOKIE),
        user_id=int(user.id),
        server_id=0,
        session_version=user_session_version(user),
        client_ip=ip,
        device_id=device_id,
    )


def _load_identity(
    session: Session,
    server: Server,
    user: User,
    request: Request,
    role_raw: Optional[str],
    *,
    need_grant: bool,
):
    role = hf.normalize_role(role_raw)
    ident = None
    if role == hf.ROLE_PRIVILEGED:
        if not cons.can_open_privileged(user):
            raise HTTPException(status_code=403, detail="Privileged files are not allowed for this role")
        ident = idents.get_by_role(session, int(server.id), idents.ROLE_PRIVILEGED)
        if not ident or not ident.enabled:
            raise HTTPException(status_code=400, detail="No privileged identity on this host")
        if need_grant and not _has_grant(request, user):
            raise HTTPException(status_code=403, detail="privileged_confirm")
    return role, ident


def _get_server(session: Session, server_id: int) -> Server:
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    if not hf.files_supported(server):
        raise HTTPException(status_code=400, detail="Host has no SSH identity for Files")
    return server


@router.get("/{server_id}/files", response_class=HTMLResponse)
async def files_page(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    p: str = "",
    identity: str = "fleet",
):
    _files_gate()
    server = _get_server(session, server_id)
    role = hf.normalize_role(identity)
    ident = None
    needs_stepup = False
    ident_error = ""
    if role == hf.ROLE_PRIVILEGED:
        if not cons.can_open_privileged(user):
            return _redirect(server_id, p=p, identity="fleet", error="privileged_forbidden")
        ident = idents.get_by_role(session, int(server.id), idents.ROLE_PRIVILEGED)
        if not ident or not ident.enabled:
            ident_error = "No privileged identity on this host"
            role = hf.ROLE_FLEET
            ident = None
        elif not _has_grant(request, user):
            needs_stepup = True

    listing = {
        "jail": "",
        "rel": p or "",
        "abs": "",
        "truncated": False,
        "entries": [],
        "crumbs": [],
    }
    list_error = ident_error
    if not needs_stepup and not ident_error:
        try:
            listing = hf.list_dir(server, p, role=role, identity=ident)
            _audit(
                session,
                user_id=user.id,
                server_id=server.id,
                action="host_file_list",
                details=(
                    f"identity={role} dir={listing.get('rel') or '.'} "
                    f"count={len(listing.get('entries') or [])}"
                    f"{' truncated=1' if listing.get('truncated') else ''}"
                ),
            )
        except hf.FilesError as e:
            list_error = e.message
            try:
                listing["jail"] = hf.jail_path(server, role=role, identity=ident)
            except hf.FilesError:
                pass
        except Exception as e:
            logger.exception("files list")
            list_error = str(e)[:200]

    from ..security.auth import ROLE_OPERATOR, role_at_least

    id_rows = idents.list_for_server(session, int(server.id))
    identities = [idents.public_view(r) for r in id_rows]
    _nav = host_feature_context(session, int(user.id) if user else None, server, "files")
    existing = [e["name"] for e in listing.get("entries") or [] if e.get("kind") == "file"]
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_files.html",
        context={
            "title": f"{server.name} · Files",
            "server": server,
            "listing": listing,
            "role": role,
            "identities": identities,
            "needs_stepup": needs_stepup,
            "list_error": list_error,
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or list_error,
            "can_privileged": cons.can_open_privileged(user),
            "existing_names": existing,
            "max_upload_h": hf.human_size(hf.max_upload_bytes()),
            "is_operator": role_at_least(user, ROLE_OPERATOR),
            **_nav,
        },
    )


@router.post("/{server_id}/files/unlock")
async def files_unlock(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    totp_code: str = Form(""),
    p: str = Form(""),
):
    _files_gate()
    server = _get_server(session, server_id)
    if not cons.can_open_privileged(user):
        raise HTTPException(status_code=403, detail="Privileged files are not allowed for this role")
    from .server_console import _verify_console_2fa

    ok, err = _verify_console_2fa(session, user, totp_code=totp_code)
    if not ok:
        return _redirect(server_id, p=p, identity="privileged", error=err or "2fa_required")
    ip = client_ip_from_request(request) or ""
    device_id = cons.ensure_device_id(request.cookies.get(cons.CONSOLE_DEVICE_COOKIE))
    grant = cons.mint_grant(
        user_id=int(user.id),
        server_id=int(server.id),
        session_version=user_session_version(user),
        client_ip=ip,
        device_id=device_id,
        require_console=False,
    )
    resp = _redirect(server_id, p=p, identity="privileged", msg="unlocked")
    resp.set_cookie(
        cons.CONSOLE_GRANT_COOKIE,
        grant,
        **cookie_auth_kwargs(max_age=cons.grant_minutes() * 60),
    )
    if cons.bind_device_enabled():
        resp.set_cookie(
            cons.CONSOLE_DEVICE_COOKIE,
            device_id,
            **cookie_auth_kwargs(max_age=60 * 60 * 24 * 400),
        )
    return resp


@router.get("/{server_id}/files/download")
async def files_download(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    p: str = "",
    identity: str = "fleet",
):
    _files_gate()
    server = _get_server(session, server_id)
    role, ident = _load_identity(session, server, user, request, identity, need_grant=True)
    try:
        info = hf.stat_file(server, p, role=role, identity=ident)
    except hf.FilesError as e:
        raise _http(e) from e
    hasher = hashlib.sha256()
    nbytes = 0

    def gen():
        nonlocal nbytes
        try:
            for chunk in hf.iter_file(server, p, role=role, identity=ident):
                hasher.update(chunk)
                nbytes += len(chunk)
                yield chunk
        finally:
            try:
                _audit(
                    session,
                    user_id=user.id,
                    server_id=server.id,
                    action="host_file_get",
                    details=(
                        f"identity={role} path={info.get('rel')} "
                        f"bytes={nbytes} sha256={hasher.hexdigest()}"
                    ),
                )
            except Exception:
                logger.exception("files download audit")

    filename = (info.get("rel") or "download").rsplit("/", 1)[-1]
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(gen(), media_type="application/octet-stream", headers=headers)


@router.post("/{server_id}/files/upload")
async def files_upload(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    file: UploadFile = File(...),
    p: str = Form(""),
    identity: str = Form("fleet"),
):
    _files_gate()
    server = _get_server(session, server_id)
    role, ident = _load_identity(session, server, user, request, identity, need_grant=True)
    name = hf.sanitize_basename(file.filename or "upload.bin")
    try:
        result = hf.put_file(
            server,
            p,
            name,
            file.file,
            size=getattr(file, "size", None),
            role=role,
            identity=ident,
        )
    except hf.FilesError as e:
        _audit(
            session,
            user_id=user.id,
            server_id=server.id,
            action="host_file_put",
            details=f"identity={role} path={p}/{name} error={e.code}",
            status="error",
        )
        raise _http(e) from e
    _audit(
        session,
        user_id=user.id,
        server_id=server.id,
        action="host_file_put",
        details=(
            f"identity={role} path={result['rel']} bytes={result['bytes']} "
            f"sha256={result['sha256']} overwrite={int(result['overwrite'])}"
        ),
    )
    return _redirect(server_id, p=p, identity=role, msg="uploaded")


@router.post("/{server_id}/files/mkdir")
async def files_mkdir(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    name: str = Form(...),
    p: str = Form(""),
    identity: str = Form("fleet"),
):
    _files_gate()
    server = _get_server(session, server_id)
    role, ident = _load_identity(session, server, user, request, identity, need_grant=True)
    try:
        result = hf.mkdir(server, p, name, role=role, identity=ident)
    except hf.FilesError as e:
        _audit(
            session,
            user_id=user.id,
            server_id=server.id,
            action="host_file_mkdir",
            details=f"identity={role} error={e.code}",
            status="error",
        )
        raise _http(e) from e
    _audit(
        session,
        user_id=user.id,
        server_id=server.id,
        action="host_file_mkdir",
        details=f"identity={role} path={result['rel']}",
    )
    return _redirect(server_id, p=p, identity=role, msg="mkdir")


@router.post("/{server_id}/files/rename")
async def files_rename(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    src: str = Form(...),
    dest: str = Form(...),
    p: str = Form(""),
    identity: str = Form("fleet"),
):
    _files_gate()
    server = _get_server(session, server_id)
    role, ident = _load_identity(session, server, user, request, identity, need_grant=True)
    try:
        result = hf.rename(server, p, src, dest, role=role, identity=ident)
    except hf.FilesError as e:
        _audit(
            session,
            user_id=user.id,
            server_id=server.id,
            action="host_file_rename",
            details=f"identity={role} error={e.code}",
            status="error",
        )
        raise _http(e) from e
    _audit(
        session,
        user_id=user.id,
        server_id=server.id,
        action="host_file_rename",
        details=f"identity={role} from={result['from']} to={result['to']}",
    )
    return _redirect(server_id, p=p, identity=role, msg="renamed")


@router.post("/{server_id}/files/delete")
async def files_delete(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    name: str = Form(...),
    p: str = Form(""),
    identity: str = Form("fleet"),
):
    _files_gate()
    server = _get_server(session, server_id)
    role, ident = _load_identity(session, server, user, request, identity, need_grant=True)
    rel = "/".join([x for x in ((p or "").strip("/"), name) if x])
    try:
        result = hf.remove(server, rel, role=role, identity=ident)
    except hf.FilesError as e:
        _audit(
            session,
            user_id=user.id,
            server_id=server.id,
            action="host_file_delete",
            details=f"identity={role} error={e.code}",
            status="error",
        )
        raise _http(e) from e
    _audit(
        session,
        user_id=user.id,
        server_id=server.id,
        action="host_file_delete",
        details=f"identity={role} path={result['rel']}",
    )
    return _redirect(server_id, p=p, identity=role, msg="deleted")

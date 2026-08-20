"""Host Files dest-card — jailed SFTP browser (v1.3 Stream F)."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime
from queue import Queue
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.concurrency import iterate_in_threadpool
from sqlmodel import Session

from .. import templates as templates_mod
from ..database import get_session
from ..models import Server, User
from ..security.auth import (
    cookie_auth_kwargs,
    cookie_delete_kwargs,
    get_operator_user,
    user_session_version,
)
from ..services import host_files as hf
from ..services import ssh_console as cons
from ..services import ssh_identities as idents
from ..services import webauthn_svc as wa_svc
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
    "denied": 403,
    "not_found": 404,
    "too_large": 413,
    "binary": 415,
    "exists": 409,
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


def _wants_json(request: Request) -> bool:
    if (request.headers.get("x-piherder-files") or "") == "1":
        return True
    acc = (request.headers.get("accept") or "").lower()
    return "application/json" in acc and "text/html" not in acc


def listing_public(listing: dict) -> dict:
    from ..services.app_settings import format_datetime_in_app_tz

    entries = []
    for e in listing.get("entries") or []:
        mt = e.get("mtime")
        entries.append(
            {
                "name": e.get("name"),
                "rel": e.get("rel") or "",
                "kind": e.get("kind"),
                "size": e.get("size"),
                "size_h": e.get("size_h") or "",
                "mtime_h": format_datetime_in_app_tz(mt, "%Y-%m-%d %H:%M") if mt else "",
                "secretish": bool(e.get("secretish")),
                "escaped": bool(e.get("escaped")),
                "mode_h": e.get("mode_h") or "",
                "owner_h": e.get("owner_h") or "",
                "owner": e.get("owner") or "",
                "group": e.get("group") or "",
                "uid": e.get("uid"),
                "gid": e.get("gid"),
            }
        )
    return {
        "ok": True,
        "jail": listing.get("jail") or "",
        "rel": listing.get("rel") or "",
        "abs": listing.get("abs") or "",
        "truncated": bool(listing.get("truncated")),
        "crumbs": listing.get("crumbs") or [],
        "entries": entries,
        "search": bool(listing.get("search")),
        "query": listing.get("query") or "",
    }


def _ok_mutate(request: Request, server: Server, p: str, role: str, ident, msg: str):
    if _wants_json(request):
        try:
            listing = hf.list_dir(server, p, role=role, identity=ident)
            body = listing_public(listing)
            body["msg"] = msg
            return JSONResponse(body)
        except hf.FilesError as e:
            return JSONResponse(
                {
                    "ok": True,
                    "msg": msg,
                    "error": e.message,
                    "rel": p or "",
                    "entries": [],
                    "crumbs": [],
                }
            )
    return _redirect(int(server.id), p=p, identity=role, msg=msg)


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

    has_passkeys = False
    try:
        has_passkeys = bool(user and user.id and wa_svc.has_passkeys(session, int(user.id)))
    except Exception:
        has_passkeys = False

    id_rows = idents.list_for_server(session, int(server.id))
    identities = [idents.public_view(r) for r in id_rows]
    _nav = host_feature_context(session, int(user.id) if user else None, server, "files")
    existing = [e["name"] for e in listing.get("entries") or [] if e.get("kind") == "file"]
    boot = listing_public(listing)
    if list_error:
        boot["error"] = list_error
        boot["ok"] = False
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_files.html",
        context={
            "title": f"{server.name} · Files",
            "server": server,
            "listing": listing,
            "listing_boot": boot,
            "role": role,
            "identities": identities,
            "needs_stepup": needs_stepup,
            "list_error": list_error,
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or list_error,
            "can_privileged": cons.can_open_privileged(user),
            "has_passkeys": has_passkeys,
            "prefer_passkey": cons.prefer_passkey(),
            "require_passkey": cons.require_passkey_if_enrolled(),
            "existing_names": existing,
            "max_upload_h": hf.human_size(hf.max_upload_bytes()),
            "is_operator": role_at_least(user, ROLE_OPERATOR),
            **_nav,
        },
    )


@router.get("/{server_id}/files/ls")
async def files_ls(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    p: str = "",
    identity: str = "fleet",
):
    """JSON directory listing for the explorer (no full-page reload)."""
    _files_gate()
    server = _get_server(session, server_id)
    role, ident = _load_identity(session, server, user, request, identity, need_grant=True)
    try:
        listing = hf.list_dir(server, p, role=role, identity=ident)
    except hf.FilesError as e:
        raise _http(e) from e
    return JSONResponse(listing_public(listing))


@router.get("/{server_id}/files/search")
async def files_search(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    q: str = "",
    p: str = "",
    identity: str = "fleet",
):
    _files_gate()
    server = _get_server(session, server_id)
    role, ident = _load_identity(session, server, user, request, identity, need_grant=True)
    try:
        listing = hf.search(server, q, rel=p, role=role, identity=ident)
    except hf.FilesError as e:
        raise _http(e) from e
    return JSONResponse(listing_public(listing))


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


@router.post("/{server_id}/files/webauthn/options")
async def files_webauthn_options(
    request: Request,
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Passkey options for privileged Files — does not require the console kill switch."""
    _files_gate()
    _get_server(session, server_id)
    if not cons.can_open_privileged(user):
        raise HTTPException(status_code=403, detail="Privileged files are not allowed for this role")
    if not cons.same_site_browser_request(request):
        return JSONResponse(
            {"ok": False, "error": "cross_site", "detail": "Passkey step-up is same-origin only."},
            status_code=403,
        )
    try:
        options_json, chal = wa_svc.authentication_options_json(session, user)
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": "passkey_unavailable", "detail": str(e)[:120]},
            status_code=400,
        )
    resp = JSONResponse({"ok": True, "options": json.loads(options_json)})
    resp.set_cookie(
        wa_svc.CHALLENGE_COOKIE_AUTH,
        chal,
        **cookie_auth_kwargs(max_age=wa_svc.CHALLENGE_MINUTES * 60),
    )
    return resp


@router.post("/{server_id}/files/webauthn/verify")
async def files_webauthn_verify(
    request: Request,
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Verify passkey and set the same grant cookie the console uses."""
    _files_gate()
    server = _get_server(session, server_id)
    if not cons.can_open_privileged(user):
        raise HTTPException(status_code=403, detail="Privileged files are not allowed for this role")
    if not cons.same_site_browser_request(request):
        return JSONResponse(
            {"ok": False, "error": "cross_site", "detail": "Passkey step-up is same-origin only."},
            status_code=403,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
    credential = body.get("credential") if isinstance(body, dict) else None
    if not isinstance(credential, dict):
        return JSONResponse({"ok": False, "error": "bad_credential"}, status_code=400)
    chal = request.cookies.get(wa_svc.CHALLENGE_COOKIE_AUTH)
    ip = client_ip_from_request(request) or ""
    try:
        wa_svc.verify_authentication(session, user, credential, chal)
    except Exception as e:
        _audit(
            session,
            user_id=user.id,
            server_id=server.id,
            action="host_file_denied",
            details=f"passkey failed ip={ip} {str(e)[:80]}",
            status="error",
        )
        return JSONResponse({"ok": False, "error": "2fa_bad_code", "detail": "Passkey verification failed"}, status_code=403)

    sv = user_session_version(user)
    device_id = cons.ensure_device_id(request.cookies.get(cons.CONSOLE_DEVICE_COOKIE))
    grant = cons.mint_grant(
        user_id=int(user.id),
        server_id=int(server.id),
        session_version=sv,
        client_ip=ip,
        device_id=device_id,
        require_console=False,
    )
    resp = JSONResponse({"ok": True, "grant_active": True})
    resp.delete_cookie(wa_svc.CHALLENGE_COOKIE_AUTH, **cookie_delete_kwargs())
    if cons.bind_device_enabled():
        resp.set_cookie(
            cons.CONSOLE_DEVICE_COOKIE,
            device_id,
            **cookie_auth_kwargs(max_age=60 * 60 * 24 * 400),
        )
    resp.set_cookie(
        cons.CONSOLE_GRANT_COOKIE,
        grant,
        **cookie_auth_kwargs(max_age=cons.grant_minutes() * 60),
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
    hasher = hashlib.sha256()
    nbytes = 0
    rel = (p or "").strip()
    try:
        info = hf.stat_file(server, p, role=role, identity=ident)
    except hf.FilesError as e:
        raise _http(e) from e

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
                        f"identity={role} path={info.get('rel') or rel} "
                        f"bytes={nbytes} sha256={hasher.hexdigest()}"
                    ),
                )
            except Exception:
                logger.exception("files download audit")

    filename = (info.get("rel") or rel or "download").rsplit("/", 1)[-1] or "download"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store, no-transform",
        "X-Accel-Buffering": "no",
    }
    size = info.get("size")
    if size is not None and int(size) >= 0:
        headers["Content-Length"] = str(int(size))
    return StreamingResponse(
        iterate_in_threadpool(gen()),
        media_type="application/octet-stream",
        headers=headers,
    )


@router.get("/{server_id}/files/content")
async def files_content(
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
        data = hf.read_text(server, p, role=role, identity=ident)
    except hf.FilesError as e:
        raise _http(e) from e
    return JSONResponse({"ok": True, **data})


@router.post("/{server_id}/files/save")
async def files_save(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    p: str = Form(""),
    identity: str = Form("fleet"),
    content: str = Form(""),
):
    _files_gate()
    server = _get_server(session, server_id)
    role, ident = _load_identity(session, server, user, request, identity, need_grant=True)
    try:
        result = hf.write_text(server, p, content, role=role, identity=ident)
    except hf.FilesError as e:
        raise _http(e) from e
    _audit(
        session,
        user_id=user.id,
        server_id=server.id,
        action="host_file_put",
        details=f"identity={role} path={result.get('rel')} bytes={result.get('bytes')} edit=1",
    )
    return _ok_mutate(request, server, (p or "").rpartition("/")[0], role, ident, "saved")


def _rels_from_names(p: str, names: list[str]) -> list[str]:
    rels = []
    for n in names or []:
        n = (n or "").strip().strip("/")
        if not n:
            continue
        rels.append(n if not p else f"{p.strip('/')}/{n}")
    return rels


def _delete_zip_sources(server, rels: list[str], *, skip: str, role: str, ident) -> None:
    skip_n = (skip or "").strip("/")
    for rel in rels:
        if (rel or "").strip("/") == skip_n:
            continue
        try:
            hf.remove_tree(server, rel, role=role, identity=ident)
        except hf.FilesError:
            logger.exception("files zip delete source %s", rel)


@router.post("/{server_id}/files/archive")
async def files_archive(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    p: str = Form(""),
    identity: str = Form("fleet"),
    names: list[str] = Form(default=[]),
    dest: str = Form("download"),
    name: str = Form(""),
    delete: str = Form(""),
):
    _files_gate()
    server = _get_server(session, server_id)
    role, ident = _load_identity(session, server, user, request, identity, need_grant=True)
    rels = _rels_from_names(p, names)
    want_host = str(dest or "").strip().lower() in ("host", "save", "remote")
    want_delete = str(delete or "").strip().lower() in ("1", "true", "on", "yes")
    if want_host:
        try:
            result = hf.save_zip(
                server, rels, p, name or None, role=role, identity=ident
            )
        except hf.FilesError as e:
            raise _http(e) from e
        zip_rel = result.get("rel") or ""
        if want_delete:
            _delete_zip_sources(server, rels, skip=zip_rel, role=role, ident=ident)
        _audit(
            session,
            user_id=user.id,
            server_id=server.id,
            action="host_file_put",
            details=(
                f"identity={role} zip={zip_rel} names={len(rels)} "
                f"save=host delete={int(want_delete)}"
            ),
        )
        return _ok_mutate(request, server, p, role, ident, "zipped")
    try:
        fname, body = hf.build_zip(
            server, rels, role=role, identity=ident, dest_name=name or None
        )
    except hf.FilesError as e:
        raise _http(e) from e
    _audit(
        session,
        user_id=user.id,
        server_id=server.id,
        action="host_file_get",
        details=(
            f"identity={role} zip={fname} names={len(rels)} "
            f"save=download delete={int(want_delete)}"
        ),
    )
    headers = {
        "Content-Disposition": f'attachment; filename="{fname}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store, no-transform",
        "X-Accel-Buffering": "no",
    }

    def gen():
        ok = False
        try:
            for chunk in body:
                yield chunk
            ok = True
        finally:
            if ok and want_delete:
                _delete_zip_sources(server, rels, skip="", role=role, ident=ident)

    return StreamingResponse(
        iterate_in_threadpool(gen()),
        media_type="application/zip",
        headers=headers,
    )


@router.post("/{server_id}/files/unzip")
async def files_unzip(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    p: str = Form(""),
    identity: str = Form("fleet"),
    name: str = Form(...),
):
    _files_gate()
    server = _get_server(session, server_id)
    role, ident = _load_identity(session, server, user, request, identity, need_grant=True)
    zip_rel = "/".join(x for x in ((p or "").strip("/"), name) if x)
    try:
        result = hf.unzip_into(server, zip_rel, p, role=role, identity=ident)
    except hf.FilesError as e:
        raise _http(e) from e
    _audit(
        session,
        user_id=user.id,
        server_id=server.id,
        action="host_file_put",
        details=f"identity={role} unzip={zip_rel} files={result.get('files')} bytes={result.get('bytes')}",
    )
    return _ok_mutate(request, server, p, role, ident, "unzipped")


@router.post("/{server_id}/files/rm")
async def files_rm(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    p: str = Form(""),
    identity: str = Form("fleet"),
    names: list[str] = Form(default=[]),
):
    _files_gate()
    server = _get_server(session, server_id)
    role, ident = _load_identity(session, server, user, request, identity, need_grant=True)
    totals = {"files": 0, "dirs": 0}
    try:
        for n in names or []:
            rel = "/".join(x for x in ((p or "").strip("/"), n) if x)
            out = hf.remove_tree(server, rel, role=role, identity=ident)
            totals["files"] += int(out.get("files") or 0)
            totals["dirs"] += int(out.get("dirs") or 0)
    except hf.FilesError as e:
        raise _http(e) from e
    _audit(
        session,
        user_id=user.id,
        server_id=server.id,
        action="host_file_delete",
        details=f"identity={role} rm={len(names or [])} files={totals['files']} dirs={totals['dirs']}",
    )
    return _ok_mutate(request, server, p, role, ident, "deleted")


@router.post("/{server_id}/files/perms")
async def files_perms(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    p: str = Form(""),
    identity: str = Form("fleet"),
    names: list[str] = Form(default=[]),
    mode: str = Form(""),
    owner: str = Form(""),
    group: str = Form(""),
    recursive: str = Form(""),
):
    _files_gate()
    server = _get_server(session, server_id)
    rec = str(recursive or "").strip().lower() in ("1", "true", "on", "yes")
    want_owner = bool((owner or "").strip() or (group or "").strip())
    role_asked = hf.normalize_role(identity)
    if want_owner and role_asked != hf.ROLE_PRIVILEGED:
        raise HTTPException(
            status_code=403,
            detail="Ownership requires privileged Files (Connect as…)",
        )
    role, ident = _load_identity(
        session, server, user, request, identity, need_grant=True
    )
    rels = []
    for n in names or []:
        n = (n or "").strip().strip("/")
        if not n:
            continue
        rels.append(n if not p else f"{p.strip('/')}/{n}")
    try:
        result = hf.apply_perms(
            server,
            rels,
            mode=mode,
            owner=owner,
            group=group,
            recursive=rec,
            role=role,
            identity=ident,
        )
    except hf.FilesError as e:
        raise _http(e) from e
    _audit(
        session,
        user_id=user.id,
        server_id=server.id,
        action="host_file_chmod",
        details=(
            f"identity={role} names={len(rels)} mode={result.get('mode') or '-'} "
            f"owner={result.get('owner') or '-'} group={result.get('group') or '-'} "
            f"recursive={int(rec)} changed={result.get('changed')} sudo={int(bool(result.get('sudo')))}"
        ),
    )
    return _ok_mutate(request, server, p, role, ident, "permissions updated")


@router.post("/{server_id}/files/move")
async def files_move(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    p: str = Form(""),
    identity: str = Form("fleet"),
    dest: str = Form(...),
    names: list[str] = Form(default=[]),
    overwrite: str = Form(""),
):
    _files_gate()
    server = _get_server(session, server_id)
    role, ident = _load_identity(session, server, user, request, identity, need_grant=True)
    ow = str(overwrite or "").strip().lower() in ("1", "true", "on", "yes")
    rels = []
    for n in names or []:
        n = (n or "").strip().strip("/")
        if not n:
            continue
        rels.append(n if not p else f"{p.strip('/')}/{n}")
    try:
        result = hf.move_many(
            server, rels, dest, overwrite=ow, role=role, identity=ident
        )
    except hf.FilesError as e:
        raise _http(e) from e
    _audit(
        session,
        user_id=user.id,
        server_id=server.id,
        action="host_file_rename",
        details=(
            f"identity={role} move={len(rels)} dest={result.get('dest')} "
            f"moved={result.get('moved')} overwrite={int(ow)}"
        ),
    )
    return _ok_mutate(request, server, result.get("dest") or dest, role, ident, "moved")


@router.post("/{server_id}/files/upload")
async def files_upload(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    file: UploadFile = File(...),
    p: str = Form(""),
    identity: str = Form("fleet"),
    rel_path: str = Form(""),
):
    _files_gate()
    server = _get_server(session, server_id)
    role, ident = _load_identity(session, server, user, request, identity, need_grant=True)
    put_dir = p
    name = hf.sanitize_basename(file.filename or "upload.bin")
    nested = (rel_path or "").strip()
    if nested:
        try:
            parts = hf.parse_nested_rel(nested)
        except hf.FilesError as e:
            raise _http(e) from e
        if not parts:
            raise HTTPException(status_code=400, detail="Invalid folder path")
        name = parts[-1]
        parent = "/".join(x for x in ((p or "").strip("/"), *parts[:-1]) if x)
        if parts[:-1]:
            try:
                hf.ensure_dir(server, parent, role=role, identity=ident)
            except hf.FilesError as e:
                raise _http(e) from e
        put_dir = parent
    total = getattr(file, "size", None)
    try:
        total = int(total) if total not in (None, "") else None
    except (TypeError, ValueError):
        total = None

    if not _wants_json(request):
        try:
            result = hf.put_file(
                server,
                put_dir,
                name,
                file.file,
                size=total,
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
        return _ok_mutate(request, server, p, role, ident, "uploaded")

    q: Queue = Queue()

    def progress(written: int, tot: int) -> None:
        q.put(("sftp", written, tot))

    def worker() -> None:
        try:
            result = hf.put_file(
                server,
                put_dir,
                name,
                file.file,
                size=total,
                role=role,
                identity=ident,
                progress=progress,
            )
            listing = hf.list_dir(server, p, role=role, identity=ident)
            q.put(("done", result, listing))
        except Exception as e:
            q.put(("err", e))

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            item = q.get()
            if item[0] == "sftp":
                yield json.dumps(
                    {"phase": "sftp", "written": item[1], "total": item[2] or 0}
                ) + "\n"
            elif item[0] == "done":
                result, listing = item[1], item[2]
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
                body = listing_public(listing)
                body["msg"] = "uploaded"
                yield json.dumps(body) + "\n"
                return
            else:
                err = item[1]
                if isinstance(err, hf.FilesError):
                    _audit(
                        session,
                        user_id=user.id,
                        server_id=server.id,
                        action="host_file_put",
                        details=f"identity={role} path={p}/{name} error={err.code}",
                        status="error",
                    )
                    yield json.dumps(
                        {"ok": False, "error": err.message, "code": err.code}
                    ) + "\n"
                else:
                    yield json.dumps(
                        {"ok": False, "error": str(err)[:200], "code": "ssh"}
                    ) + "\n"
                return

    return StreamingResponse(gen(), media_type="application/x-ndjson")


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
    return _ok_mutate(request, server, p, role, ident, "mkdir")


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
    return _ok_mutate(request, server, p, role, ident, "renamed")


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
    return _ok_mutate(request, server, p, role, ident, "deleted")

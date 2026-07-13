"""Managed TLS certificates — list, upload PEM, targets, deploy, renew."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select
from urllib.parse import urlencode

from .. import templates as templates_mod
from ..database import get_session
from ..models import AuditLog, Server, User
from ..security.auth import get_current_user, get_operator_user, role_at_least, ROLE_OPERATOR
from ..services import certificates as cert_svc

logger = logging.getLogger(__name__)
router = APIRouter(tags=["certificates"])


def _audit(
    session: Session,
    user: User,
    action: str,
    *,
    server_id: Optional[int] = None,
    details: str = "",
    status: str = "success",
) -> None:
    try:
        session.add(
            AuditLog(
                user_id=user.id,
                server_id=server_id,
                action=action,
                status=status,
                details=(details or "")[:2000],
                started_at=datetime.utcnow(),
                finished_at=datetime.utcnow(),
            )
        )
        session.commit()
    except Exception as e:
        logger.debug("audit skip: %s", e)
        session.rollback()


def _redirect(path: str, **params) -> RedirectResponse:
    if params:
        path = f"{path}?{urlencode({k: v for k, v in params.items() if v is not None})}"
    return RedirectResponse(path, status_code=303)


def _can_mutate(user: User) -> bool:
    return role_at_least(user, ROLE_OPERATOR)


@router.get("/certificates", response_class=HTMLResponse)
async def certificates_list(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    rows = cert_svc.list_certificates(session)
    items = [cert_svc.public_cert_dict(c) for c in rows]
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="certificates_list.html",
        context={
            "title": "Certificates",
            "user": user,
            "certificates": items,
            "can_mutate": _can_mutate(user),
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


@router.get("/certificates/upload", response_class=HTMLResponse)
async def certificate_upload_form(
    request: Request,
    user: User = Depends(get_operator_user),
):
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="certificates_upload.html",
        context={
            "title": "Upload certificate",
            "user": user,
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


@router.post("/certificates/upload")
async def certificate_upload(
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    name: str = Form(""),
    fullchain_pem: str = Form(...),
    privkey_pem: str = Form(...),
):
    try:
        row = cert_svc.upsert_from_pems(
            session,
            name=name or "Uploaded certificate",
            fullchain_pem=fullchain_pem,
            privkey_pem=privkey_pem,
            source="upload",
            auto_renew=False,
        )
        _audit(
            session,
            user,
            "cert_uploaded",
            details=f"cert={row.id} name={row.name}",
        )
        return _redirect(f"/certificates/{row.id}", msg="uploaded")
    except ValueError as e:
        return _redirect("/certificates/upload", error="invalid", detail=str(e)[:200])
    except Exception as e:
        logger.exception("cert upload")
        return _redirect(
            "/certificates/upload", error="save_failed", detail=str(e)[:200]
        )


@router.get("/certificates/{cert_id}", response_class=HTMLResponse)
async def certificate_detail(
    cert_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    cert = cert_svc.get_certificate(session, cert_id)
    if not cert:
        raise HTTPException(404)
    targets = cert_svc.list_targets(session, cert_id)
    servers = list(session.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
    server_names = {s.id: s.name for s in servers}
    target_rows = []
    for t in targets:
        target_rows.append(
            {
                "id": t.id,
                "server_id": t.server_id,
                "server_name": server_names.get(t.server_id, f"#{t.server_id}"),
                "remote_dir": t.remote_dir,
                "layout": t.layout,
                "enabled": t.enabled,
                "last_deployed_at": t.last_deployed_at,
                "last_deploy_status": t.last_deploy_status,
                "last_deploy_message": t.last_deploy_message,
                "fullchain_filename": t.fullchain_filename,
                "privkey_filename": t.privkey_filename,
                "combined_filename": t.combined_filename,
                "pfx_filename": t.pfx_filename,
            }
        )
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="certificates_detail.html",
        context={
            "title": cert.name,
            "user": user,
            "cert": cert_svc.public_cert_dict(cert),
            "targets": target_rows,
            "servers": servers,
            "can_mutate": _can_mutate(user),
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


@router.post("/certificates/{cert_id}/settings")
async def certificate_settings(
    cert_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    name: str = Form(...),
    auto_renew: Optional[str] = Form(None),
    renew_days_before: int = Form(21),
):
    cert = cert_svc.get_certificate(session, cert_id)
    if not cert:
        raise HTTPException(404)
    cert.name = (name or cert.name).strip() or cert.name
    cert.auto_renew = auto_renew in ("on", "1", "true")
    cert.renew_days_before = max(1, min(90, int(renew_days_before or 21)))
    cert.updated_at = datetime.utcnow()
    session.add(cert)
    session.commit()
    _audit(session, user, "cert_updated", details=f"cert={cert_id}")
    return _redirect(f"/certificates/{cert_id}", msg="saved")


@router.post("/certificates/{cert_id}/replace-pem")
async def certificate_replace_pem(
    cert_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    fullchain_pem: str = Form(...),
    privkey_pem: str = Form(...),
):
    cert = cert_svc.get_certificate(session, cert_id)
    if not cert:
        raise HTTPException(404)
    try:
        cert_svc.upsert_from_pems(
            session,
            name=cert.name,
            fullchain_pem=fullchain_pem,
            privkey_pem=privkey_pem,
            source=cert.source or "upload",
            source_integration_id=cert.source_integration_id,
            external_id=cert.external_id,
            auto_renew=cert.auto_renew,
            renew_days_before=cert.renew_days_before,
            existing=cert,
        )
        _audit(session, user, "cert_pem_replaced", details=f"cert={cert_id}")
        return _redirect(f"/certificates/{cert_id}", msg="replaced")
    except ValueError as e:
        return _redirect(
            f"/certificates/{cert_id}", error="invalid", detail=str(e)[:200]
        )


@router.post("/certificates/{cert_id}/targets")
async def certificate_add_target(
    cert_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    server_id: int = Form(...),
    remote_dir: str = Form("~/certs"),
    layout: str = Form("pair"),
    fullchain_filename: str = Form("fullchain.pem"),
    privkey_filename: str = Form("privkey.pem"),
    combined_filename: str = Form("snakeoil.pem"),
    pfx_filename: str = Form("Certificate.pfx"),
    file_mode: str = Form("600"),
    file_owner: str = Form(""),
    file_group: str = Form(""),
    pfx_export_password: str = Form(""),
    post_deploy_command: str = Form(""),
):
    try:
        t = cert_svc.create_target(
            session,
            certificate_id=cert_id,
            server_id=server_id,
            remote_dir=remote_dir,
            layout=layout,
            fullchain_filename=fullchain_filename,
            privkey_filename=privkey_filename,
            combined_filename=combined_filename,
            pfx_filename=pfx_filename,
            file_mode=file_mode,
            file_owner=file_owner,
            file_group=file_group,
            pfx_export_password=pfx_export_password,
            post_deploy_command=post_deploy_command,
        )
        _audit(
            session,
            user,
            "cert_target_added",
            server_id=server_id,
            details=f"cert={cert_id} target={t.id}",
        )
        return _redirect(f"/certificates/{cert_id}", msg="target_added")
    except ValueError as e:
        return _redirect(
            f"/certificates/{cert_id}", error="invalid", detail=str(e)[:200]
        )


@router.post("/certificates/{cert_id}/targets/{target_id}/delete")
async def certificate_delete_target(
    cert_id: int,
    target_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    cert_svc.delete_target(session, target_id)
    _audit(session, user, "cert_target_deleted", details=f"target={target_id}")
    return _redirect(f"/certificates/{cert_id}", msg="target_deleted")


@router.post("/certificates/{cert_id}/deploy")
async def certificate_deploy(
    cert_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    target_id: Optional[int] = Form(None),
    force: Optional[str] = Form(None),
):
    cert = cert_svc.get_certificate(session, cert_id)
    if not cert:
        raise HTTPException(404)
    force_b = force in ("on", "1", "true")
    if target_id:
        result = cert_svc.deploy_target(session, int(target_id), force=force_b)
        ok = result.get("ok")
        _audit(
            session,
            user,
            "cert_deploy",
            server_id=result.get("server_id"),
            details=f"cert={cert_id} target={target_id} ok={ok}",
            status="success" if ok else "failed",
        )
        if ok:
            return _redirect(f"/certificates/{cert_id}", msg="deployed")
        return _redirect(
            f"/certificates/{cert_id}",
            error="deploy_failed",
            detail=(result.get("error") or "")[:200],
        )
    result = cert_svc.deploy_all_targets(session, cert_id, force=force_b)
    _audit(
        session,
        user,
        "cert_deploy_all",
        details=f"cert={cert_id} ok={result.get('ok')} count={result.get('count')}",
        status="success" if result.get("ok") else "failed",
    )
    if result.get("ok"):
        return _redirect(f"/certificates/{cert_id}", msg="deployed")
    return _redirect(
        f"/certificates/{cert_id}",
        error="deploy_failed",
        detail="one or more targets failed",
    )


@router.post("/certificates/{cert_id}/renew")
async def certificate_renew(
    cert_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    cert = cert_svc.get_certificate(session, cert_id)
    if not cert:
        raise HTTPException(404)
    if cert.source != "npm":
        return _redirect(
            f"/certificates/{cert_id}",
            error="invalid",
            detail="Only NPM-sourced certificates can be renewed here. Re-upload PEMs for manual certs.",
        )
    # Shorter poll for interactive UI; scheduler uses full 3m×5
    result = cert_svc.renew_npm_certificate(
        session, cert, poll_interval_sec=10, poll_attempts=3
    )
    _audit(
        session,
        user,
        "cert_renew",
        details=f"cert={cert_id} ok={result.get('ok')}",
        status="success" if result.get("ok") else "failed",
    )
    if result.get("ok"):
        return _redirect(f"/certificates/{cert_id}", msg="renewed")
    return _redirect(
        f"/certificates/{cert_id}",
        error="renew_failed",
        detail=(result.get("error") or "")[:200],
    )


@router.post("/certificates/{cert_id}/delete")
async def certificate_delete(
    cert_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    cert_svc.delete_certificate(session, cert_id)
    _audit(session, user, "cert_deleted", details=f"cert={cert_id}")
    return _redirect("/certificates", msg="deleted")

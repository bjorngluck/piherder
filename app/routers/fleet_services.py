"""Fleet-wide Services view + logo serve/upload/discover."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlmodel import Session

from .. import templates as templates_mod
from ..database import get_session
from ..models import IntegrationBinding, Server, User
from ..security.auth import get_current_user, get_operator_user, role_at_least, ROLE_OPERATOR
from ..services import service_logos as logos
from ..services.integrations import registry as integ_reg

logger = logging.getLogger(__name__)
router = APIRouter(tags=["fleet-services"])


@router.get("/services", response_class=HTMLResponse)
async def fleet_services_page(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Icon grid of all monitored services across the fleet."""
    services = integ_reg.fleet_service_chips(session)
    up = sum(1 for s in services if s.get("state") == "up")
    down = sum(1 for s in services if s.get("state") == "down")
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="fleet_services.html",
        context={
            "title": "Services",
            "user": user,
            "services": services,
            "service_count": len(services),
            "up_count": up,
            "down_count": down,
            "can_mutate": role_at_least(user, ROLE_OPERATOR),
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
            "lean_page": True,
        },
    )


@router.get("/services/logo/{binding_id}")
async def service_logo(
    binding_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    b = session.get(IntegrationBinding, binding_id)
    if not b or not b.logo_path:
        raise HTTPException(404, "Logo not found")
    path = logos.absolute_logo_path(b.logo_path)
    if not path:
        raise HTTPException(404, "Logo file missing")
    return FileResponse(
        path,
        media_type=logos.content_type_for_path(path),
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("/services/{binding_id}/logo")
async def upload_service_logo(
    binding_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    file: UploadFile = File(...),
    next: str = Form("/services"),
):
    b = session.get(IntegrationBinding, binding_id)
    if not b or b.role != integ_reg.ROLE_SERVICE:
        raise HTTPException(404, "Service binding not found")
    data = await file.read()
    try:
        rel = logos.save_logo_bytes(binding_id, data, file.content_type or "")
    except ValueError as e:
        dest = next or "/services"
        sep = "&" if "?" in dest else "?"
        return RedirectResponse(
            f"{dest}{sep}error=logo&detail={str(e)[:120]}", status_code=303
        )
    b.logo_path = rel
    session.add(b)
    session.commit()
    dest = next or "/services"
    sep = "&" if "?" in dest else "?"
    return RedirectResponse(f"{dest}{sep}msg=logo_saved", status_code=303)


@router.post("/services/{binding_id}/logo/discover")
async def rediscover_service_logo(
    binding_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    next: str = Form("/services"),
):
    b = session.get(IntegrationBinding, binding_id)
    if not b or b.role != integ_reg.ROLE_SERVICE:
        raise HTTPException(404, "Service binding not found")
    # Clear so discover always re-fetches
    if b.logo_path:
        logos.delete_logo_files(binding_id)
        b.logo_path = None
        session.add(b)
        session.commit()
        session.refresh(b)
    ok = integ_reg.maybe_discover_logo(session, b)
    dest = next or "/services"
    sep = "&" if "?" in dest else "?"
    if ok:
        return RedirectResponse(f"{dest}{sep}msg=logo_discovered", status_code=303)
    return RedirectResponse(
        f"{dest}{sep}error=logo&detail=Could+not+find+a+favicon",
        status_code=303,
    )


@router.post("/services/{binding_id}/logo/delete")
async def delete_service_logo(
    binding_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    next: str = Form("/services"),
):
    b = session.get(IntegrationBinding, binding_id)
    if not b:
        raise HTTPException(404)
    logos.delete_logo_files(binding_id)
    b.logo_path = None
    session.add(b)
    session.commit()
    dest = next or "/services"
    sep = "&" if "?" in dest else "?"
    return RedirectResponse(f"{dest}{sep}msg=logo_deleted", status_code=303)

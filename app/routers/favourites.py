"""Favourites API + form actions (J)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session

from ..database import get_session
from ..models import User
from ..security.auth import get_current_user
from ..services import nav_shortcuts as nav

router = APIRouter(tags=["favourites"])


def _safe_next(raw: str | None, fallback: str = "/") -> str:
    t = (raw or "").strip()
    if t.startswith("/") and not t.startswith("//") and "://" not in t:
        return t[:500]
    return fallback


def _redirect_after_pin(dest: str) -> RedirectResponse:
    """303 redirect after pin toggle. No flash msg — star state is the feedback.

    Preserves #fragments (maps need #map to open the SVG).
    """
    dest = (dest or "/").strip() or "/"
    # Prefer allowlisted map hrefs if someone POSTed bare /dns/physical
    if dest in ("/dns/physical", "/dns/physical/"):
        dest = "/dns/physical#map"
    elif dest in ("/dns/logical", "/dns/logical/"):
        dest = "/dns/logical#map"
    return RedirectResponse(dest, status_code=303)


@router.get("/account/favourites.json")
async def favourites_json(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    items = nav.list_favourites(session, int(user.id))  # type: ignore[arg-type]
    return JSONResponse({"items": items, "count": len(items)})


@router.post("/account/favourites/toggle")
async def favourites_toggle(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    kind: str = Form("server_feature"),
    server_id: str = Form(""),
    feature: str = Form(""),
    page: str = Form(""),
    integration_id: str = Form(""),
    next: str = Form(""),
):
    """Toggle a pin. Kind: server_feature | app_page | integration."""
    k = (kind or nav.KIND_SERVER_FEATURE).strip().lower()
    uid = int(user.id)  # type: ignore[arg-type]
    try:
        if k == nav.KIND_APP_PAGE:
            p = (page or feature or "").strip()
            nav.toggle_app_page_favourite(session, uid, page=p)
            # Always use canonical href (includes #map for fabric pages)
            fallback = nav.app_page_href(p)
            # Ignore bare next without #map for map pages
            nxt = _safe_next(next, fallback=fallback)
            if p in ("hosts_map", "path_map"):
                dest = fallback
            else:
                dest = nxt
        elif k == nav.KIND_INTEGRATION:
            iid = int((integration_id or feature or "0").strip() or "0")
            if iid <= 0:
                raise ValueError("integration_id required")
            nav.toggle_integration_favourite(
                session, uid, integration_id=iid
            )
            fallback = f"/integrations/{iid}"
            dest = _safe_next(next, fallback=fallback)
        else:
            # server_feature (default)
            sid = int((server_id or "0").strip() or "0")
            if sid <= 0:
                raise ValueError("server_id required")
            feat = (feature or "").strip()
            nav.toggle_server_favourite(
                session, uid, server_id=sid, feature=feat
            )
            fallback = nav.feature_href(sid, feat)
            dest = _safe_next(next, fallback=fallback)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _redirect_after_pin(dest)


@router.post("/account/favourites/{favourite_id}/remove")
async def favourites_remove(
    favourite_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    next: str = Form(""),
):
    nav.remove_favourite(session, int(user.id), favourite_id)  # type: ignore[arg-type]
    dest = _safe_next(next, fallback="/")
    return RedirectResponse(dest, status_code=303)

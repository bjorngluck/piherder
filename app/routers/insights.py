"""Operational Reports from Job history (Stream N)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from .. import templates as templates_mod
from ..database import get_session
from ..models import User
from ..security.auth import get_current_user
from ..services.ops_reports import clamp_report_days, collect_ops_reports
from ..services.report_layout import (
    apply_action,
    attach_layout_cookie,
    cards_for_template,
    layout_from_request,
)

router = APIRouter(tags=["reports"])


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    days: str = "",
):
    window = clamp_report_days(days)
    data = collect_ops_reports(session, days=window)
    cards = cards_for_template(layout_from_request(request))
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="reports.html",
        context={
            "title": "Reports",
            "user": user,
            "days": data["days"],
            "day_choices": data["day_choices"],
            "backup": data["backup"],
            "os_patch": data["os_patch"],
            "lan": data["lan"],
            "docker": data["docker"],
            "move": data["move"],
            "console": data["console"],
            "layout_visible": cards["visible"],
            "layout_hidden": cards["hidden"],
            "layout_is_default": cards["is_default"],
        },
    )


@router.post("/reports/layout")
async def reports_layout(
    request: Request,
    user: User = Depends(get_current_user),
    action: str = Form(""),
    card: str = Form(""),
    days: str = Form(""),
):
    """Personal Reports chrome (pin / hide / reorder). Not fleet data."""
    _ = user
    layout = apply_action(layout_from_request(request), action, card)
    window = clamp_report_days(days)
    resp = RedirectResponse(url=f"/reports?days={window}", status_code=303)
    attach_layout_cookie(resp, layout)
    return resp

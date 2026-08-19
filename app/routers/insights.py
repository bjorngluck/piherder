"""Operational Reports from Job history (Stream N)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from .. import templates as templates_mod
from ..database import get_session
from ..models import User
from ..security.auth import get_current_user
from ..services.ops_reports import clamp_report_days, collect_ops_reports

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
            "console": data["console"],
        },
    )

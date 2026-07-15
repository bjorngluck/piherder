from typing import Optional

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlmodel import Session, select

from ..database import get_session
from ..models import User, Server, Notification
from ..security.auth import get_current_user
from ..services import notifications as notif_svc
from .. import templates as templates_mod

router = APIRouter()


@router.get("/notifications", response_class=HTMLResponse)
async def notifications_page(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    status: str = "open",
    type: str = "",
    server_id: Optional[str] = None,
):
    sid = None
    if server_id and server_id.strip():
        try:
            sid = int(server_id)
        except ValueError:
            sid = None
    status_filter = status if status in ("open", "dismissed", "resolved", "all") else "open"
    st = None if status_filter == "all" else status_filter
    rows = notif_svc.list_notifications(
        session,
        status=st,
        type=type or None,
        server_id=sid,
        limit=150,
    )
    servers = list(session.exec(select(Server).order_by(Server.name)).all())
    server_map = {s.id: s.name for s in servers}
    items = []
    pulse = {
        "open": 0,
        "dismissed": 0,
        "resolved": 0,
        "total": 0,
        # Severity across ALL statuses (open + dismissed + resolved)
        "critical": 0,
        "warning": 0,
        "info": 0,
        # Severity among open only (for health / urgency)
        "open_critical": 0,
        "open_warning": 0,
        "open_info": 0,
        "shown": 0,
        "by_type": {},
    }
    open_n = notif_svc.open_count(session)
    pulse["open"] = open_n
    # Fleet-wide stats (all statuses) for hero breakdown
    try:
        all_rows = notif_svc.list_notifications(session, status=None, limit=800)
        pulse["total"] = len(all_rows)
        for n in all_rows:
            st = (n.status or "").lower()
            if st == "open":
                pulse["open"] = pulse.get("open", 0)  # keep open_count as source of truth below
            elif st == "dismissed":
                pulse["dismissed"] += 1
            elif st == "resolved":
                pulse["resolved"] += 1
            sev = (n.severity or "warning").lower()
            if sev == "critical":
                pulse["critical"] += 1
            elif sev == "info":
                pulse["info"] += 1
            else:
                pulse["warning"] += 1
            if st == "open":
                if sev == "critical":
                    pulse["open_critical"] += 1
                elif sev == "info":
                    pulse["open_info"] += 1
                else:
                    pulse["open_warning"] += 1
        # Prefer exact open_count (same as bell badge)
        pulse["open"] = open_n
        if pulse["total"] == 0:
            pulse["total"] = open_n + pulse["dismissed"] + pulse["resolved"]
    except Exception:
        pulse["open"] = open_n
        pulse["total"] = open_n
    for n in rows:
        d = n.model_dump()
        d["server_name"] = server_map.get(n.server_id) if n.server_id else None
        items.append(d)
        pulse["shown"] += 1
        t = n.type or "other"
        pulse["by_type"][t] = pulse["by_type"].get(t, 0) + 1

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="notifications.html",
        context={
            "title": "Notifications",
            "user": user,
            "items": items,
            "servers": servers,
            "status": status_filter,
            "type_filter": type,
            "server_id": sid,
            "open_count": open_n,
            "pulse": pulse,
        },
    )


@router.get("/notifications/count")
async def notifications_count(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return JSONResponse({"count": notif_svc.open_count(session)})


@router.get("/notifications/preview")
async def notifications_preview(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rows = notif_svc.list_notifications(session, status="open", limit=5)
    return JSONResponse({
        "count": notif_svc.open_count(session),
        "items": [
            {
                "id": n.id,
                "title": n.title,
                "body": n.body,
                "link_url": n.link_url,
                "severity": n.severity,
                "type": n.type,
            }
            for n in rows
        ],
    })


@router.post("/notifications/{notification_id}/dismiss")
async def dismiss_one(
    notification_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not notif_svc.dismiss(session, notification_id, user):
        return RedirectResponse(
            "/notifications?error=dismiss&msg=not_found",
            status_code=303,
        )
    return RedirectResponse("/notifications?dismissed=1", status_code=303)


@router.post("/notifications/dismiss-all")
async def dismiss_all(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    notif_svc.dismiss_all(session, user)
    return RedirectResponse("/notifications", status_code=303)

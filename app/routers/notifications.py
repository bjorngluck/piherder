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
    for n in rows:
        d = n.model_dump()
        d["server_name"] = server_map.get(n.server_id) if n.server_id else None
        items.append(d)

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
            "open_count": notif_svc.open_count(session),
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
        raise HTTPException(404)
    return RedirectResponse("/notifications", status_code=303)


@router.post("/notifications/dismiss-all")
async def dismiss_all(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    notif_svc.dismiss_all(session, user)
    return RedirectResponse("/notifications", status_code=303)

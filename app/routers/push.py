"""Web Push subscription APIs and account preference form."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session

from ..database import get_session
from ..models import User
from ..security.auth import get_current_user
from ..services import push as push_svc
from ..config import settings

router = APIRouter()


@router.get("/api/push/vapid-public-key")
async def vapid_public_key(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    key = push_svc.vapid_public_key(session)
    if not key:
        raise HTTPException(status_code=503, detail="Web Push is not available")
    return JSONResponse({"publicKey": key})


@router.get("/api/push/status")
async def push_status(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    creds = push_svc.ensure_vapid_keys(session)
    configured = bool(creds)
    pref = push_svc.get_or_create_preference(session, user.id)  # type: ignore[arg-type]
    subs = push_svc.list_subscriptions(session, user.id)  # type: ignore[arg-type]
    return JSONResponse(
        {
            "configured": configured,
            "vapid_source": creds.source if creds else None,
            "public_url": settings.PIHERDER_PUBLIC_URL,
            "hostname": settings.PIHERDER_HOSTNAME,
            "push_enabled": pref.push_enabled,
            "preferences": {
                "backup_failed": pref.backup_failed,
                "os_updates": pref.os_updates,
                "reboot_pending": pref.reboot_pending,
                "container_updates": pref.container_updates,
                "herder_backup_failed": pref.herder_backup_failed,
            },
            "subscription_count": len(subs),
            "subscriptions": [
                {
                    "id": s.id,
                    "endpoint_hint": (s.endpoint[:48] + "…") if len(s.endpoint) > 48 else s.endpoint,
                    "user_agent": s.user_agent,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in subs
            ],
        }
    )


@router.post("/api/push/subscribe")
async def push_subscribe(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not push_svc.ensure_vapid_keys(session):
        raise HTTPException(status_code=503, detail="Web Push is not available")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    endpoint = data.get("endpoint") or ""
    keys = data.get("keys") or {}
    p256dh = data.get("p256dh") or keys.get("p256dh") or ""
    auth = data.get("auth") or keys.get("auth") or ""
    ua = request.headers.get("user-agent")

    try:
        sub = push_svc.save_subscription(
            session,
            user,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=ua,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return JSONResponse({"ok": True, "id": sub.id})


@router.post("/api/push/unsubscribe")
async def push_unsubscribe(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    endpoint = None
    try:
        data = await request.json()
        endpoint = (data or {}).get("endpoint")
    except Exception:
        endpoint = None
    n = push_svc.remove_subscription(session, user, endpoint=endpoint)
    return JSONResponse({"ok": True, "removed": n})


@router.post("/api/push/test")
async def push_test_api(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """JSON test push for this user only (all of their device subscriptions)."""
    result = push_svc.send_test_to_user(session, user)
    if result.get("error") == "vapid_unavailable":
        raise HTTPException(status_code=503, detail="Web Push is not available")
    if result.get("error") == "no_subscription":
        raise HTTPException(
            status_code=400,
            detail="No push subscription on this account — enable on a device first",
        )
    return JSONResponse(result)


@router.post("/auth/account/push-test")
async def push_test_form(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Form POST from Account — redirect with flash-style query msg."""
    result = push_svc.send_test_to_user(session, user)
    err = result.get("error")
    if err == "vapid_unavailable":
        return RedirectResponse("/auth/account?error=push_vapid", status_code=303)
    if err == "no_subscription":
        return RedirectResponse("/auth/account?error=push_no_device", status_code=303)
    if not result.get("ok"):
        return RedirectResponse("/auth/account?error=push_test_failed", status_code=303)
    return RedirectResponse(
        f"/auth/account?msg=push_test_sent&push_sent={result.get('sent', 0)}",
        status_code=303,
    )


@router.post("/auth/account/push-preferences")
async def push_preferences_form(
    push_enabled: Optional[str] = Form(None),
    backup_failed: Optional[str] = Form(None),
    os_updates: Optional[str] = Form(None),
    reboot_pending: Optional[str] = Form(None),
    container_updates: Optional[str] = Form(None),
    herder_backup_failed: Optional[str] = Form(None),
    integration_down: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    def _on(v: Optional[str]) -> bool:
        return v in ("on", "true", "1", "yes")

    push_svc.update_preferences(
        session,
        user.id,  # type: ignore[arg-type]
        push_enabled=_on(push_enabled),
        backup_failed=_on(backup_failed),
        os_updates=_on(os_updates),
        reboot_pending=_on(reboot_pending),
        container_updates=_on(container_updates),
        herder_backup_failed=_on(herder_backup_failed),
        integration_down=_on(integration_down),
    )
    return RedirectResponse("/auth/account?msg=push_prefs_saved", status_code=303)

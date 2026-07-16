"""Admin user management routes (mounted under /auth)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from .. import templates as templates_mod
from ..database import get_session
from ..models import User, TotpBackupCode
from ..security.auth import (
    get_admin_user,
    get_password_hash,
    normalize_role,
    user_role,
    is_sole_admin,
    count_active_admins,
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    VALID_ROLES,
)
from ..config import settings
from ..services import password_policy as pwpol
from ..services.audit_write import make_audit_log
from ..services.request_ip import client_ip_from_request

router = APIRouter()


def _client_ip(request: Request) -> Optional[str]:
    return client_ip_from_request(request)


def _audit(session: Session, user_id: int, action: str, details: str, status: str = "success"):
    al = make_audit_log(
        user_id=user_id,
        server_id=None,
        action=action,
        status=status,
        details=details,
        started_at=datetime.utcnow(),
        finished_at=datetime.utcnow(),
    )
    session.add(al)
    session.commit()

@router.get("/users", response_class=HTMLResponse)
async def users_admin_page(
    request: Request,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Admin-only multi-user RBAC management + create user."""
    from ..services import password_policy as pwpol

    from ..services.ops_pulse import users_pulse as build_users_pulse

    users = list(session.exec(select(User).order_by(User.email)).all())
    sole_admin_ids = {u.id for u in users if is_sole_admin(session, u)}
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="users_admin.html",
        context={
            "title": "Users & roles",
            "user": admin,
            "users": users,
            "roles": [ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER],
            "sole_admin_ids": sole_admin_ids,
            "admin_count": count_active_admins(session),
            "msg": request.query_params.get("msg"),
            "error": request.query_params.get("error"),
            "password_policy_text": pwpol.policy_rules_text(),
            "password_min_length": pwpol.MIN_LENGTH,
            "new_user_credentials": None,
            "users_pulse": build_users_pulse(
                users,
                sole_admin_ids,
                role_admin=ROLE_ADMIN,
                role_operator=ROLE_OPERATOR,
                role_viewer=ROLE_VIEWER,
            ),
        },
    )


@router.post("/users/create")
async def create_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(ROLE_OPERATOR),
    display_name: str = Form(""),
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Admin creates a user with password (no self-registration required).

    On success, re-renders the users page with a one-time credentials card
    (password is never put in the URL).
    """
    from ..services import password_policy as pwpol

    email = (email or "").strip().lower()
    display_name = (display_name or "").strip() or None

    def _users_page(**extra):
        from ..services.ops_pulse import users_pulse as build_users_pulse

        users = list(session.exec(select(User).order_by(User.email)).all())
        sole_admin_ids = {u.id for u in users if is_sole_admin(session, u)}
        ctx = {
            "title": "Users & roles",
            "user": admin,
            "users": users,
            "roles": [ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER],
            "sole_admin_ids": sole_admin_ids,
            "admin_count": count_active_admins(session),
            "msg": None,
            "error": None,
            "password_policy_text": pwpol.policy_rules_text(),
            "password_min_length": pwpol.MIN_LENGTH,
            "new_user_credentials": None,
            "users_pulse": build_users_pulse(
                users,
                sole_admin_ids,
                role_admin=ROLE_ADMIN,
                role_operator=ROLE_OPERATOR,
                role_viewer=ROLE_VIEWER,
            ),
        }
        ctx.update(extra)
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="users_admin.html",
            context=ctx,
        )

    if not email or "@" not in email:
        return _users_page(error="bad_email")
    ok, err = pwpol.validate_password(password or "")
    if not ok:
        return _users_page(error="password_policy", error_detail=err)
    existing = session.exec(select(User).where(User.email == email)).first()
    if existing:
        return _users_page(error="email_taken")
    new_role = normalize_role(role)
    if new_role not in VALID_ROLES:
        new_role = ROLE_OPERATOR
    try:
        created = User(
            email=email,
            hashed_password=get_password_hash(password),
            role=new_role,
            display_name=display_name,
            must_change_password=True,  # force reset on first login
        )
        session.add(created)
        session.commit()
        session.refresh(created)
        _audit(
            session,
            admin.id,
            "user_created",
            f"Created {email} as {new_role}",
        )
        # Prefer external URL from request (works behind Caddy)
        base = str(request.base_url).rstrip("/")
        login_url = f"{base}/auth/login"
        invite = pwpol.format_invite_text(
            email=email,
            password=password,
            role=new_role,
            login_url=login_url,
            display_name=display_name,
        )
        return _users_page(
            msg="user_created",
            new_user_credentials={
                "email": email,
                "password": password,
                "role": new_role,
                "display_name": display_name or "",
                "login_url": login_url,
                "invite_text": invite,
            },
        )
    except Exception:
        return _users_page(error="create_failed")


@router.post("/users/{target_id}/role")
async def set_user_role(
    target_id: int,
    role: str = Form(...),
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    target = session.get(User, target_id)
    if not target:
        raise HTTPException(404)
    new_role = normalize_role(role)
    if new_role not in VALID_ROLES:
        return RedirectResponse("/auth/users?error=bad_role", status_code=303)
    # Always keep at least one admin — sole admin cannot change own (or any last) role away
    if user_role(target) == ROLE_ADMIN and new_role != ROLE_ADMIN:
        if is_sole_admin(session, target):
            return RedirectResponse("/auth/users?error=last_admin", status_code=303)
    old = user_role(target)
    if old == new_role:
        return RedirectResponse("/auth/users?msg=role_saved", status_code=303)
    target.role = new_role
    target.updated_at = datetime.utcnow()
    session.add(target)
    session.commit()
    _audit(
        session,
        admin.id,
        "user_role_changed",
        f"{target.email}: {old} → {new_role}",
    )
    return RedirectResponse("/auth/users?msg=role_saved", status_code=303)


@router.post("/users/{target_id}/delete")
async def delete_user(
    target_id: int,
    confirm: Optional[str] = Form(None),
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Delete a user (admin only). Cannot delete self or the last admin."""
    target = session.get(User, target_id)
    if not target:
        raise HTTPException(404)
    if target.id == admin.id:
        return RedirectResponse("/auth/users?error=delete_self", status_code=303)
    if is_sole_admin(session, target):
        return RedirectResponse("/auth/users?error=last_admin", status_code=303)
    if confirm not in ("1", "on", "true", "yes", "DELETE"):
        return RedirectResponse("/auth/users?error=delete_confirm", status_code=303)

    email = target.email
    # Remove 2FA / device rows first (no ON DELETE CASCADE assumed)
    for row in session.exec(select(TotpBackupCode).where(TotpBackupCode.user_id == target.id)).all():
        session.delete(row)
    from ..models import TrustedDevice
    for row in session.exec(select(TrustedDevice).where(TrustedDevice.user_id == target.id)).all():
        session.delete(row)
    # Leave audit rows; null user_id so history remains
    for al in session.exec(select(AuditLog).where(AuditLog.user_id == target.id)).all():
        al.user_id = None
        session.add(al)
    session.delete(target)
    session.commit()
    _audit(session, admin.id, "user_deleted", f"Deleted user {email}")
    return RedirectResponse("/auth/users?msg=user_deleted", status_code=303)



"""Admin user management routes (mounted under /auth)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from .. import templates as templates_mod
from ..database import get_session
from ..models import User
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
from ..services import password_policy as pwpol
from ..services.audit_write import make_audit_log
from ..services.request_ip import client_ip_from_request
from ..services.user_admin import (
    admin_clear_2fa_only,
    admin_reset_password,
    admin_sign_out_sessions,
)

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


def _users_page_response(
    request: Request,
    session: Session,
    admin: User,
    **extra: Any,
):
    """Shared Users admin TemplateResponse (create/reset credentials modals)."""
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


def _login_url(request: Request) -> str:
    return f"{str(request.base_url).rstrip('/')}/auth/login"


def _credentials_payload(
    request: Request,
    *,
    email: str,
    password: str,
    role: str,
    display_name: str | None,
    kind: str,
) -> dict[str, Any]:
    login_url = _login_url(request)
    invite = pwpol.format_invite_text(
        email=email,
        password=password,
        role=role,
        login_url=login_url,
        display_name=display_name,
    )
    return {
        "email": email,
        "password": password,
        "role": role,
        "display_name": display_name or "",
        "login_url": login_url,
        "invite_text": invite,
        "kind": kind,  # create | reset | reset_access
    }


@router.get("/users", response_class=HTMLResponse)
async def users_admin_page(
    request: Request,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Admin-only multi-user RBAC management + create user."""
    return _users_page_response(
        request,
        session,
        admin,
        msg=request.query_params.get("msg"),
        error=request.query_params.get("error"),
        new_user_credentials=None,
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
    email = (email or "").strip().lower()
    display_name = (display_name or "").strip() or None

    def _page(**extra):
        return _users_page_response(request, session, admin, **extra)

    if not email or "@" not in email:
        return _page(error="bad_email")
    ok, err = pwpol.validate_password(password or "")
    if not ok:
        return _page(error="password_policy", error_detail=err)
    existing = session.exec(select(User).where(User.email == email)).first()
    if existing:
        return _page(error="email_taken")
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
        return _page(
            msg="user_created",
            new_user_credentials=_credentials_payload(
                request,
                email=email,
                password=password,
                role=new_role,
                display_name=display_name,
                kind="create",
            ),
        )
    except Exception:
        return _page(error="create_failed")


@router.post("/users/{target_id}/reset-password")
async def reset_user_password(
    request: Request,
    target_id: int,
    password: str = Form(...),
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Admin sets a temporary password; user must change it on next login.

    Invalidates all sessions and trusted devices. Password shown once.
    """
    target = session.get(User, target_id)
    if not target:
        raise HTTPException(404)
    ok, err = pwpol.validate_password(password or "")
    if not ok:
        return _users_page_response(
            request, session, admin, error="password_policy", error_detail=err
        )
    admin_reset_password(session, target, password, clear_2fa=False)
    session.commit()
    _audit(
        session,
        admin.id,
        "admin_password_reset",
        f"Temporary password set for {target.email}; sessions revoked",
    )
    return _users_page_response(
        request,
        session,
        admin,
        msg="password_reset",
        new_user_credentials=_credentials_payload(
            request,
            email=target.email,
            password=password,
            role=user_role(target),
            display_name=target.display_name,
            kind="reset",
        ),
    )


@router.post("/users/{target_id}/clear-2fa")
async def clear_user_2fa(
    request: Request,
    target_id: int,
    confirm: Optional[str] = Form(None),
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Admin removes 2FA so the user can re-enrol (lost authenticator)."""
    target = session.get(User, target_id)
    if not target:
        raise HTTPException(404)
    if confirm not in ("1", "on", "true", "yes"):
        return RedirectResponse("/auth/users?error=clear_2fa_confirm", status_code=303)
    had_2fa = bool(target.totp_enabled or target.totp_secret_encrypted)
    admin_clear_2fa_only(session, target)
    session.commit()
    _audit(
        session,
        admin.id,
        "admin_2fa_cleared",
        f"Cleared 2FA for {target.email}"
        + ("" if had_2fa else " (was already off)"),
    )
    return RedirectResponse("/auth/users?msg=2fa_cleared", status_code=303)


@router.post("/users/{target_id}/reset-access")
async def reset_user_access(
    request: Request,
    target_id: int,
    password: str = Form(...),
    confirm: Optional[str] = Form(None),
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Full lockout recovery: temp password + clear 2FA + kill sessions.

    Cannot target self (use Account / clear-2FA + Account password, or another admin).
    """
    target = session.get(User, target_id)
    if not target:
        raise HTTPException(404)
    if target.id == admin.id:
        return RedirectResponse("/auth/users?error=reset_self", status_code=303)
    if confirm not in ("1", "on", "true", "yes"):
        return RedirectResponse("/auth/users?error=reset_access_confirm", status_code=303)
    ok, err = pwpol.validate_password(password or "")
    if not ok:
        return _users_page_response(
            request, session, admin, error="password_policy", error_detail=err
        )
    admin_reset_password(session, target, password, clear_2fa=True)
    session.commit()
    _audit(
        session,
        admin.id,
        "admin_access_reset",
        f"Full access reset for {target.email} (password + 2FA + sessions)",
    )
    return _users_page_response(
        request,
        session,
        admin,
        msg="access_reset",
        new_user_credentials=_credentials_payload(
            request,
            email=target.email,
            password=password,
            role=user_role(target),
            display_name=target.display_name,
            kind="reset_access",
        ),
    )


@router.post("/users/{target_id}/sign-out-sessions")
async def sign_out_user_sessions(
    target_id: int,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Force logout everywhere for a user (JWT session_version + trusted devices)."""
    target = session.get(User, target_id)
    if not target:
        raise HTTPException(404)
    admin_sign_out_sessions(session, target)
    session.commit()
    _audit(
        session,
        admin.id,
        "admin_sessions_revoked",
        f"Signed out all sessions for {target.email}",
    )
    # If admin signed out themselves, cookie is now invalid → login
    if target.id == admin.id:
        response = RedirectResponse("/auth/login?msg=sessions_revoked", status_code=303)
        response.delete_cookie("access_token", path="/")
        return response
    return RedirectResponse("/auth/users?msg=sessions_revoked", status_code=303)


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
    request: Request,
    target_id: int,
    confirm: Optional[str] = Form(None),
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Delete a user (admin only). Cannot delete self or the last admin."""
    from ..services.user_admin import detach_and_delete_user

    target = session.get(User, target_id)
    if not target:
        raise HTTPException(404)
    if target.id == admin.id:
        return RedirectResponse("/auth/users?error=delete_self", status_code=303)
    if is_sole_admin(session, target):
        return RedirectResponse("/auth/users?error=last_admin", status_code=303)
    if confirm not in ("1", "on", "true", "yes", "DELETE"):
        return RedirectResponse("/auth/users?error=delete_confirm", status_code=303)

    email = detach_and_delete_user(session, target)
    session.commit()
    al = make_audit_log(
        user_id=admin.id,
        server_id=None,
        action="user_deleted",
        status="success",
        details=f"Deleted user {email}",
        client_ip=_client_ip(request),
        started_at=datetime.utcnow(),
        finished_at=datetime.utcnow(),
    )
    session.add(al)
    session.commit()
    return RedirectResponse("/auth/users?msg=user_deleted", status_code=303)



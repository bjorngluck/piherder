from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, HTTPException, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from sqlmodel import Session, select

from ..database import get_session
from ..models import User, AuditLog, TotpBackupCode
from ..security.auth import (
    authenticate_user,
    create_access_token,
    create_pending_2fa_token,
    get_password_hash,
    get_current_user,
    get_admin_user,
    verify_password,
    decode_token_payload,
    rate_limit_auth,
    generate_totp_secret,
    encrypt_totp_secret,
    decrypt_totp_secret,
    totp_provisioning_uri,
    totp_qr_svg,
    totp_qr_data_uri,
    verify_totp_code,
    generate_backup_codes,
    replace_backup_codes,
    consume_backup_code,
    create_trusted_device,
    find_valid_trusted_device,
    revoke_trusted_device,
    revoke_all_trusted_devices,
    list_trusted_devices,
    normalize_role,
    user_role,
    is_sole_admin,
    count_active_admins,
    post_login_path,
    force_2fa_required,
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    VALID_ROLES,
)
from ..services import avatars as avatar_svc
from ..config import settings
from .. import templates as templates_mod

router = APIRouter()

TRUSTED_COOKIE = "trusted_device"
PENDING_COOKIE = "pending_2fa"


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def _audit(session: Session, user_id: int, action: str, details: str, status: str = "success"):
    al = AuditLog(
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


def _touch_last_login(session: Session, user: User) -> None:
    """Record successful interactive login time (Users admin UI)."""
    user.last_login_at = datetime.utcnow()
    session.add(user)
    session.commit()


def _set_auth_cookie(response: RedirectResponse, token: str):
    response.set_cookie("access_token", token, httponly=True, max_age=60 * 60 * 24 * 7, samesite="lax")


def _registration_allowed(session: Session) -> bool:
    if settings.ALLOW_OPEN_REGISTRATION:
        return True
    existing = session.exec(select(User)).first()
    return existing is None


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"title": "Login"}
    )


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session)
):
    ip = _client_ip(request) or "unknown"
    if not rate_limit_auth(f"login:{ip}"):
        return RedirectResponse("/auth/login?error=rate", status_code=303)

    user = authenticate_user(session, email, password)
    if not user:
        return RedirectResponse("/auth/login?error=invalid", status_code=303)

    # 2FA path (skip when user must change password first — they re-login after)
    if (
        user.totp_enabled
        and user.totp_secret_encrypted
        and not getattr(user, "must_change_password", False)
    ):
        raw_trusted = request.cookies.get(TRUSTED_COOKIE)
        if raw_trusted and find_valid_trusted_device(session, user.id, raw_trusted):
            _touch_last_login(session, user)
            token = create_access_token({"sub": str(user.id)})
            response = RedirectResponse(url=post_login_path(user), status_code=303)
            _set_auth_cookie(response, token)
            return response

        pending = create_pending_2fa_token(user.id)
        response = RedirectResponse(url="/auth/2fa", status_code=303)
        response.set_cookie(
            PENDING_COOKIE, pending, httponly=True, max_age=60 * 10, samesite="lax"
        )
        return response

    _touch_last_login(session, user)
    token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse(url=post_login_path(user), status_code=303)
    _set_auth_cookie(response, token)
    return response


@router.get("/2fa", response_class=HTMLResponse)
async def two_factor_page(request: Request):
    pending = request.cookies.get(PENDING_COOKIE)
    if not pending or not decode_token_payload(pending):
        return RedirectResponse("/auth/login", status_code=303)
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="two_factor.html",
        context={"title": "Two-factor authentication", "error": request.query_params.get("error")}
    )


@router.post("/2fa")
async def two_factor_submit(
    request: Request,
    code: str = Form(""),
    trust_device: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    ip = _client_ip(request) or "unknown"
    if not rate_limit_auth(f"2fa:{ip}", max_attempts=30):
        return RedirectResponse("/auth/2fa?error=rate", status_code=303)

    pending = request.cookies.get(PENDING_COOKIE)
    payload = decode_token_payload(pending) if pending else None
    if not payload or not payload.get("2fa_pending"):
        return RedirectResponse("/auth/login", status_code=303)

    user = session.get(User, int(payload["sub"]))
    if not user or not user.totp_enabled or not user.totp_secret_encrypted:
        return RedirectResponse("/auth/login", status_code=303)

    code = (code or "").strip()
    ok = False
    if code:
        try:
            secret = decrypt_totp_secret(user.totp_secret_encrypted)
            if verify_totp_code(secret, code):
                ok = True
            elif consume_backup_code(session, user.id, code):
                ok = True
        except Exception:
            ok = False

    if not ok:
        return RedirectResponse("/auth/2fa?error=invalid", status_code=303)

    _touch_last_login(session, user)
    token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse(url=post_login_path(user), status_code=303)
    _set_auth_cookie(response, token)
    response.delete_cookie(PENDING_COOKIE)

    if trust_device in ("1", "on", "true"):
        raw, _dev = create_trusted_device(
            session,
            user.id,
            label="Browser",
            user_agent=request.headers.get("user-agent"),
            ip=ip,
        )
        response.set_cookie(
            TRUSTED_COOKIE,
            raw,
            httponly=True,
            max_age=60 * 60 * 24 * settings.TRUSTED_DEVICE_DAYS,
            samesite="lax",
        )
    return response


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, session: Session = Depends(get_session)):
    if not _registration_allowed(session):
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "title": "Register",
                "error": "Registration is closed. Ask an admin or set ALLOW_OPEN_REGISTRATION=true.",
                "closed": True,
            },
        )
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="register.html",
        context={"title": "Register"}
    )


@router.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session)
):
    if not _registration_allowed(session):
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "title": "Register",
                "error": "Registration is closed.",
                "closed": True,
            },
        )

    existing = session.exec(select(User).where(User.email == email)).first()
    if existing:
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="register.html",
            context={"title": "Register", "error": "User with that email already exists"}
        )
    from ..services import password_policy as pwpol

    ok, pol_err = pwpol.validate_password(password or "")
    if not ok:
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="register.html",
            context={"title": "Register", "error": pol_err or "Password does not meet policy"},
        )
    try:
        hashed = get_password_hash(password)
        # First user is admin; later open-registration users start as operator
        is_first = session.exec(select(User)).first() is None
        user = User(
            email=email,
            hashed_password=hashed,
            role=ROLE_ADMIN if is_first else ROLE_OPERATOR,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return RedirectResponse("/auth/login", status_code=303)
    except Exception:
        msg = "Registration failed. Please try a different email or shorter password."
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="register.html",
            context={"title": "Register", "error": msg}
        )


@router.get("/logout")
async def logout():
    """Clear auth cookie and return to login. (GET is acceptable for logout in this app.)"""
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie("access_token")
    response.delete_cookie(PENDING_COOKIE)
    return response


@router.get("/account", response_class=HTMLResponse)
async def account_page(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    msg = request.query_params.get("msg")
    err = request.query_params.get("error")
    push_sent = request.query_params.get("push_sent")
    devices = list_trusted_devices(session, user.id)
    backup_remaining = len(
        session.exec(
            select(TotpBackupCode).where(
                TotpBackupCode.user_id == user.id,
                TotpBackupCode.used_at.is_(None),
            )
        ).all()
    )
    setup_secret = request.cookies.get("totp_setup_secret")
    # Pending unconfirmed secret on user row (preferred — survives cookie loss)
    pending_setup = bool(
        (user.totp_secret_encrypted and not user.totp_enabled) or setup_secret
    )
    if pending_setup and not setup_secret and user.totp_secret_encrypted:
        try:
            setup_secret = decrypt_totp_secret(user.totp_secret_encrypted)
        except Exception:
            setup_secret = None
    # Build QR in-process (SVG — no Pillow; never store QR in cookies — too large)
    setup_qr_svg = None
    setup_qr_uri = None
    setup_otpauth = None
    if pending_setup and setup_secret:
        try:
            setup_otpauth = totp_provisioning_uri(setup_secret, user.email)
            setup_qr_svg = totp_qr_svg(setup_otpauth)
            setup_qr_uri = totp_qr_data_uri(setup_otpauth)
        except Exception:
            setup_qr_svg = None
            setup_qr_uri = None
    show_2fa_modal = pending_setup or msg == "2fa_setup"
    backup_codes = request.query_params.get("backup_codes")

    from ..services import push as push_svc

    push_creds = None
    push_prefs = None
    push_subscription_count = 0
    try:
        push_creds = push_svc.ensure_vapid_keys(session)
        push_prefs = push_svc.get_or_create_preference(session, user.id)
        push_subscription_count = len(push_svc.list_subscriptions(session, user.id))
    except Exception:
        push_prefs = None

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="account.html",
        context={
            "title": "Account",
            "user": user,
            "msg": msg,
            "error": err,
            "devices": devices,
            "backup_remaining": backup_remaining,
            "setup_qr_svg": setup_qr_svg,
            "setup_qr_uri": setup_qr_uri,
            "setup_secret": setup_secret,
            "setup_otpauth": setup_otpauth,
            "pending_2fa_setup": pending_setup,
            "show_2fa_modal": show_2fa_modal,
            "backup_codes_shown": backup_codes.split(",") if backup_codes else None,
            "trusted_device_days": settings.TRUSTED_DEVICE_DAYS,
            "user_role": user_role(user),
            "is_admin": user_role(user) == ROLE_ADMIN,
            "push_configured": bool(push_creds),
            "push_vapid_source": push_creds.source if push_creds else None,
            "push_prefs": push_prefs,
            "push_subscription_count": push_subscription_count,
            "public_url": settings.PIHERDER_PUBLIC_URL,
            "push_sent": push_sent,
        },
    )


@router.post("/account/profile")
async def update_profile(
    display_name: str = Form(""),
    email: str = Form(...),
    current_password: str = Form(""),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    email = email.strip().lower()
    display_name = (display_name or "").strip() or None
    email_changed = email != user.email.lower()

    if email_changed:
        if not current_password or not verify_password(current_password, user.hashed_password):
            return RedirectResponse("/auth/account?error=password_required", status_code=303)
        taken = session.exec(select(User).where(User.email == email)).first()
        if taken and taken.id != user.id:
            return RedirectResponse("/auth/account?error=email_taken", status_code=303)
        user.email = email
        _audit(session, user.id, "user_email_changed", f"Email changed to {email}")

    user.display_name = display_name
    user.updated_at = datetime.utcnow()
    session.add(user)
    session.commit()
    if not email_changed:
        _audit(session, user.id, "user_profile_updated", "Profile updated")
    return RedirectResponse("/auth/account?msg=profile_saved", status_code=303)


@router.post("/account/password")
async def change_password(
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    from ..services import password_policy as pwpol

    if not verify_password(current_password, user.hashed_password):
        return RedirectResponse("/auth/account?error=bad_password", status_code=303)
    if new_password != confirm_password:
        return RedirectResponse("/auth/account?error=password_mismatch", status_code=303)
    ok, _err = pwpol.validate_password(new_password or "")
    if not ok:
        return RedirectResponse("/auth/account?error=password_policy", status_code=303)

    user.hashed_password = get_password_hash(new_password)
    user.must_change_password = False
    user.updated_at = datetime.utcnow()
    session.add(user)
    session.commit()
    revoke_all_trusted_devices(session, user.id)
    _audit(session, user.id, "user_password_changed", "Password changed; trusted devices revoked")
    return RedirectResponse("/auth/account?msg=password_changed", status_code=303)


@router.post("/account/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    data = await file.read()
    try:
        rel = avatar_svc.save_avatar(user.id, data)
    except ValueError as e:
        return RedirectResponse(f"/auth/account?error=avatar:{e}", status_code=303)
    user.avatar_path = rel
    user.updated_at = datetime.utcnow()
    session.add(user)
    session.commit()
    _audit(session, user.id, "user_avatar_updated", "Avatar uploaded")
    return RedirectResponse("/auth/account?msg=avatar_saved", status_code=303)


@router.post("/account/avatar/delete")
async def delete_avatar(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    avatar_svc.delete_avatar_files(user.id)
    user.avatar_path = None
    user.updated_at = datetime.utcnow()
    session.add(user)
    session.commit()
    _audit(session, user.id, "user_avatar_updated", "Avatar removed")
    return RedirectResponse("/auth/account?msg=avatar_deleted", status_code=303)


@router.get("/me/avatar")
async def my_avatar(user: User = Depends(get_current_user)):
    path = avatar_svc.absolute_avatar_path(user.avatar_path)
    if not path:
        raise HTTPException(404)
    return FileResponse(path, media_type=avatar_svc.content_type_for_path(path))


# --- 2FA management ---

@router.post("/account/2fa/start")
async def two_factor_start(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if user.totp_enabled:
        return RedirectResponse("/auth/account?error=2fa_already", status_code=303)
    secret = generate_totp_secret()
    user.totp_secret_encrypted = encrypt_totp_secret(secret)
    user.totp_enabled = False
    user.totp_confirmed_at = None
    session.add(user)
    session.commit()

    # Secret is stored encrypted on the user; QR is generated on the account page (SVG).
    # Optional short-lived cookie helps if DB read is delayed; not used for QR (size limits).
    response = RedirectResponse("/auth/account?msg=2fa_setup", status_code=303)
    response.set_cookie("totp_setup_secret", secret, httponly=True, max_age=600, samesite="lax")
    response.delete_cookie("totp_setup_qr")  # legacy oversized cookie
    return response


@router.post("/account/2fa/confirm")
async def two_factor_confirm(
    request: Request,
    code: str = Form(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    secret = request.cookies.get("totp_setup_secret")
    if not secret and user.totp_secret_encrypted and not user.totp_enabled:
        try:
            secret = decrypt_totp_secret(user.totp_secret_encrypted)
        except Exception:
            secret = None
    if not secret:
        return RedirectResponse("/auth/account?error=2fa_no_setup", status_code=303)
    if not verify_totp_code(secret, code):
        return RedirectResponse("/auth/account?error=2fa_bad_code", status_code=303)

    user.totp_secret_encrypted = encrypt_totp_secret(secret)
    user.totp_enabled = True
    user.totp_confirmed_at = datetime.utcnow()
    session.add(user)
    session.commit()

    codes = generate_backup_codes()
    replace_backup_codes(session, user.id, codes)
    _audit(session, user.id, "user_2fa_enabled", "TOTP 2FA enabled")

    response = RedirectResponse(
        f"/auth/account?msg=2fa_enabled&backup_codes={','.join(codes)}",
        status_code=303,
    )
    response.delete_cookie("totp_setup_secret")
    response.delete_cookie("totp_setup_qr")
    return response


@router.post("/account/2fa/disable")
async def two_factor_disable(
    current_password: str = Form(...),
    code: str = Form(""),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not verify_password(current_password, user.hashed_password):
        return RedirectResponse("/auth/account?error=bad_password", status_code=303)
    if user.totp_enabled and user.totp_secret_encrypted:
        secret = decrypt_totp_secret(user.totp_secret_encrypted)
        code_ok = verify_totp_code(secret, code) if code else False
        if not code_ok and not (code and consume_backup_code(session, user.id, code)):
            return RedirectResponse("/auth/account?error=2fa_bad_code", status_code=303)

    user.totp_enabled = False
    user.totp_secret_encrypted = None
    user.totp_confirmed_at = None
    session.add(user)
    session.commit()
    # Clear backup codes
    for row in session.exec(select(TotpBackupCode).where(TotpBackupCode.user_id == user.id)).all():
        session.delete(row)
    session.commit()
    revoke_all_trusted_devices(session, user.id)
    _audit(session, user.id, "user_2fa_disabled", "TOTP 2FA disabled")
    return RedirectResponse("/auth/account?msg=2fa_disabled", status_code=303)


@router.post("/account/2fa/backup-codes")
async def regenerate_backup_codes(
    current_password: str = Form(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not user.totp_enabled:
        return RedirectResponse("/auth/account?error=2fa_off", status_code=303)
    if not verify_password(current_password, user.hashed_password):
        return RedirectResponse("/auth/account?error=bad_password", status_code=303)
    codes = generate_backup_codes()
    replace_backup_codes(session, user.id, codes)
    revoke_all_trusted_devices(session, user.id)
    _audit(session, user.id, "user_2fa_backup_regenerated", "Backup codes regenerated")
    return RedirectResponse(
        f"/auth/account?msg=backup_codes&backup_codes={','.join(codes)}",
        status_code=303,
    )


@router.post("/account/trusted-devices/{device_id}/revoke")
async def revoke_device(
    device_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if revoke_trusted_device(session, user.id, device_id):
        _audit(session, user.id, "user_trusted_device_revoked", f"Device #{device_id}")
    return RedirectResponse("/auth/account?msg=device_revoked", status_code=303)


@router.post("/account/trusted-devices/revoke-all")
async def revoke_all_devices(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    n = revoke_all_trusted_devices(session, user.id)
    _audit(session, user.id, "user_trusted_device_revoked", f"Revoked all ({n})")
    return RedirectResponse("/auth/account?msg=devices_revoked", status_code=303)


@router.get("/users", response_class=HTMLResponse)
async def users_admin_page(
    request: Request,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Admin-only multi-user RBAC management + create user."""
    from ..services import password_policy as pwpol

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


@router.get("/force-password", response_class=HTMLResponse)
async def force_password_page(
    request: Request,
    user: User = Depends(get_current_user),
):
    from ..services import password_policy as pwpol

    if not getattr(user, "must_change_password", False):
        return RedirectResponse(post_login_path(user), status_code=303)
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="force_password.html",
        context={
            "title": "Set a new password",
            "user": user,
            "error": request.query_params.get("error"),
            "password_policy_text": pwpol.policy_rules_text(),
            "password_min_length": pwpol.MIN_LENGTH,
        },
    )


@router.post("/force-password")
async def force_password_submit(
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    from ..services import password_policy as pwpol

    if not getattr(user, "must_change_password", False):
        return RedirectResponse(post_login_path(user), status_code=303)
    if new_password != confirm_password:
        return RedirectResponse("/auth/force-password?error=mismatch", status_code=303)
    ok, _err = pwpol.validate_password(new_password or "")
    if not ok:
        return RedirectResponse("/auth/force-password?error=policy", status_code=303)
    # Disallow reusing the temporary password
    if verify_password(new_password, user.hashed_password):
        return RedirectResponse("/auth/force-password?error=same", status_code=303)

    user.hashed_password = get_password_hash(new_password)
    user.must_change_password = False
    user.updated_at = datetime.utcnow()
    session.add(user)
    session.commit()
    revoke_all_trusted_devices(session, user.id)
    _audit(session, user.id, "user_password_changed", "First-login password set")
    # Re-issue path for force 2FA if needed
    return RedirectResponse(post_login_path(user), status_code=303)


@router.get("/force-2fa", response_class=HTMLResponse)
async def force_2fa_page(
    request: Request,
    user: User = Depends(get_current_user),
):
    if not force_2fa_required() or user.totp_enabled:
        return RedirectResponse("/", status_code=303)
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="force_2fa.html",
        context={
            "title": "Two-factor authentication required",
            "user": user,
        },
    )

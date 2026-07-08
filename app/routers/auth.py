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

    # 2FA path
    if user.totp_enabled and user.totp_secret_encrypted:
        raw_trusted = request.cookies.get(TRUSTED_COOKIE)
        if raw_trusted and find_valid_trusted_device(session, user.id, raw_trusted):
            token = create_access_token({"sub": str(user.id)})
            response = RedirectResponse(url="/", status_code=303)
            _set_auth_cookie(response, token)
            return response

        pending = create_pending_2fa_token(user.id)
        response = RedirectResponse(url="/auth/2fa", status_code=303)
        response.set_cookie(
            PENDING_COOKIE, pending, httponly=True, max_age=60 * 10, samesite="lax"
        )
        return response

    token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse(url="/", status_code=303)
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

    token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse(url="/", status_code=303)
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
    try:
        hashed = get_password_hash(password)
        user = User(email=email, hashed_password=hashed)
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
    if not verify_password(current_password, user.hashed_password):
        return RedirectResponse("/auth/account?error=bad_password", status_code=303)
    if len(new_password) < 6:
        return RedirectResponse("/auth/account?error=password_short", status_code=303)
    if new_password != confirm_password:
        return RedirectResponse("/auth/account?error=password_mismatch", status_code=303)

    user.hashed_password = get_password_hash(new_password)
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

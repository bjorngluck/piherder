import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, HTTPException, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

from ..database import get_session
from ..models import User, TotpBackupCode
from ..services.audit_write import make_audit_log
from ..services.request_ip import client_ip_from_request
from ..security.auth import (
    authenticate_user,
    create_user_access_token,
    create_pending_2fa_token,
    create_account_stepup_token,
    account_stepup_active,
    ACCOUNT_STEPUP_COOKIE,
    ACCOUNT_STEPUP_MINUTES,
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
    ensure_trusted_device,
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
    cookie_auth_kwargs,
    cookie_delete_kwargs,
    trusted_cookie_name,
    read_trusted_device_token,
    TRUSTED_COOKIE_LEGACY,
    LOGIN_RATE_MAX,
    LOGIN_RATE_WINDOW,
    TWOFA_RATE_MAX,
    TWOFA_RATE_WINDOW,
    REGISTER_RATE_MAX,
    REGISTER_RATE_WINDOW,
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    VALID_ROLES,
)
from ..services import avatars as avatar_svc
from ..config import settings
from .. import templates as templates_mod

router = APIRouter()

PENDING_COOKIE = "pending_2fa"


def _set_trusted_device_cookie(response: Response, user_id: int, raw: str) -> None:
    """Persist 2FA skip token for this user only (survives logout)."""
    max_age = 60 * 60 * 24 * int(settings.TRUSTED_DEVICE_DAYS or 30)
    response.set_cookie(
        trusted_cookie_name(user_id),
        raw,
        **cookie_auth_kwargs(max_age=max_age),
    )
    # Drop legacy single-name cookie so it cannot fight multi-account browsers
    response.delete_cookie(TRUSTED_COOKIE_LEGACY, **cookie_delete_kwargs())


def _clear_trusted_device_cookie(response: Response, user_id: int) -> None:
    response.delete_cookie(trusted_cookie_name(user_id), **cookie_delete_kwargs())
    response.delete_cookie(TRUSTED_COOKIE_LEGACY, **cookie_delete_kwargs())


def _client_ip(request: Request) -> Optional[str]:
    """Prefer Caddy XFF / X-Real-IP; fall back to TCP peer."""
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


def _touch_last_login(session: Session, user: User) -> None:
    """Record successful interactive login time (Users admin UI)."""
    user.last_login_at = datetime.utcnow()
    session.add(user)
    session.commit()


def _set_auth_cookie(response: RedirectResponse, token: str):
    response.set_cookie(
        "access_token",
        token,
        **cookie_auth_kwargs(max_age=60 * 60 * 24 * 7),
    )


def _registration_allowed(session: Session) -> bool:
    # Public demo sandbox: never allow open or first-user registration
    from ..services.demo import demo_mode

    if demo_mode():
        return False
    if settings.ALLOW_OPEN_REGISTRATION:
        return True
    existing = session.exec(select(User)).first()
    return existing is None


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, session: Session = Depends(get_session)):
    from ..services import alert_channels as alert_ch
    from ..services import oidc_svc as oidc
    from ..services import turnstile as turnstile_svc
    from ..services.demo import demo_mode

    is_demo = demo_mode()
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "title": "Login",
            "registration_open": False if is_demo else _registration_allowed(session),
            "demo_mode_on": is_demo,
            # Shared demo: never offer forgot-password or SSO (vandalism / lockout)
            "password_reset_available": (
                False if is_demo else alert_ch.password_reset_available()
            ),
            "oidc_enabled": False if is_demo else oidc.oidc_enabled(),
            "oidc_display_name": oidc.oidc_display_name(),
            "oidc_require_sso": False if is_demo else oidc.oidc_require_sso(),
            "turnstile_on": turnstile_svc.turnstile_enabled(),
            "turnstile_site_key": turnstile_svc.turnstile_site_key(),
        },
    )


def _public_base_url(request: Request) -> str:
    """Best-effort public origin for reset links (proxy-aware)."""
    xf_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    xf_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host = xf_host or request.headers.get("host") or request.url.netloc
    scheme = xf_proto or request.url.scheme or "https"
    if not host:
        return str(request.base_url).rstrip("/")
    return f"{scheme}://{host}".rstrip("/")


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    from ..services import alert_channels as alert_ch
    from ..services.demo import redirect_if_demo

    blocked = redirect_if_demo("/auth/login")
    if blocked:
        return blocked
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="forgot_password.html",
        context={
            "title": "Forgot password",
            "available": alert_ch.password_reset_available(),
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or "",
        },
    )


@router.post("/forgot-password")
async def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    session: Session = Depends(get_session),
):
    from ..services import alert_channels as alert_ch
    from ..services import password_reset as pwreset
    from ..services.demo import redirect_if_demo

    blocked = redirect_if_demo("/auth/login")
    if blocked:
        return blocked
    ip = _client_ip(request) or "unknown"
    if not rate_limit_auth(
        f"forgot:{ip}", max_attempts=5, window_seconds=LOGIN_RATE_WINDOW
    ):
        return RedirectResponse("/auth/forgot-password?error=rate", status_code=303)
    if not alert_ch.password_reset_available():
        return RedirectResponse(
            "/auth/forgot-password?error=disabled", status_code=303
        )
    # Rate limit per email too (enumeration-resistant generic response)
    rate_limit_auth(
        f"forgot-email:{(email or '').strip().lower()[:120]}",
        max_attempts=3,
        window_seconds=LOGIN_RATE_WINDOW,
    )
    result = pwreset.request_reset_email(
        session,
        email,
        base_url=_public_base_url(request),
        request_ip=ip,
    )
    if not result.get("ok") and result.get("error") not in (None,):
        # SMTP send failure — still generic on UI if user missing; only hard fail when disabled
        if result.get("error") == "email recovery is not enabled":
            return RedirectResponse(
                "/auth/forgot-password?error=disabled", status_code=303
            )
    return RedirectResponse("/auth/forgot-password?msg=sent", status_code=303)


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = ""):
    from ..services.demo import redirect_if_demo

    blocked = redirect_if_demo("/auth/login")
    if blocked:
        return blocked
    tok = (token or request.query_params.get("token") or "").strip()
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="reset_password.html",
        context={
            "title": "Reset password",
            "token": tok,
            "error": request.query_params.get("error") or "",
        },
    )


@router.post("/reset-password")
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    session: Session = Depends(get_session),
):
    from ..services import password_policy as pwpol
    from ..services import password_reset as pwreset
    from ..services.user_admin import bump_session_version
    from ..services.demo import redirect_if_demo

    blocked = redirect_if_demo("/auth/login")
    if blocked:
        return blocked
    ip = _client_ip(request) or "unknown"
    if not rate_limit_auth(
        f"reset:{ip}", max_attempts=10, window_seconds=LOGIN_RATE_WINDOW
    ):
        return RedirectResponse(
            f"/auth/reset-password?token={token}&error=rate", status_code=303
        )
    if new_password != confirm_password:
        return RedirectResponse(
            f"/auth/reset-password?token={token}&error=mismatch", status_code=303
        )
    ok, _err = pwpol.validate_password(new_password or "")
    if not ok:
        return RedirectResponse(
            f"/auth/reset-password?token={token}&error=policy", status_code=303
        )
    user = pwreset.consume_token(session, token)
    if not user:
        return RedirectResponse(
            "/auth/reset-password?error=invalid", status_code=303
        )
    user.hashed_password = get_password_hash(new_password)
    user.must_change_password = False
    user.updated_at = datetime.utcnow()
    session.add(user)
    revoke_all_trusted_devices(session, user.id)
    bump_session_version(session, user)
    session.commit()
    _audit(
        session,
        user.id,
        "user_password_reset",
        "Password reset via email token; sessions + trusted devices revoked",
    )
    return RedirectResponse("/auth/login?msg=password_reset", status_code=303)


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
    cf_turnstile_response: Optional[str] = Form(None, alias="cf-turnstile-response"),
):
    ip = _client_ip(request) or "unknown"
    if not rate_limit_auth(
        f"login:{ip}", max_attempts=LOGIN_RATE_MAX, window_seconds=LOGIN_RATE_WINDOW
    ):
        return RedirectResponse("/auth/login?error=rate", status_code=303)

    from ..services import turnstile as turnstile_svc

    if turnstile_svc.turnstile_enabled():
        # Token: Form alias + raw form (hyphenated names can be flaky)
        token = (cf_turnstile_response or "").strip()
        if not token:
            try:
                form = await request.form()
                token = (
                    str(form.get("cf-turnstile-response") or form.get("cf_turnstile_response") or "")
                ).strip()
            except Exception:
                token = ""
        # Always send visitor remoteip (Caddy sets XFF from CF-Connecting-IP when orange-clouded)
        visitor = turnstile_svc.visitor_ip_for_turnstile(request) or (
            ip if ip != "unknown" else None
        )
        ok, code = turnstile_svc.verify_turnstile_token(token, remoteip=visitor)
        if not ok:
            logger.warning(
                "login captcha failed code=%s ip=%s visitor=%s token_len=%s",
                code,
                ip,
                visitor,
                len(token or ""),
            )
            return RedirectResponse("/auth/login?error=captcha", status_code=303)

    user = authenticate_user(session, email, password)
    if not user:
        try:
            al = make_audit_log(
                user_id=None,
                action="user_login_failed",
                status="failed",
                details=f"Invalid credentials for {(email or '')[:120]}",
                finished_at=datetime.utcnow(),
            )
            session.add(al)
            session.commit()
        except Exception:
            session.rollback()
        return RedirectResponse("/auth/login?error=invalid", status_code=303)

    # 2FA path: TOTP and/or passkeys (skip when user must change password first)
    from ..services import webauthn_svc as wa_svc

    if (
        wa_svc.user_requires_2fa_stepup(session, user)
        and not getattr(user, "must_change_password", False)
    ):
        raw_trusted = read_trusted_device_token(request.cookies, user.id)
        if raw_trusted and find_valid_trusted_device(session, user.id, raw_trusted):
            _touch_last_login(session, user)
            _audit(session, user.id, "user_login", "Login (trusted device, 2FA skipped)")
            token = create_user_access_token(user)
            response = RedirectResponse(
                url=post_login_path(user, session), status_code=303
            )
            _set_auth_cookie(response, token)
            # Refresh browser cookie lifetime (and migrate legacy → per-user name)
            _set_trusted_device_cookie(response, user.id, raw_trusted)
            return response

        pending = create_pending_2fa_token(user.id)
        response = RedirectResponse(url="/auth/2fa", status_code=303)
        response.set_cookie(
            PENDING_COOKIE,
            pending,
            **cookie_auth_kwargs(max_age=60 * 10),
        )
        return response

    _touch_last_login(session, user)
    _audit(session, user.id, "user_login", "Login")
    token = create_user_access_token(user)
    response = RedirectResponse(url=post_login_path(user, session), status_code=303)
    _set_auth_cookie(response, token)
    return response


def _pending_2fa_user(request: Request, session: Session) -> Optional[User]:
    pending = request.cookies.get(PENDING_COOKIE)
    payload = decode_token_payload(pending) if pending else None
    if not payload or not payload.get("2fa_pending"):
        return None
    try:
        user = session.get(User, int(payload["sub"]))
    except (TypeError, ValueError):
        return None
    return user


@router.get("/2fa", response_class=HTMLResponse)
async def two_factor_page(
    request: Request, session: Session = Depends(get_session)
):
    from ..services import webauthn_svc as wa_svc

    user = _pending_2fa_user(request, session)
    if not user:
        return RedirectResponse("/auth/login", status_code=303)
    has_totp = wa_svc.totp_active(user)
    has_passkey = wa_svc.has_passkeys(session, int(user.id))
    if not has_totp and not has_passkey:
        return RedirectResponse("/auth/login", status_code=303)
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="two_factor.html",
        context={
            "title": "Two-factor authentication",
            "error": request.query_params.get("error"),
            "trusted_device_days": settings.TRUSTED_DEVICE_DAYS,
            "has_totp": has_totp,
            "has_passkey": has_passkey,
        },
    )


@router.post("/2fa")
async def two_factor_submit(
    request: Request,
    code: str = Form(""),
    trust_device: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    from ..services import webauthn_svc as wa_svc

    ip = _client_ip(request) or "unknown"
    if not rate_limit_auth(
        f"2fa:{ip}", max_attempts=TWOFA_RATE_MAX, window_seconds=TWOFA_RATE_WINDOW
    ):
        return RedirectResponse("/auth/2fa?error=rate", status_code=303)

    user = _pending_2fa_user(request, session)
    if not user:
        return RedirectResponse("/auth/login", status_code=303)

    # TOTP / backup codes only on this form path (passkeys use /auth/2fa/webauthn/*)
    if not wa_svc.totp_active(user):
        return RedirectResponse("/auth/2fa?error=use_passkey", status_code=303)

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
        try:
            al = make_audit_log(
                user_id=user.id,
                action="user_login_failed",
                status="failed",
                details="Invalid 2FA code",
                finished_at=datetime.utcnow(),
            )
            session.add(al)
            session.commit()
        except Exception:
            session.rollback()
        return RedirectResponse("/auth/2fa?error=invalid", status_code=303)

    _touch_last_login(session, user)
    _audit(session, user.id, "user_login", "Login (2FA verified)")
    token = create_user_access_token(user)
    response = RedirectResponse(url=post_login_path(user, session), status_code=303)
    _set_auth_cookie(response, token)
    response.delete_cookie(PENDING_COOKIE, **cookie_delete_kwargs())

    if trust_device in ("1", "on", "true"):
        raw, _dev, _created = ensure_trusted_device(
            session,
            user.id,
            read_trusted_device_token(request.cookies, user.id),
            label="Browser",
            user_agent=request.headers.get("user-agent"),
            ip=ip,
        )
        _set_trusted_device_cookie(response, user.id, raw)
    return response


@router.post("/2fa/webauthn/options")
async def two_factor_webauthn_options(
    request: Request,
    session: Session = Depends(get_session),
):
    """JSON: PublicKeyCredentialRequestOptions for login passkey step-up."""
    from fastapi.responses import JSONResponse
    from ..services import webauthn_svc as wa_svc

    ip = _client_ip(request) or "unknown"
    if not rate_limit_auth(
        f"2fa-wa:{ip}", max_attempts=TWOFA_RATE_MAX, window_seconds=TWOFA_RATE_WINDOW
    ):
        return JSONResponse({"ok": False, "error": "rate"}, status_code=429)

    user = _pending_2fa_user(request, session)
    if not user:
        return JSONResponse({"ok": False, "error": "session"}, status_code=401)
    try:
        options_json, chal_token = wa_svc.authentication_options_json(session, user)
    except wa_svc.WebAuthnConfigError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": "options_failed"}, status_code=500)

    import json as _json

    body = _json.loads(options_json)
    resp = JSONResponse({"ok": True, "publicKey": body})
    resp.set_cookie(
        wa_svc.CHALLENGE_COOKIE_AUTH,
        chal_token,
        **cookie_auth_kwargs(max_age=60 * wa_svc.CHALLENGE_MINUTES),
    )
    return resp


@router.post("/2fa/webauthn/verify")
async def two_factor_webauthn_verify(
    request: Request,
    session: Session = Depends(get_session),
):
    """JSON body: credential + optional trust_device — completes login on success."""
    from fastapi.responses import JSONResponse
    from ..services import webauthn_svc as wa_svc

    ip = _client_ip(request) or "unknown"
    if not rate_limit_auth(
        f"2fa-wa:{ip}", max_attempts=TWOFA_RATE_MAX, window_seconds=TWOFA_RATE_WINDOW
    ):
        return JSONResponse({"ok": False, "error": "rate"}, status_code=429)

    user = _pending_2fa_user(request, session)
    if not user:
        return JSONResponse({"ok": False, "error": "session"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)

    credential = body.get("credential") if isinstance(body, dict) else None
    if not isinstance(credential, dict):
        return JSONResponse({"ok": False, "error": "missing_credential"}, status_code=400)
    trust_device = body.get("trust_device") if isinstance(body, dict) else None

    chal = request.cookies.get(wa_svc.CHALLENGE_COOKIE_AUTH)
    try:
        wa_svc.verify_authentication(session, user, credential, chal)
    except wa_svc.WebAuthnVerifyError as e:
        try:
            al = make_audit_log(
                user_id=user.id,
                action="user_login_failed",
                status="failed",
                details=f"Passkey 2FA failed: {e}",
                finished_at=datetime.utcnow(),
            )
            session.add(al)
            session.commit()
        except Exception:
            session.rollback()
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception:
        return JSONResponse({"ok": False, "error": "verify_failed"}, status_code=400)

    _touch_last_login(session, user)
    _audit(session, user.id, "user_login", "Login (passkey 2FA verified)")
    token = create_user_access_token(user)
    redirect = post_login_path(user, session)
    resp = JSONResponse({"ok": True, "redirect": redirect})
    _set_auth_cookie(resp, token)
    resp.delete_cookie(PENDING_COOKIE, **cookie_delete_kwargs())
    resp.delete_cookie(wa_svc.CHALLENGE_COOKIE_AUTH, **cookie_delete_kwargs())
    if trust_device in (True, 1, "1", "on", "true"):
        raw, _dev, _created = ensure_trusted_device(
            session,
            user.id,
            read_trusted_device_token(request.cookies, user.id),
            label="Browser",
            user_agent=request.headers.get("user-agent"),
            ip=ip,
        )
        _set_trusted_device_cookie(resp, user.id, raw)
    return resp


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, session: Session = Depends(get_session)):
    from ..services import password_policy as pwpol
    from ..services.demo import demo_mode, redirect_if_demo

    # Public demo: no self-signup UI — shared login only
    if demo_mode():
        blocked = redirect_if_demo("/auth/login", error="demo_no_register")
        if blocked:
            return blocked
    if not _registration_allowed(session):
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "title": "Register",
                "error": (
                    "Registration is closed. Ask an administrator to create an account "
                    "for you (Users → Create user), or to send an invite."
                ),
                "closed": True,
                "password_policy_text": pwpol.policy_rules_text(),
            },
        )
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "title": "Register",
            "password_policy_text": pwpol.policy_rules_text(),
        },
    )


@router.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session)
):
    from ..services import password_policy as pwpol
    from ..services.demo import demo_mode, redirect_if_demo

    if demo_mode():
        blocked = redirect_if_demo("/auth/login", error="demo_no_register")
        if blocked:
            return blocked

    ip = _client_ip(request) or "unknown"
    if not rate_limit_auth(
        f"register:{ip}",
        max_attempts=REGISTER_RATE_MAX,
        window_seconds=REGISTER_RATE_WINDOW,
    ):
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "title": "Register",
                "error": "Too many registration attempts. Wait a few minutes and try again.",
                "password_policy_text": pwpol.policy_rules_text(),
            },
        )

    if not _registration_allowed(session):
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "title": "Register",
                "error": (
                    "Registration is closed. Ask an administrator to create an account "
                    "for you (Users → Create user)."
                ),
                "closed": True,
                "password_policy_text": pwpol.policy_rules_text(),
            },
        )

    existing = session.exec(select(User).where(User.email == email)).first()
    if existing:
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "title": "Register",
                "error": "User with that email already exists",
                "password_policy_text": pwpol.policy_rules_text(),
            },
        )

    ok, pol_err = pwpol.validate_password(password or "")
    if not ok:
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "title": "Register",
                "error": pol_err or "Password does not meet policy",
                "password_policy_text": pwpol.policy_rules_text(),
            },
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
    """Clear session cookies and return to login.

    Does **not** clear trusted-device cookies — those are meant to skip 2FA on
    the next password login for this browser (until expiry or revoke).
    Does clear console step-up grant so web SSH cannot ride a dead session.
    """
    response = RedirectResponse("/auth/login", status_code=303)
    dk = cookie_delete_kwargs()
    response.delete_cookie("access_token", **dk)
    response.delete_cookie(PENDING_COOKIE, **dk)
    try:
        from ..services.ssh_console import CONSOLE_GRANT_COOKIE

        response.delete_cookie(CONSOLE_GRANT_COOKIE, **dk)
        # Keep console_device cookie: next login on same browser rebinds cleanly;
        # tickets still require a fresh session + 2FA and cannot be resumed.
    except Exception:
        pass
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
    from ..services.nav_shortcuts import trusted_device_public
    from ..services import webauthn_svc as wa_svc

    device_rows = [trusted_device_public(d) for d in devices]
    passkeys = wa_svc.list_credentials(session, int(user.id))
    passkey_rows = [wa_svc.credential_public_dict(c) for c in passkeys]
    has_2fa = wa_svc.user_has_2fa(session, user)
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

    role = user_role(user)
    is_admin_user = role == ROLE_ADMIN
    n_devices = len(devices or [])
    n_passkeys = len(passkeys or [])
    account_pulse = {
        "health": "ok" if has_2fa else ("warn" if not pending_setup else "busy"),
        "primary": "on" if has_2fa else ("…" if pending_setup else "off"),
        "primary_label": "2fa",
        "bar": [
            {
                "n": 1 if has_2fa else 0.001,
                "cls": "ops-bar--ok" if has_2fa else "ops-bar--mute",
                "title": "2FA",
            },
            {
                "n": n_devices or 0.001,
                "cls": "ops-bar--run",
                "title": f"{n_devices} trusted devices",
            },
            {
                "n": push_subscription_count or 0.001,
                "cls": "ops-bar--ok" if push_subscription_count else "ops-bar--mute",
                "title": f"{push_subscription_count} push devices",
            },
        ],
        "line1": [
            {
                "n": "on" if has_2fa else "off",
                "l": "2fa",
                "cls": "text-accent" if has_2fa else "text-warning",
            },
            {
                "n": n_passkeys if n_passkeys else "—",
                "l": "passkeys",
                "cls": "text-accent" if n_passkeys else "",
            },
            {
                "n": backup_remaining if user.totp_enabled else "—",
                "l": "codes",
                "cls": "text-warning" if user.totp_enabled and backup_remaining < 3 else "",
            },
            {"n": n_devices, "l": "trusted", "cls": ""},
            {
                "n": push_subscription_count,
                "l": "push",
                "cls": "text-info" if push_subscription_count else "",
            },
        ],
        "line2": [
            {"n": role or "admin", "l": "role", "cls": "text-accent"},
            {
                "n": "yes" if user.avatar_path else "no",
                "l": "avatar",
                "cls": "",
            },
            {
                "n": "on" if (push_prefs and push_prefs.push_enabled) else "off",
                "l": "push master",
                "cls": "",
            },
        ],
        "caption": "Security · devices · notifications",
    }

    from ..services import password_policy as pwpol
    from ..services import oidc_svc as oidc
    from ..services.demo import demo_mode

    is_demo = demo_mode()
    oidc_rows = [oidc.identity_public_dict(r) for r in oidc.list_identities(session, int(user.id))]
    password_login_enabled = oidc.password_login_allowed(user)

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="account.html",
        context={
            "title": "Account",
            "user": user,
            "msg": msg,
            "error": err,
            "devices": devices,
            "device_rows": device_rows,
            "backup_remaining": backup_remaining,
            "passkeys": passkey_rows,
            "passkey_count": n_passkeys,
            "webauthn_rp_id": wa_svc.resolve_rp_id(),
            "webauthn_origin": wa_svc.resolve_expected_origin(),
            "setup_qr_svg": setup_qr_svg,
            "setup_qr_uri": setup_qr_uri,
            "setup_secret": setup_secret,
            "setup_otpauth": setup_otpauth,
            "pending_2fa_setup": pending_setup,
            "show_2fa_modal": show_2fa_modal,
            "backup_codes_shown": backup_codes.split(",") if backup_codes else None,
            "trusted_device_days": settings.TRUSTED_DEVICE_DAYS,
            "user_role": role,
            "is_admin": is_admin_user,
            "push_configured": bool(push_creds),
            "push_vapid_source": push_creds.source if push_creds else None,
            "push_prefs": push_prefs,
            "push_subscription_count": push_subscription_count,
            "public_url": settings.PIHERDER_PUBLIC_URL,
            "push_sent": push_sent,
            "account_pulse": account_pulse,
            "password_policy_text": pwpol.policy_rules_text(),
            "oidc_enabled": False if is_demo else oidc.oidc_enabled(),
            "oidc_display_name": oidc.oidc_display_name(),
            "oidc_identities": [] if is_demo else oidc_rows,
            "password_login_enabled": password_login_enabled,
            "has_2fa": has_2fa,
            "has_totp": wa_svc.totp_active(user),
            "has_passkeys": bool(passkeys),
            "account_stepup_active": account_stepup_active(request, user),
            "account_stepup_minutes": ACCOUNT_STEPUP_MINUTES,
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
    from ..services.demo import redirect_if_demo

    # Shared demo: email change locks everyone out of the known login
    blocked = redirect_if_demo("/auth/account")
    if blocked:
        # Allow display_name-only updates? No — keep shared account fixed.
        return blocked
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
    from ..services.demo import redirect_if_demo

    blocked = redirect_if_demo("/auth/account")
    if blocked:
        return blocked

    if not verify_password(current_password, user.hashed_password):
        return RedirectResponse("/auth/account?error=bad_password", status_code=303)
    if new_password != confirm_password:
        return RedirectResponse("/auth/account?error=password_mismatch", status_code=303)
    ok, _err = pwpol.validate_password(new_password or "")
    if not ok:
        return RedirectResponse("/auth/account?error=password_policy", status_code=303)

    from ..services.user_admin import bump_session_version

    user.hashed_password = get_password_hash(new_password)
    user.must_change_password = False
    user.updated_at = datetime.utcnow()
    session.add(user)
    revoke_all_trusted_devices(session, user.id)
    bump_session_version(session, user)
    session.commit()
    session.refresh(user)
    _audit(
        session,
        user.id,
        "user_password_changed",
        "Password changed; sessions + trusted devices revoked",
    )
    # Re-issue cookie so *this* browser stays signed in; other sessions die
    response = RedirectResponse("/auth/account?msg=password_changed", status_code=303)
    _set_auth_cookie(response, create_user_access_token(user))
    _clear_trusted_device_cookie(response, user.id)
    return response


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
async def my_avatar(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    path = avatar_svc.absolute_avatar_path(user.avatar_path)
    if not path:
        # Stale DB path (file missing) — clear so UI falls back to letter avatar
        if user.avatar_path:
            user.avatar_path = None
            user.updated_at = datetime.utcnow()
            session.add(user)
            session.commit()
        raise HTTPException(404)
    return FileResponse(
        path,
        media_type=avatar_svc.content_type_for_path(path),
        headers={
            # Per-user URL + query bust should be enough; never share across sessions
            "Cache-Control": "private, no-cache, must-revalidate",
        },
    )


# --- 2FA management ---

def _demo_account_block():
    """Shared demo: password/2FA mutations would lock out other visitors."""
    from ..services.demo import redirect_if_demo

    return redirect_if_demo("/auth/account")


@router.post("/account/2fa/start")
async def two_factor_start(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    blocked = _demo_account_block()
    if blocked:
        return blocked
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
    response.set_cookie(
        "totp_setup_secret",
        secret,
        **cookie_auth_kwargs(max_age=600),
    )
    response.delete_cookie("totp_setup_qr", path="/")  # legacy oversized cookie
    return response


@router.post("/account/2fa/confirm")
async def two_factor_confirm(
    request: Request,
    code: str = Form(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    blocked = _demo_account_block()
    if blocked:
        return blocked
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
    blocked = _demo_account_block()
    if blocked:
        return blocked
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
    response = RedirectResponse("/auth/account?msg=2fa_disabled", status_code=303)
    _clear_trusted_device_cookie(response, user.id)
    return response


@router.post("/account/2fa/backup-codes")
async def regenerate_backup_codes(
    current_password: str = Form(...),
    code: str = Form(""),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Regenerate backup codes — requires current password + live 2FA (TOTP or unused backup)."""
    blocked = _demo_account_block()
    if blocked:
        return blocked
    if not user.totp_enabled:
        return RedirectResponse("/auth/account?error=2fa_off", status_code=303)
    if not verify_password(current_password, user.hashed_password):
        return RedirectResponse("/auth/account?error=bad_password", status_code=303)
    # Step-up 2FA: password alone must not be enough to mint new recovery codes.
    secret = None
    if user.totp_secret_encrypted:
        try:
            secret = decrypt_totp_secret(user.totp_secret_encrypted)
        except Exception:
            secret = None
    code_ok = bool(secret and code and verify_totp_code(secret, code))
    if not code_ok and not (code and consume_backup_code(session, user.id, code)):
        return RedirectResponse("/auth/account?error=2fa_bad_code", status_code=303)
    codes = generate_backup_codes()
    replace_backup_codes(session, user.id, codes)
    revoke_all_trusted_devices(session, user.id)
    _audit(session, user.id, "user_2fa_backup_regenerated", "Backup codes regenerated")
    response = RedirectResponse(
        f"/auth/account?msg=backup_codes&backup_codes={','.join(codes)}",
        status_code=303,
    )
    _clear_trusted_device_cookie(response, user.id)
    return response


# --- WebAuthn / passkeys (v1.2 Stream I) ---


@router.post("/account/webauthn/register/options")
async def webauthn_register_options(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """JSON: PublicKeyCredentialCreationOptions for adding a passkey."""
    from fastapi.responses import JSONResponse
    from ..services import webauthn_svc as wa_svc
    from ..services.demo import http_403_if_demo
    import json as _json

    http_403_if_demo("shared_account")
    try:
        options_json, chal_token = wa_svc.registration_options_json(session, user)
    except wa_svc.WebAuthnConfigError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception:
        return JSONResponse({"ok": False, "error": "options_failed"}, status_code=500)

    body = _json.loads(options_json)
    resp = JSONResponse({"ok": True, "publicKey": body})
    resp.set_cookie(
        wa_svc.CHALLENGE_COOKIE_REG,
        chal_token,
        **cookie_auth_kwargs(max_age=60 * wa_svc.CHALLENGE_MINUTES),
    )
    return resp


@router.post("/account/webauthn/register/verify")
async def webauthn_register_verify(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """JSON body: { credential, nickname? } — stores new passkey."""
    from fastapi.responses import JSONResponse
    from ..services import webauthn_svc as wa_svc
    from ..services.demo import http_403_if_demo

    http_403_if_demo("shared_account")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
    credential = body.get("credential") if isinstance(body, dict) else None
    nickname = (body.get("nickname") or "").strip() if isinstance(body, dict) else ""
    if not isinstance(credential, dict):
        return JSONResponse({"ok": False, "error": "missing_credential"}, status_code=400)

    chal = request.cookies.get(wa_svc.CHALLENGE_COOKIE_REG)
    try:
        row = wa_svc.verify_registration(
            session, user, credential, chal, nickname=nickname or None
        )
    except wa_svc.WebAuthnVerifyError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except wa_svc.WebAuthnConfigError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception:
        return JSONResponse({"ok": False, "error": "verify_failed"}, status_code=400)

    _audit(
        session,
        user.id,
        "user_passkey_registered",
        f"Passkey registered: {row.nickname or row.id}",
    )
    resp = JSONResponse(
        {
            "ok": True,
            "credential": wa_svc.credential_public_dict(row),
            "redirect": "/auth/account?msg=passkey_added#account-passkeys",
        }
    )
    resp.delete_cookie(wa_svc.CHALLENGE_COOKIE_REG, **cookie_delete_kwargs())
    return resp


@router.post("/account/webauthn/stepup/options")
async def account_webauthn_stepup_options(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """PublicKeyCredentialRequestOptions for Account sensitive-action step-up."""
    from fastapi.responses import JSONResponse
    from ..services import webauthn_svc as wa_svc
    import json as _json

    if not wa_svc.has_passkeys(session, int(user.id)):
        return JSONResponse(
            {"ok": False, "error": "No passkeys registered for this account"},
            status_code=400,
        )
    try:
        options_json, chal_token = wa_svc.authentication_options_json(session, user)
    except wa_svc.WebAuthnConfigError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception:
        return JSONResponse({"ok": False, "error": "options_failed"}, status_code=500)

    body = _json.loads(options_json)
    resp = JSONResponse({"ok": True, "publicKey": body})
    resp.set_cookie(
        wa_svc.CHALLENGE_COOKIE_AUTH,
        chal_token,
        **cookie_auth_kwargs(max_age=60 * wa_svc.CHALLENGE_MINUTES),
    )
    return resp


@router.post("/account/webauthn/stepup/verify")
async def account_webauthn_stepup_verify(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Verify passkey and grant short-lived Account step-up cookie."""
    from fastapi.responses import JSONResponse
    from ..services import webauthn_svc as wa_svc

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
    credential = body.get("credential") if isinstance(body, dict) else None
    if not isinstance(credential, dict):
        return JSONResponse({"ok": False, "error": "missing_credential"}, status_code=400)

    chal = request.cookies.get(wa_svc.CHALLENGE_COOKIE_AUTH)
    try:
        wa_svc.verify_authentication(session, user, credential, chal)
    except wa_svc.WebAuthnVerifyError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception:
        return JSONResponse({"ok": False, "error": "verify_failed"}, status_code=400)

    _audit(session, user.id, "user_account_stepup", "Passkey step-up for Account actions")
    resp = JSONResponse(
        {
            "ok": True,
            "redirect": "/auth/account?msg=stepup_ok#account-stepup-box",
            "minutes": ACCOUNT_STEPUP_MINUTES,
        }
    )
    resp.delete_cookie(wa_svc.CHALLENGE_COOKIE_AUTH, **cookie_delete_kwargs())
    resp.set_cookie(
        ACCOUNT_STEPUP_COOKIE,
        create_account_stepup_token(user.id),
        **cookie_auth_kwargs(max_age=ACCOUNT_STEPUP_MINUTES * 60),
    )
    return resp


@router.post("/account/webauthn/{cred_id}/revoke")
async def webauthn_revoke(
    cred_id: int,
    current_password: str = Form(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    blocked = _demo_account_block()
    if blocked:
        return blocked
    from ..services import webauthn_svc as wa_svc

    if not verify_password(current_password, user.hashed_password):
        return RedirectResponse(
            "/auth/account?error=bad_password#account-passkeys", status_code=303
        )
    row = wa_svc.get_credential(session, cred_id, int(user.id))
    if not row:
        return RedirectResponse(
            "/auth/account?error=passkey_not_found#account-passkeys", status_code=303
        )
    label = row.nickname or str(row.id)
    wa_svc.delete_credential(session, row)
    _audit(session, user.id, "user_passkey_revoked", f"Passkey revoked: {label}")
    return RedirectResponse(
        "/auth/account?msg=passkey_revoked#account-passkeys", status_code=303
    )


@router.post("/account/webauthn/{cred_id}/rename")
async def webauthn_rename(
    cred_id: int,
    nickname: str = Form(""),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    from ..services import webauthn_svc as wa_svc

    row = wa_svc.get_credential(session, cred_id, int(user.id))
    if not row:
        return RedirectResponse(
            "/auth/account?error=passkey_not_found#account-passkeys", status_code=303
        )
    wa_svc.rename_credential(session, row, nickname)
    _audit(session, user.id, "user_passkey_renamed", f"Passkey renamed #{cred_id}")
    return RedirectResponse(
        "/auth/account?msg=passkey_renamed#account-passkeys", status_code=303
    )


@router.post("/account/trusted-devices/{device_id}/rename")
async def rename_trusted_device(
    device_id: int,
    label: str = Form(""),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """AB — operator-friendly name for a trusted device."""
    from ..models import TrustedDevice

    dev = session.get(TrustedDevice, device_id)
    if not dev or dev.user_id != user.id:
        return RedirectResponse("/auth/account?error=device_not_found", status_code=303)
    name = (label or "").strip()[:80] or None
    dev.label = name
    session.add(dev)
    session.commit()
    _audit(session, user.id, "user_trusted_device_renamed", f"Device #{device_id}")
    return RedirectResponse("/auth/account?msg=device_renamed", status_code=303)


@router.post("/account/trusted-devices/{device_id}/revoke")
async def revoke_device(
    device_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if revoke_trusted_device(session, user.id, device_id):
        _audit(session, user.id, "user_trusted_device_revoked", f"Device #{device_id}")
    # Do not clear this browser's cookie: only the revoked row dies. If it was
    # this browser, the cookie becomes inert on next login (lookup fails).
    return RedirectResponse("/auth/account?msg=device_revoked", status_code=303)


@router.post("/account/trusted-devices/revoke-all")
async def revoke_all_devices(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    n = revoke_all_trusted_devices(session, user.id)
    _audit(session, user.id, "user_trusted_device_revoked", f"Revoked all ({n})")
    response = RedirectResponse("/auth/account?msg=devices_revoked", status_code=303)
    _clear_trusted_device_cookie(response, user.id)
    return response



from .auth_users import router as users_router
router.include_router(users_router)

@router.get("/force-password", response_class=HTMLResponse)
async def force_password_page(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    from ..services import password_policy as pwpol

    if not getattr(user, "must_change_password", False):
        return RedirectResponse(post_login_path(user, session), status_code=303)
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
        return RedirectResponse(post_login_path(user, session), status_code=303)
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
    # Re-issue cookie with current session_version (admin recovery may have bumped it)
    response = RedirectResponse(post_login_path(user, session), status_code=303)
    _set_auth_cookie(response, create_user_access_token(user))
    _clear_trusted_device_cookie(response, user.id)
    return response


@router.get("/force-2fa", response_class=HTMLResponse)
async def force_2fa_page(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    from ..security.auth import user_has_second_factor

    if not force_2fa_required() or user_has_second_factor(session, user):
        return RedirectResponse("/", status_code=303)
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="force_2fa.html",
        context={
            "title": "Two-factor authentication required",
            "user": user,
        },
    )

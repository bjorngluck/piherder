"""OIDC / SSO routes (v1.2 Stream S)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from ..database import get_session
from ..models import User
from ..security.auth import (
    cookie_auth_kwargs,
    cookie_delete_kwargs,
    create_pending_2fa_token,
    create_user_access_token,
    find_valid_trusted_device,
    get_current_user,
    post_login_path,
    read_trusted_device_token,
    rate_limit_auth,
    LOGIN_RATE_MAX,
    LOGIN_RATE_WINDOW,
)
from ..services import oidc_svc as oidc
from ..services import webauthn_svc as wa_svc
from ..services.audit_write import make_audit_log
from ..services.request_ip import client_ip_from_request

router = APIRouter()

PENDING_COOKIE = "pending_2fa"


def _audit(
    session: Session,
    user_id: Optional[int],
    action: str,
    details: str,
    status: str = "success",
) -> None:
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


def _set_auth_cookie(response: RedirectResponse, token: str) -> None:
    response.set_cookie(
        "access_token",
        token,
        **cookie_auth_kwargs(max_age=60 * 60 * 24 * 7),
    )


def _client_ip(request: Request) -> str:
    return client_ip_from_request(request) or "unknown"


def _set_oidc_state_cookie(response: RedirectResponse, value: str) -> None:
    response.set_cookie(
        oidc.STATE_COOKIE,
        value,
        **cookie_auth_kwargs(max_age=60 * oidc.STATE_MINUTES),
    )


def _clear_oidc_state(response: RedirectResponse) -> None:
    response.delete_cookie(oidc.STATE_COOKIE, **cookie_delete_kwargs())


def _finish_login(
    request: Request,
    session: Session,
    user: User,
    *,
    audit_detail: str,
) -> RedirectResponse:
    """Same 2FA / trusted-device / force-2FA path as password login."""
    user.last_login_at = datetime.utcnow()
    session.add(user)
    session.commit()

    if (
        wa_svc.user_requires_2fa_stepup(session, user)
        and not getattr(user, "must_change_password", False)
    ):
        raw_trusted = read_trusted_device_token(request.cookies, user.id)
        if raw_trusted and find_valid_trusted_device(session, user.id, raw_trusted):
            _audit(session, user.id, "sso_login", f"{audit_detail} (trusted device, 2FA skipped)")
            _audit(session, user.id, "user_login", "Login (SSO, trusted device)")
            token = create_user_access_token(user)
            response = RedirectResponse(
                url=post_login_path(user, session), status_code=303
            )
            _set_auth_cookie(response, token)
            return response

        pending = create_pending_2fa_token(user.id)
        _audit(session, user.id, "sso_login", f"{audit_detail} (2FA pending)")
        response = RedirectResponse(url="/auth/2fa", status_code=303)
        response.set_cookie(
            PENDING_COOKIE,
            pending,
            **cookie_auth_kwargs(max_age=60 * 10),
        )
        return response

    _audit(session, user.id, "sso_login", audit_detail)
    _audit(session, user.id, "user_login", "Login (SSO)")
    token = create_user_access_token(user)
    response = RedirectResponse(url=post_login_path(user, session), status_code=303)
    _set_auth_cookie(response, token)
    return response


@router.get("/oidc/login")
async def oidc_login_start(request: Request, session: Session = Depends(get_session)):
    from ..services.demo import redirect_if_demo

    del session
    blocked = redirect_if_demo("/auth/login")
    if blocked:
        return blocked
    ip = _client_ip(request)
    if not rate_limit_auth(
        f"oidc-login:{ip}", max_attempts=LOGIN_RATE_MAX, window_seconds=LOGIN_RATE_WINDOW
    ):
        return RedirectResponse("/auth/login?error=rate", status_code=303)
    try:
        url, state_cookie = oidc.build_authorize_url(mode="login")
    except oidc.OidcConfigError as e:
        return RedirectResponse(
            f"/auth/login?error=sso_config&detail={str(e)[:80]}", status_code=303
        )
    response = RedirectResponse(url, status_code=303)
    _set_oidc_state_cookie(response, state_cookie)
    return response


@router.get("/oidc/link")
async def oidc_link_start(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Account → Link SSO (local session → IdP)."""
    from ..services.demo import redirect_if_demo

    blocked = redirect_if_demo("/auth/account")
    if blocked:
        return blocked
    if not oidc.oidc_enabled():
        return RedirectResponse("/auth/account?error=sso_disabled", status_code=303)
    # GET is start-only when the user has no 2FA. Enrolled users must POST
    # (password / TOTP / passkey) — ``?ok=1`` is not a step-up.
    if wa_svc.user_has_2fa(session, user):
        return RedirectResponse("/auth/account?error=sso_2fa_link", status_code=303)
    try:
        url, state_cookie = oidc.build_authorize_url(mode="link", user_id=int(user.id))
    except oidc.OidcConfigError as e:
        return RedirectResponse(
            f"/auth/account?error=sso_config&detail={str(e)[:80]}", status_code=303
        )
    response = RedirectResponse(url, status_code=303)
    _set_oidc_state_cookie(response, state_cookie)
    return response


@router.post("/oidc/link")
async def oidc_link_start_post(
    request: Request,
    current_password: str = Form(""),
    totp_code: str = Form(""),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    from ..services.demo import redirect_if_demo

    blocked = redirect_if_demo("/auth/account")
    if blocked:
        return blocked
    if not oidc.oidc_enabled():
        return RedirectResponse("/auth/account?error=sso_disabled", status_code=303)
    ok, err = oidc.verify_stepup_2fa(
        session,
        user,
        password=current_password or None,
        totp_code=totp_code or None,
        request=request,
    )
    if not ok:
        return RedirectResponse(f"/auth/account?error={err or 'sso_2fa_link'}", status_code=303)
    try:
        url, state_cookie = oidc.build_authorize_url(mode="link", user_id=int(user.id))
    except oidc.OidcConfigError as e:
        return RedirectResponse(
            f"/auth/account?error=sso_config&detail={str(e)[:80]}", status_code=303
        )
    response = RedirectResponse(url, status_code=303)
    _set_oidc_state_cookie(response, state_cookie)
    return response


@router.get("/oidc/callback")
async def oidc_callback(request: Request, session: Session = Depends(get_session)):
    from ..services.demo import redirect_if_demo

    blocked = redirect_if_demo("/auth/login")
    if blocked:
        return blocked
    ip = _client_ip(request)
    err = request.query_params.get("error")
    if err:
        _audit(
            session,
            None,
            "sso_login_failed",
            f"IdP error={err} ip={ip}",
            status="failed",
        )
        return RedirectResponse("/auth/login?error=sso_denied", status_code=303)

    code = (request.query_params.get("code") or "").strip()
    state_param = (request.query_params.get("state") or "").strip()
    state_raw = request.cookies.get(oidc.STATE_COOKIE)
    state = oidc.parse_state_token(state_raw)

    if not code or not state or state.get("sp") != state_param:
        _audit(session, None, "sso_login_failed", f"bad state ip={ip}", status="failed")
        return RedirectResponse("/auth/login?error=sso_state", status_code=303)

    mode = state.get("mode") or "login"
    code_verifier = state.get("cv") or ""
    nonce = state.get("nonce") or ""

    try:
        tokens = oidc.exchange_code(code, code_verifier)
        claims = oidc.claims_from_tokens(tokens, expected_nonce=nonce)
        cfg = oidc.oidc_settings()
        issuer = oidc.normalize_issuer(str(cfg.get("oidc_issuer") or ""))

        if mode == "link":
            uid = state.get("uid")
            if uid is None:
                return RedirectResponse("/auth/account?error=sso_state", status_code=303)
            user = session.get(User, int(uid))
            if not user or not user.is_active:
                return RedirectResponse("/auth/login?error=sso_inactive", status_code=303)
            oidc.create_link(
                session,
                user,
                issuer=issuer,
                subject=str(claims.get("sub")),
                claims=claims,
            )
            session.commit()
            _audit(
                session,
                user.id,
                "sso_link",
                f"reason=account_explicit iss={issuer} sub={str(claims.get('sub'))[:64]}",
            )
            response = RedirectResponse("/auth/account?msg=sso_linked", status_code=303)
            _clear_oidc_state(response)
            return response

        # Login mode
        user, reason, _existing = oidc.find_user_for_login(session, claims, cfg)
        if reason == "email_match":
            oidc.create_link(
                session,
                user,
                issuer=issuer,
                subject=str(claims.get("sub")),
                claims=claims,
            )
            session.commit()
            _audit(
                session,
                user.id,
                "sso_link",
                f"reason=email_match iss={issuer} sub={str(claims.get('sub'))[:64]}",
            )
        elif reason == "jit":
            oidc.create_link(
                session,
                user,
                issuer=issuer,
                subject=str(claims.get("sub")),
                claims=claims,
            )
            session.commit()
            _audit(
                session,
                user.id,
                "sso_user_provisioned",
                f"email={user.email} role={user.role}",
            )
            _audit(
                session,
                user.id,
                "sso_link",
                f"reason=jit iss={issuer} sub={str(claims.get('sub'))[:64]}",
            )
        else:
            # existing link — refresh snapshot
            ident = oidc.get_identity_by_iss_sub(session, issuer, str(claims.get("sub")))
            if ident:
                ident.claims_json = oidc._safe_claims_json(claims)
                ident.last_login_at = datetime.utcnow()
                session.add(ident)
            session.commit()

        role_changed = oidc.maybe_sync_role(session, user, claims, cfg)
        if role_changed:
            session.commit()
            _audit(
                session,
                user.id,
                "user_role_changed",
                f"source=oidc role={user.role}",
            )

        response = _finish_login(
            request,
            session,
            user,
            audit_detail=f"SSO login reason={reason} iss={issuer}",
        )
        _clear_oidc_state(response)
        return response

    except oidc.OidcConfigError:
        _audit(session, None, "sso_login_failed", f"config ip={ip}", status="failed")
        dest = "/auth/account?error=sso_config" if mode == "link" else "/auth/login?error=sso_config"
        response = RedirectResponse(dest, status_code=303)
        _clear_oidc_state(response)
        return response
    except oidc.OidcFlowError as e:
        msg = str(e)[:200]
        _audit(session, None, "sso_login_failed", f"{msg} ip={ip}", status="failed")
        # Map common messages to stable query codes
        low = msg.lower()
        if "already linked to another" in low:
            code = "sso_link_conflict"
        elif "already exists" in low:
            code = "sso_email_conflict"
        elif "disabled" in low:
            code = "sso_inactive"
        else:
            code = "sso_denied"
        dest = f"/auth/account?error={code}" if mode == "link" else f"/auth/login?error={code}"
        response = RedirectResponse(dest, status_code=303)
        _clear_oidc_state(response)
        return response
    except Exception as e:
        from logging import getLogger

        getLogger(__name__).exception("OIDC callback unexpected: %s", e)
        _audit(session, None, "sso_login_failed", f"internal ip={ip}", status="failed")
        response = RedirectResponse("/auth/login?error=sso_denied", status_code=303)
        _clear_oidc_state(response)
        return response


@router.post("/oidc/unlink")
async def oidc_unlink(
    request: Request,
    identity_id: int = Form(...),
    current_password: str = Form(""),
    totp_code: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    from ..models import OidcIdentity
    from ..services import password_policy as pwpol
    from ..services.demo import redirect_if_demo

    blocked = redirect_if_demo("/auth/account")
    if blocked:
        return blocked
    row = session.get(OidcIdentity, int(identity_id))
    if not row or int(row.user_id) != int(user.id):
        return RedirectResponse("/auth/account?error=sso_not_found", status_code=303)

    ok, err = oidc.verify_stepup_2fa(
        session,
        user,
        password=current_password or None,
        totp_code=totp_code or None,
        request=request,
    )
    if not ok:
        return RedirectResponse(f"/auth/account?error={err or '2fa_required'}", status_code=303)

    # Must retain a password login path after unlink
    if not oidc.password_login_allowed(user):
        if not new_password or new_password != confirm_password:
            return RedirectResponse("/auth/account?error=password_mismatch", status_code=303)
        ok_pw, _ = pwpol.validate_password(new_password)
        if not ok_pw:
            return RedirectResponse("/auth/account?error=password_policy", status_code=303)
        oidc.enable_password(user, new_password)
        session.add(user)
        _audit(session, user.id, "user_password_set", "Password set before SSO unlink")

    session.delete(row)
    session.commit()
    _audit(session, user.id, "sso_unlink", f"identity_id={identity_id} iss={row.issuer}")
    return RedirectResponse("/auth/account?msg=sso_unlinked", status_code=303)


@router.post("/account/password/remove")
async def password_remove(
    request: Request,
    current_password: str = Form(""),
    totp_code: str = Form(""),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    from ..services.demo import redirect_if_demo

    blocked = redirect_if_demo("/auth/account")
    if blocked:
        return blocked
    if not oidc.has_oidc_link(session, int(user.id)):
        return RedirectResponse("/auth/account?error=sso_required", status_code=303)
    if not oidc.password_login_allowed(user):
        return RedirectResponse("/auth/account?msg=password_already_removed", status_code=303)

    ok, err = oidc.verify_stepup_2fa(
        session,
        user,
        password=current_password or None,
        totp_code=totp_code or None,
        request=request,
    )
    if not ok:
        return RedirectResponse(f"/auth/account?error={err or '2fa_required'}", status_code=303)

    # If no 2FA, verify_stepup already checked password when enabled
    oidc.set_unusable_password(user)
    user.updated_at = datetime.utcnow()
    session.add(user)
    session.commit()
    _audit(session, user.id, "user_password_removed", "Password removed; SSO-only login")
    return RedirectResponse("/auth/account?msg=password_removed", status_code=303)


@router.post("/account/password/set")
async def password_set(
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    totp_code: str = Form(""),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Set password when password_login is disabled (SSO-only)."""
    from ..services import password_policy as pwpol
    from ..services.user_admin import bump_session_version
    from ..security.auth import revoke_all_trusted_devices
    from ..services.demo import redirect_if_demo

    blocked = redirect_if_demo("/auth/account")
    if blocked:
        return blocked
    if oidc.password_login_allowed(user):
        return RedirectResponse("/auth/account?error=use_change_password", status_code=303)
    if new_password != confirm_password:
        return RedirectResponse("/auth/account?error=password_mismatch", status_code=303)
    ok_pw, _ = pwpol.validate_password(new_password)
    if not ok_pw:
        return RedirectResponse("/auth/account?error=password_policy", status_code=303)

    if wa_svc.user_has_2fa(session, user):
        ok, err = oidc.verify_stepup_2fa(
            session, user, totp_code=totp_code or None, request=request
        )
        if not ok:
            return RedirectResponse(f"/auth/account?error={err or '2fa_required'}", status_code=303)

    oidc.enable_password(user, new_password)
    user.updated_at = datetime.utcnow()
    session.add(user)
    revoke_all_trusted_devices(session, user.id)
    bump_session_version(session, user)
    session.commit()
    _audit(session, user.id, "user_password_set", "Password set (was SSO-only)")
    return RedirectResponse("/auth/account?msg=password_set", status_code=303)

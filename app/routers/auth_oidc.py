"""OIDC / SSO routes (v1.2 Stream S)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from ..database import get_session
from ..models import User
from ..security.auth import (
    cookie_auth_kwargs,
    cookie_delete_kwargs,
    create_access_token,
    create_pending_2fa_token,
    create_user_access_token,
    decode_token_payload,
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
# One-shot: POST /oidc/link verified step-up; GET then 303s to the IdP.
# Browser CSP form-action 'self' blocks a *form* 303 straight to Authentik.
LINK_OK_COOKIE = "oidc_link_ok"


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


def _set_link_ok_cookie(response: RedirectResponse, user_id: int) -> None:
    token = create_access_token(
        {"sub": str(user_id), "oidc_link": True},
        expires_delta=timedelta(minutes=2),
    )
    response.set_cookie(
        LINK_OK_COOKIE,
        token,
        **cookie_auth_kwargs(max_age=120),
    )


def _link_ok_valid(request: Request, user: User) -> bool:
    raw = request.cookies.get(LINK_OK_COOKIE)
    if not raw:
        return False
    payload = decode_token_payload(raw)
    if not payload or not payload.get("oidc_link"):
        return False
    try:
        return int(payload.get("sub")) == int(user.id)
    except (TypeError, ValueError):
        return False


def _account_redir(
    *,
    error: Optional[str] = None,
    msg: Optional[str] = None,
    fragment: str = "account-sso",
) -> RedirectResponse:
    parts = []
    if error:
        parts.append(f"error={error}")
    if msg:
        parts.append(f"msg={msg}")
    qs = ("?" + "&".join(parts)) if parts else ""
    frag = f"#{fragment}" if fragment else ""
    return RedirectResponse(f"/auth/account{qs}{frag}", status_code=303)


def _finish_login(
    request: Request,
    session: Session,
    user: User,
    *,
    audit_detail: str,
    claims: dict | None = None,
) -> RedirectResponse:
    """Same 2FA / trusted-device / force-2FA path as password login."""
    from ..services.account_stepup import idp_mfa_satisfies_login, login_trusted_skip_2fa

    user.last_login_at = datetime.utcnow()
    session.add(user)
    session.commit()

    if (
        wa_svc.user_requires_2fa_stepup(session, user)
        and not getattr(user, "must_change_password", False)
    ):
        if idp_mfa_satisfies_login(claims):
            _audit(session, user.id, "sso_login", f"{audit_detail} (IdP MFA satisfied login 2FA)")
            _audit(session, user.id, "user_login", "Login (SSO, IdP MFA)")
            token = create_user_access_token(user)
            response = RedirectResponse(
                url=post_login_path(user, session, request), status_code=303
            )
            _set_auth_cookie(response, token)
            return response
        raw_trusted = read_trusted_device_token(request.cookies, user.id)
        if (
            login_trusted_skip_2fa()
            and raw_trusted
            and find_valid_trusted_device(session, user.id, raw_trusted)
        ):
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
        return _account_redir(error="sso_disabled")
    # Enrolled 2FA: GET is allowed only after POST step-up (one-shot cookie).
    # Without 2FA, GET may start the IdP (session already proved identity).
    if wa_svc.user_has_2fa(session, user) and not _link_ok_valid(request, user):
        return _account_redir(error="sso_2fa_link")
    try:
        url, state_cookie = oidc.build_authorize_url(mode="link", user_id=int(user.id))
    except oidc.OidcConfigError as e:
        return RedirectResponse(
            f"/auth/account?error=sso_config&detail={str(e)[:80]}#account-sso",
            status_code=303,
        )
    response = RedirectResponse(url, status_code=303)
    _set_oidc_state_cookie(response, state_cookie)
    response.delete_cookie(LINK_OK_COOKIE, **cookie_delete_kwargs())
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
        return _account_redir(error="sso_disabled")
    ok, err = oidc.verify_stepup_2fa(
        session,
        user,
        password=current_password or None,
        totp_code=totp_code or None,
        request=request,
    )
    if not ok:
        return _account_redir(error=err or "sso_2fa_link")
    # Same-origin 303 first. A form POST that 303s straight to Authentik is
    # blocked by Content-Security-Policy form-action 'self' (login uses GET).
    response = RedirectResponse("/auth/oidc/link", status_code=303)
    _set_link_ok_cookie(response, int(user.id))
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

        sync_status, mapped_role = oidc.maybe_sync_role(session, user, claims, cfg)
        if sync_status == "changed":
            session.commit()
            _audit(
                session,
                user.id,
                "user_role_changed",
                f"source=oidc role={user.role}",
            )
        elif sync_status == "skipped_sole_admin":
            _audit(
                session,
                user.id,
                "user_role_sync_skipped",
                f"source=oidc reason=sole_admin kept=admin mapped={mapped_role}",
            )

        response = _finish_login(
            request,
            session,
            user,
            audit_detail=f"SSO login reason={reason} iss={issuer}",
            claims=claims,
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
        code = oidc.map_oidc_flow_error(msg)
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
        return _account_redir(error="sso_not_found")

    unlink_frag = f"account-sso-unlink-{int(identity_id)}"
    ok, err = oidc.verify_stepup_2fa(
        session,
        user,
        password=current_password or None,
        totp_code=totp_code or None,
        request=request,
    )
    if not ok:
        return _account_redir(error=err or "2fa_required", fragment=unlink_frag)

    # Must retain a password login path after unlink
    if not oidc.password_login_allowed(user):
        if not new_password or new_password != confirm_password:
            return _account_redir(error="password_mismatch", fragment=unlink_frag)
        ok_pw, _ = pwpol.validate_password(new_password)
        if not ok_pw:
            return _account_redir(error="password_policy", fragment=unlink_frag)
        oidc.enable_password(user, new_password)
        session.add(user)
        _audit(session, user.id, "user_password_set", "Password set before SSO unlink")

    session.delete(row)
    session.commit()
    _audit(session, user.id, "sso_unlink", f"identity_id={identity_id} iss={row.issuer}")
    return _account_redir(msg="sso_unlinked")


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
        return _account_redir(error="sso_required", fragment="account-password")
    if not oidc.password_login_allowed(user):
        return _account_redir(msg="password_already_removed", fragment="account-password")

    ok, err = oidc.verify_stepup_2fa(
        session,
        user,
        password=current_password or None,
        totp_code=totp_code or None,
        request=request,
    )
    if not ok:
        return _account_redir(error=err or "2fa_required", fragment="account-password")

    # If no 2FA, verify_stepup already checked password when enabled
    oidc.set_unusable_password(user)
    user.updated_at = datetime.utcnow()
    session.add(user)
    session.commit()
    _audit(session, user.id, "user_password_removed", "Password removed; SSO-only login")
    return _account_redir(msg="password_removed", fragment="account-password")


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
        return _account_redir(error="use_change_password", fragment="account-password")
    if new_password != confirm_password:
        return _account_redir(error="password_mismatch", fragment="account-password")
    ok_pw, _ = pwpol.validate_password(new_password)
    if not ok_pw:
        return _account_redir(error="password_policy", fragment="account-password")

    if wa_svc.user_has_2fa(session, user):
        ok, err = oidc.verify_stepup_2fa(
            session, user, totp_code=totp_code or None, request=request
        )
        if not ok:
            return _account_redir(error=err or "2fa_required", fragment="account-password")

    oidc.enable_password(user, new_password)
    user.updated_at = datetime.utcnow()
    session.add(user)
    revoke_all_trusted_devices(session, user.id)
    bump_session_version(session, user)
    session.commit()
    _audit(session, user.id, "user_password_set", "Password set (was SSO-only)")
    return _account_redir(msg="password_set", fragment="account-password")

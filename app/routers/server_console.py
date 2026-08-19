"""Web SSH console — mint ticket + WebSocket PTY (v1.2 Stream W).

Private keys stay server-side. Default kill switch: PIHERDER_SSH_CONSOLE=false.

**In-app only (anti-exploit):**
  · Session cookie + operator+ (no Bearer API path)
  · Ticket mint requires same-site browser Origin/Referer (blocks cross-site POST)
  · WebSocket requires matching Origin and session cookie
  · Ticket sent in first WS message (not query string — no log/Referer leak)
  · 2FA step-up (TOTP, backup, or passkey) before grant/ticket
  · CSP frame-ancestors 'self' (same-origin modal iframe only; no third-party embed)
  · Single-use tickets bound to session_version; concurrent + idle limits
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session, select

from .. import templates as templates_mod
from ..database import get_session, engine
from ..models import Server, User
from ..security.auth import (
    get_console_user,
    role_at_least,
    ROLE_OPERATOR,
    decode_token_payload,
    user_session_version,
    cookie_auth_kwargs,
    cookie_delete_kwargs,
    rate_limit_auth,
    TWOFA_RATE_MAX,
    TWOFA_RATE_WINDOW,
)
from ..services import ssh_console as cons
from ..services import webauthn_svc as wa_svc
from ..services.audit_write import make_audit_log
from ..services.request_ip import client_ip_from_request

router = APIRouter()
# Top-level multi-host workspace (mounted without /servers prefix in main.py)
workspace_router = APIRouter()
logger = logging.getLogger("piherder.console")


def _audit(
    session: Session,
    *,
    user_id: Optional[int],
    server_id: Optional[int],
    action: str,
    details: str,
    status: str = "success",
) -> None:
    al = make_audit_log(
        user_id=user_id,
        server_id=server_id,
        action=action,
        status=status,
        details=details,
        started_at=datetime.utcnow(),
        finished_at=datetime.utcnow(),
    )
    session.add(al)
    session.commit()


def _verify_console_2fa(
    session: Session,
    user: User,
    *,
    totp_code: str,
) -> tuple[bool, str]:
    """
    Console step-up when grant missing.

    Preferred: WebAuthn (separate routes). Here: TOTP app code only by default.
    Backup codes are **rejected** unless PIHERDER_SSH_CONSOLE_ALLOW_BACKUP_CODES=true
    (they are offline recovery secrets, not a strong shell step-up).
    """
    if not wa_svc.user_has_2fa(session, user):
        return False, "enroll_2fa"

    has_pk = False
    try:
        has_pk = wa_svc.has_passkeys(session, int(user.id))
    except Exception:
        has_pk = False
    if cons.require_passkey_if_enrolled() and has_pk:
        return False, "passkey_required"

    from ..security.auth import (
        decrypt_totp_secret,
        verify_totp_code,
        consume_backup_code,
    )

    code = (totp_code or "").strip().replace(" ", "")
    if not code:
        return False, "2fa_required"

    # TOTP first (authenticator app)
    if getattr(user, "totp_enabled", False) and user.totp_secret_encrypted:
        try:
            secret = decrypt_totp_secret(user.totp_secret_encrypted)
            if verify_totp_code(secret, code):
                return True, ""
        except Exception:
            pass

    # Backup codes: optional and discouraged for console
    if cons.allow_backup_codes():
        if consume_backup_code(session, int(user.id), code):
            return True, ""
    else:
        # If it looks like a backup code attempt, give a clear error
        # (don't consume even if valid)
        if len(code) >= 8 and not code.isdigit():
            return False, "backup_not_allowed"
        # Pure digits that failed TOTP: still "bad code", not backup
    return False, "2fa_bad_code"

@workspace_router.get("/console", response_class=HTMLResponse)
async def console_workspace(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_console_user),
    host: Optional[int] = None,
    hosts: Optional[str] = None,
):
    """Multi-host Web SSH workspace — host tabs, each host keeps its shells alive.

    Open from:
      · server detail / kebab: ``/console?host=<id>``
      · multi-select on Servers: ``/console?hosts=1,2,3``
    Use **+ Host** to add more. Jump between hosts with the top host tabs
    (sessions stay open until you close the tab).
    """
    enabled = cons.console_enabled()
    demo_sim = cons.is_demo_console()
    # Host list for the picker (name + whether SSH creds exist)
    try:
        rows = list(session.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
    except Exception:
        rows = list(session.exec(select(Server).order_by(Server.name)).all())
    hosts_meta = []
    by_id = {}
    for s in rows:
        has_key = bool(
            getattr(s, "ssh_private_key_encrypted", None)
            or getattr(s, "ssh_password_encrypted", None)
        )
        # Demo D5: simulated shell — treat every seeded host as openable
        if demo_sim:
            has_key = True
        meta = {
            "id": int(s.id),
            "name": s.name,
            "host": s.hostname or s.ip_address or "",
            "has_ssh": has_key,
        }
        hosts_meta.append(meta)
        by_id[int(s.id)] = meta

    def _meta_for(sid: int) -> Optional[dict]:
        if sid in by_id:
            return by_id[sid]
        s = session.get(Server, sid)
        if not s:
            return None
        has_key = bool(
            getattr(s, "ssh_private_key_encrypted", None)
            or getattr(s, "ssh_password_encrypted", None)
        )
        if demo_sim:
            has_key = True
        return {
            "id": int(s.id),
            "name": s.name,
            "host": s.hostname or s.ip_address or "",
            "has_ssh": has_key,
        }

    # Bootstrap tabs: hosts=1,2,3 and/or single host=
    initial_ids: list[int] = []
    seen: set[int] = set()
    if hosts:
        for part in str(hosts).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                sid = int(part)
            except (TypeError, ValueError):
                continue
            if sid <= 0 or sid in seen:
                continue
            if _meta_for(sid) is None:
                continue
            seen.add(sid)
            initial_ids.append(sid)
    if host is not None:
        try:
            sid = int(host)
        except (TypeError, ValueError):
            sid = 0
        if sid > 0 and sid not in seen and _meta_for(sid) is not None:
            # Single host= opens first so detail/kebab stays primary when mixed
            initial_ids.insert(0, sid)
            seen.add(sid)

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="console_workspace.html",
        context={
            "title": "Web SSH console",
            "user": user,
            "console_enabled": enabled,
            "demo_console": demo_sim,
            "hosts_json": json.dumps(hosts_meta),
            "initial_host_id": initial_ids[0] if initial_ids else None,
            "initial_host_ids": initial_ids,
            "max_shells": cons.max_per_user(),
            "popup_mode": True,
            "console_app": True,
        },
    )


@router.get("/{server_id}/console", response_class=HTMLResponse)
async def console_page(
    request: Request,
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_console_user),
):
    """Per-host console (xterm). Prefer /console?host= for multi-host UX.

    ``?embed=1`` — compact fill layout for the multi-host workspace iframe.
    Non-embed visits redirect to the multi-host workspace so users get host tabs.
    """
    q_embed = (request.query_params.get("embed") or "").strip().lower()
    embed_mode = q_embed in ("1", "true", "yes", "embed")
    q_popup = (request.query_params.get("popup") or "").strip().lower()
    popup_q = q_popup in ("1", "true", "yes", "popup")
    # Default: send operators to multi-host workspace (clearer UX)
    if not embed_mode and not popup_q and (request.query_params.get("solo") or "") not in (
        "1",
        "true",
    ):
        return RedirectResponse(f"/console?host={int(server_id)}", status_code=303)

    server = session.get(Server, server_id)
    if not server:
        return RedirectResponse("/servers", status_code=303)

    enabled = cons.console_enabled()
    demo_sim = cons.is_demo_console()
    has_2fa = wa_svc.user_has_2fa(session, user)
    has_key = bool(
        getattr(server, "ssh_private_key_encrypted", None)
        or getattr(server, "ssh_password_encrypted", None)
    )
    if demo_sim:
        # Simulated shell — no credentials or 2FA enrollment required
        has_key = True
        has_2fa = True
    sv = user_session_version(user)
    ip = client_ip_from_request(request) or ""
    device_id = cons.ensure_device_id(request.cookies.get(cons.CONSOLE_DEVICE_COOKIE))
    grant_ok = cons.grant_valid(
        request.cookies.get(cons.CONSOLE_GRANT_COOKIE),
        user_id=int(user.id),
        server_id=int(server_id),
        session_version=sv,
        client_ip=ip,
        device_id=device_id,
    )
    if demo_sim:
        # Skip step-up UI — fleet grant always considered active
        grant_ok = True
    remaining = cons.slots_remaining(int(user.id)) if enabled else 0
    has_passkeys = False
    try:
        has_passkeys = wa_svc.has_passkeys(session, int(user.id))
    except Exception:
        has_passkeys = False
    if demo_sim:
        has_passkeys = False

    popup_mode = embed_mode or popup_q

    from ..services import ssh_identities as ident_svc

    ident_list = ident_svc.console_identities(session, server, demo=demo_sim)
    can_priv = (not demo_sim) and cons.can_open_privileged(user) and any(
        i.get("role") == ident_svc.ROLE_PRIVILEGED for i in ident_list
    )
    if not can_priv:
        ident_list = [i for i in ident_list if i.get("role") != ident_svc.ROLE_PRIVILEGED]

    response = templates_mod.templates.TemplateResponse(
        request=request,
        name="server_console.html",
        context={
            "title": f"Console · {server.name}",
            "user": user,
            "server": server,
            "console_enabled": enabled,
            "demo_console": demo_sim,
            "has_2fa": has_2fa,
            "has_passkeys": has_passkeys,
            "has_ssh_cred": has_key,
            "grant_active": grant_ok,
            "grant_minutes": cons.grant_minutes(),
            "require_2fa_every_shell": (
                False if demo_sim else cons.require_2fa_every_shell()
            ),
            "prefer_passkey": cons.prefer_passkey(),
            "require_passkey": False if demo_sim else cons.require_passkey_if_enrolled(),
            "allow_backup_codes": cons.allow_backup_codes(),
            "bind_ip": cons.bind_ip_enabled(),
            "bind_device": cons.bind_device_enabled(),
            "revalidate_sec": cons.revalidate_sec(),
            "max_shells": cons.max_per_user(),
            "slots_remaining": remaining,
            "ticket_ttl": cons.ticket_ttl_sec(),
            "idle_sec": cons.idle_sec(),
            "max_session_sec": cons.max_session_sec(),
            "scrollback_default": cons.default_scrollback(),
            "popup_mode": popup_mode,
            "console_app": True,
            "embed_mode": embed_mode,
            "ssh_identities": ident_list,
            "can_privileged": can_priv,
        },
    )
    # Pin a console device id (HttpOnly) so tickets cannot be used from another browser
    if cons.bind_device_enabled():
        response.set_cookie(
            cons.CONSOLE_DEVICE_COOKIE,
            device_id,
            **cookie_auth_kwargs(max_age=60 * 60 * 24 * 400),  # ~13 months
        )
    # Soft-key / xterm JS must never be served stale from browser or proxy cache
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def _reject_cross_site(request: Request) -> Optional[JSONResponse]:
    if not cons.same_site_browser_request(request):
        return JSONResponse(
            {
                "ok": False,
                "error": "cross_site",
                "detail": "Console is only available from the PiHerder UI (same origin).",
            },
            status_code=403,
        )
    return None


def _parse_identity_id(raw: Optional[str]) -> Optional[int]:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        n = int(s)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


@router.post("/{server_id}/console/ticket")
async def mint_console_ticket(
    request: Request,
    server_id: int,
    totp_code: str = Form(""),
    identity_id: str = Form(""),
    confirm_privileged: str = Form(""),
    reason: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_console_user),
):
    """Mint single-use ticket; 2FA required unless a valid **fleet** grant cookie exists.

    Privileged (break-glass) identities ignore the fleet grant: extra confirm +
    fresh 2FA every time. Ticket is returned in JSON only.
    Demo (D5): no 2FA, no SSH cred check, privileged mint rejected.
    """
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked

    ip = client_ip_from_request(request) or "unknown"
    if not rate_limit_auth(
        f"console-ticket:{ip}:{user.id}",
        max_attempts=TWOFA_RATE_MAX,
        window_seconds=TWOFA_RATE_WINDOW,
    ):
        return JSONResponse({"ok": False, "error": "rate"}, status_code=429)

    server = session.get(Server, server_id)
    if not server:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    try:
        cons.require_enabled()
    except cons.ConsoleDisabled as e:
        return JSONResponse({"ok": False, "error": "disabled", "detail": str(e)}, status_code=403)

    from ..services import ssh_identities as ident_svc

    demo_sim = cons.is_demo_console()
    ident = None
    iid = _parse_identity_id(identity_id)
    if iid:
        ident = ident_svc.get_by_id(session, int(server_id), iid)
        if ident is None:
            return JSONResponse(
                {"ok": False, "error": "identity_missing", "detail": "SSH identity not found"},
                status_code=400,
            )
    else:
        ident = ident_svc.ensure_fleet_identity(session, server)
        session.commit()

    privileged = ident is not None and ident.role == ident_svc.ROLE_PRIVILEGED
    if privileged:
        if demo_sim:
            _audit(
                session,
                user_id=user.id,
                server_id=server_id,
                action="ssh_console_denied",
                details=f"demo_privileged ip={ip}",
                status="failed",
            )
            return JSONResponse(
                {
                    "ok": False,
                    "error": "demo_privileged",
                    "detail": "Privileged console is disabled in demo.",
                },
                status_code=403,
            )
        if not cons.can_open_privileged(user):
            _audit(
                session,
                user_id=user.id,
                server_id=server_id,
                action="ssh_console_denied",
                details=f"privileged_rbac ip={ip} need={cons.privileged_role()}",
                status="failed",
            )
            return JSONResponse(
                {
                    "ok": False,
                    "error": "privileged_forbidden",
                    "detail": "Privileged console is limited to admins (see Settings → Console).",
                },
                status_code=403,
            )
        if (confirm_privileged or "").strip() not in ("1", "on", "true", "yes"):
            return JSONResponse(
                {
                    "ok": False,
                    "error": "privileged_confirm",
                    "detail": "Confirm break-glass before opening a privileged shell.",
                },
                status_code=400,
            )
        if not ident.enabled or not ident.private_key_encrypted:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "no_ssh",
                    "detail": "Privileged identity has no stored key.",
                },
                status_code=400,
            )

    if not demo_sim and not privileged and not (
        getattr(server, "ssh_private_key_encrypted", None)
        or getattr(server, "ssh_password_encrypted", None)
        or (ident and ident.private_key_encrypted)
    ):
        return JSONResponse(
            {
                "ok": False,
                "error": "no_ssh",
                "detail": "No SSH key or password stored for this host",
            },
            status_code=400,
        )

    sv = user_session_version(user)
    device_id = cons.ensure_device_id(request.cookies.get(cons.CONSOLE_DEVICE_COOKIE))
    grant_cookie = request.cookies.get(cons.CONSOLE_GRANT_COOKIE)
    has_grant = cons.grant_valid(
        grant_cookie,
        user_id=int(user.id),
        server_id=int(server_id),
        session_version=sv,
        client_ip=ip,
        device_id=device_id,
    )
    set_grant = False
    stepup_used = False
    set_stepup = False

    if demo_sim:
        has_grant = True
        set_grant = True
    elif privileged:
        # Fleet grant is not enough. TOTP on this POST or a just-issued step-up proof.
        stepup_ok = cons.consume_stepup_proof(
            request.cookies.get(cons.CONSOLE_STEPUP_COOKIE),
            user_id=int(user.id),
            session_version=sv,
            client_ip=ip,
            device_id=device_id,
        )
        if not stepup_ok:
            ok, err = _verify_console_2fa(session, user, totp_code=totp_code)
            if not ok:
                _audit(
                    session,
                    user_id=user.id,
                    server_id=server_id,
                    action="ssh_console_denied",
                    details=f"privileged 2FA failed ({err}) ip={ip}",
                    status="failed",
                )
                return JSONResponse({"ok": False, "error": err}, status_code=403)
        stepup_used = True
        set_grant = not cons.require_2fa_every_shell()
    elif not has_grant:
        ok, err = _verify_console_2fa(session, user, totp_code=totp_code)
        if not ok:
            _audit(
                session,
                user_id=user.id,
                server_id=server_id,
                action="ssh_console_denied",
                details=f"2FA failed ({err}) ip={ip}",
                status="failed",
            )
            return JSONResponse({"ok": False, "error": err}, status_code=403)
        set_grant = not cons.require_2fa_every_shell()
        set_stepup = True

    reason_s = (reason or "").strip()[:200]
    try:
        if cons.slots_remaining(int(user.id)) <= 0:
            return JSONResponse({"ok": False, "error": "limit"}, status_code=429)
        ticket = cons.mint_ticket(
            user_id=int(user.id),
            server_id=int(server_id),
            session_version=sv,
            client_ip=ip,
            device_id=device_id,
            identity_id=int(ident.id) if ident and ident.id else None,
            identity_role=ident.role if ident else None,
            reason=reason_s if privileged else None,
        )
    except cons.ConsoleDisabled as e:
        return JSONResponse({"ok": False, "error": "disabled", "detail": str(e)}, status_code=403)
    except cons.ConsoleDenied as e:
        return JSONResponse({"ok": False, "error": "denied", "detail": str(e)}, status_code=403)

    body = {
        "ok": True,
        "ticket": ticket,
        "ws_path": f"/servers/{server_id}/console/ws",
        "idle_sec": cons.idle_sec(),
        "max_session_sec": cons.max_session_sec(),
        "grant_active": has_grant or set_grant,
        "grant_minutes": cons.grant_minutes(),
        "require_2fa_every_shell": (
            False if demo_sim else cons.require_2fa_every_shell()
        ),
        "slots_remaining": max(0, cons.slots_remaining(int(user.id)) - 1),
        "max_shells": cons.max_per_user(),
        "no_resume": True,
        "demo_console": demo_sim,
        "identity_id": ident.id if ident else None,
        "identity_role": ident.role if ident else "fleet",
        "identity_label": ident.label if ident else "Fleet",
        "identity_username": ident.username if ident else server.ssh_username,
    }
    response = JSONResponse(body)
    if cons.bind_device_enabled():
        response.set_cookie(
            cons.CONSOLE_DEVICE_COOKIE,
            device_id,
            **cookie_auth_kwargs(max_age=60 * 60 * 24 * 400),
        )
    if set_grant:
        grant = cons.mint_grant(
            user_id=int(user.id),
            server_id=int(server_id),
            session_version=sv,
            client_ip=ip,
            device_id=device_id,
        )
        response.set_cookie(
            cons.CONSOLE_GRANT_COOKIE,
            grant,
            **cookie_auth_kwargs(max_age=cons.grant_minutes() * 60),
        )
    if stepup_used:
        response.delete_cookie(cons.CONSOLE_STEPUP_COOKIE, **cookie_delete_kwargs())
    elif set_stepup:
        response.set_cookie(
            cons.CONSOLE_STEPUP_COOKIE,
            cons.mint_stepup_proof(
                user_id=int(user.id),
                session_version=sv,
                client_ip=ip,
                device_id=device_id,
            ),
            **cookie_auth_kwargs(max_age=cons.STEPUP_SEC),
        )
    return response


@router.post("/{server_id}/console/webauthn/options")
async def console_webauthn_options(
    request: Request,
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_console_user),
):
    """PublicKeyCredentialRequestOptions for console step-up (passkey)."""
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked
    if cons.is_demo_console():
        return JSONResponse(
            {
                "ok": False,
                "error": "demo_console",
                "detail": "Demo console does not use passkey step-up",
            },
            status_code=400,
        )
    try:
        cons.require_enabled()
    except cons.ConsoleDisabled as e:
        return JSONResponse({"ok": False, "error": "disabled", "detail": str(e)}, status_code=403)
    try:
        options_json, chal = wa_svc.authentication_options_json(session, user)
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": "passkey_unavailable", "detail": str(e)[:120]},
            status_code=400,
        )
    resp = JSONResponse({"ok": True, "options": json.loads(options_json)})
    resp.set_cookie(
        wa_svc.CHALLENGE_COOKIE_AUTH,
        chal,
        **cookie_auth_kwargs(max_age=wa_svc.CHALLENGE_MINUTES * 60),
    )
    return resp


@router.post("/{server_id}/console/webauthn/verify")
async def console_webauthn_verify(
    request: Request,
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_console_user),
):
    """Verify passkey for console; sets grant cookie (unless every-shell 2FA)."""
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked
    if cons.is_demo_console():
        return JSONResponse(
            {
                "ok": False,
                "error": "demo_console",
                "detail": "Demo console does not use passkey step-up",
            },
            status_code=400,
        )
    try:
        cons.require_enabled()
    except cons.ConsoleDisabled as e:
        return JSONResponse({"ok": False, "error": "disabled", "detail": str(e)}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
    credential = body.get("credential") if isinstance(body, dict) else None
    if not isinstance(credential, dict):
        return JSONResponse({"ok": False, "error": "bad_credential"}, status_code=400)

    chal = request.cookies.get(wa_svc.CHALLENGE_COOKIE_AUTH)
    try:
        wa_svc.verify_authentication(session, user, credential, chal)
    except Exception as e:
        ip = client_ip_from_request(request) or ""
        _audit(
            session,
            user_id=user.id,
            server_id=server_id,
            action="ssh_console_denied",
            details=f"passkey failed ip={ip} {str(e)[:80]}",
            status="failed",
        )
        return JSONResponse({"ok": False, "error": "2fa_bad_code"}, status_code=403)

    sv = user_session_version(user)
    ip = client_ip_from_request(request) or ""
    device_id = cons.ensure_device_id(request.cookies.get(cons.CONSOLE_DEVICE_COOKIE))
    payload = {
        "ok": True,
        "grant_active": not cons.require_2fa_every_shell(),
        "grant_minutes": cons.grant_minutes(),
        "no_resume": True,
    }
    # When every-shell 2FA is on, return a single-use ticket immediately after passkey
    if cons.require_2fa_every_shell():
        try:
            if cons.slots_remaining(int(user.id)) <= 0:
                return JSONResponse({"ok": False, "error": "limit"}, status_code=429)
            payload["ticket"] = cons.mint_ticket(
                user_id=int(user.id),
                server_id=int(server_id),
                session_version=sv,
                client_ip=ip,
                device_id=device_id,
            )
            payload["ws_path"] = f"/servers/{server_id}/console/ws"
        except cons.ConsoleDenied as e:
            return JSONResponse(
                {"ok": False, "error": "denied", "detail": str(e)}, status_code=403
            )
    resp = JSONResponse(payload)
    resp.delete_cookie(wa_svc.CHALLENGE_COOKIE_AUTH, **cookie_delete_kwargs())
    if cons.bind_device_enabled():
        resp.set_cookie(
            cons.CONSOLE_DEVICE_COOKIE,
            device_id,
            **cookie_auth_kwargs(max_age=60 * 60 * 24 * 400),
        )
    if not cons.require_2fa_every_shell():
        grant = cons.mint_grant(
            user_id=int(user.id),
            server_id=int(server_id),
            session_version=sv,
            client_ip=ip,
            device_id=device_id,
        )
        resp.set_cookie(
            cons.CONSOLE_GRANT_COOKIE,
            grant,
            **cookie_auth_kwargs(max_age=cons.grant_minutes() * 60),
        )
    stepup = cons.mint_stepup_proof(
        user_id=int(user.id),
        session_version=sv,
        client_ip=ip,
        device_id=device_id,
    )
    resp.set_cookie(
        cons.CONSOLE_STEPUP_COOKIE,
        stepup,
        **cookie_auth_kwargs(max_age=cons.STEPUP_SEC),
    )
    return resp


@router.post("/{server_id}/console/grant/revoke")
async def revoke_console_grant(
    request: Request,
    server_id: int,
    user: User = Depends(get_console_user),
):
    """Drop the short-lived console grant (force re-2FA next shell)."""
    del server_id, user
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked
    response = JSONResponse({"ok": True})
    response.delete_cookie(cons.CONSOLE_GRANT_COOKIE, **cookie_delete_kwargs())
    return response


@router.post("/{server_id}/console/discard")
async def discard_console_session(
    request: Request,
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_console_user),
):
    """Destroy a soft-parked PTY and free its concurrent slot.

    Called when the user closes a shell/host tab so parked sessions do not
    exhaust ``MAX_PER_USER`` until idle timeout.
    """
    del session
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked
    try:
        cons.require_enabled()
    except cons.ConsoleDisabled as e:
        return JSONResponse(
            {"ok": False, "error": "disabled", "detail": str(e)}, status_code=403
        )

    resume = ""
    resumes: list[str] = []
    try:
        ctype = (request.headers.get("content-type") or "").lower()
        if "application/json" in ctype:
            body = await request.json()
            if isinstance(body, dict):
                resume = str(body.get("resume") or "").strip()
                raw = body.get("resumes")
                if isinstance(raw, list):
                    resumes = [str(x).strip() for x in raw if str(x).strip()]
        else:
            form = await request.form()
            resume = str(form.get("resume") or "").strip()
            multi = form.getlist("resumes") if hasattr(form, "getlist") else []
            resumes = [str(x).strip() for x in multi if str(x).strip()]
    except Exception:
        resume = ""
        resumes = []

    tokens = list(resumes)
    if resume:
        tokens.append(resume)
    # de-dupe
    seen: set[str] = set()
    uniq: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            uniq.append(t)

    if not uniq:
        return JSONResponse(
            {"ok": False, "error": "missing_resume", "detail": "resume token required"},
            status_code=400,
        )

    freed = 0
    for tok in uniq:
        if cons.discard_parked_for_user(
            tok, user_id=int(user.id), server_id=int(server_id)
        ):
            freed += 1

    return JSONResponse(
        {
            "ok": True,
            "freed": freed,
            "slots_remaining": cons.slots_remaining(int(user.id)),
        }
    )


def _user_from_cookie(websocket: WebSocket, session: Session) -> Optional[User]:
    raw = websocket.cookies.get("access_token")
    if not raw:
        return None
    if raw.startswith("Bearer "):
        raw = raw.split(" ", 1)[1]
    payload = decode_token_payload(raw)
    if not payload or payload.get("2fa_pending"):
        return None
    try:
        uid = int(payload.get("sub"))
        token_sv = int(payload.get("sv", 0) or 0)
    except (TypeError, ValueError):
        return None
    user = session.get(User, uid)
    if not user or not user.is_active:
        return None
    if token_sv != user_session_version(user):
        return None
    # Production: operator+. Demo D5: shared viewer may open simulated console.
    if cons.is_demo_console():
        return user
    if not role_at_least(user, ROLE_OPERATOR):
        return None
    return user


async def _console_hold_watch(resume_id: str) -> None:
    """Keep draining SSH while browser is away; expire on idle/max/hold."""
    try:
        while True:
            await asyncio.sleep(1.5)
            held = cons.get_held(resume_id)
            if not held:
                return
            if not cons.drain_held_channel(held):
                cons.destroy_held(resume_id, reason="ssh_eof")
                return
            why = cons.held_should_expire(held)
            if why:
                cons.destroy_held(resume_id, reason=why)
                return
    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.debug("hold watch %s: %s", resume_id[:8], e)
        cons.destroy_held(resume_id, reason="hold_error")


@router.websocket("/{server_id}/console/ws")
async def console_websocket(websocket: WebSocket, server_id: int):
    """
    WebSocket terminal bridge (in-app only).

    1. Origin must match Host (browser same-origin; no random WS clients)
    2. Session cookie + operator+
    3. First message: ``{"type":"auth","ticket":"..."}`` or ``{"type":"resume","resume":"..."}``
    4. On unexpected WS drop, SSH PTY is **parked** until idle/max (soft resume)
    5. Continuous revalidation while attached; JSON resize / bye / ping controls
    """
    if not cons.websocket_origin_allowed(websocket):
        await websocket.close(code=4403)
        return

    await websocket.accept()

    user_id: Optional[int] = None
    server_id_i = int(server_id)
    ip: Optional[str] = None
    opened: Optional[datetime] = None
    slot_held = False
    server_snap = None
    server_hostname = "?"
    ticket_payload: dict = {}
    expected_sv = 0
    device_id = ""
    client = None
    channel = None
    intentional_close = False
    park_on_exit = False
    resume_id = cons.mint_resume_id()
    is_resume = False
    started_mono = time.monotonic()
    last_activity = time.monotonic()

    def _ws_ip() -> str:
        """Same client IP resolution as HTTP (CF-Connecting-IP / XFF / peer)."""
        try:
            from ..services.request_ip import extract_client_ip

            peer = None
            if websocket.client:
                peer = websocket.client.host
            return extract_client_ip(dict(websocket.headers), peer) or ""
        except Exception:
            try:
                if websocket.client:
                    return websocket.client.host or ""
            except Exception:
                pass
            return ""

    # --- auth handshake ---
    try:
        raw_msg = await asyncio.wait_for(websocket.receive(), timeout=15.0)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await websocket.close(code=4408)
        return
    if raw_msg.get("type") == "websocket.disconnect":
        return
    text = raw_msg.get("text")
    if not text and raw_msg.get("bytes"):
        try:
            text = raw_msg["bytes"].decode("utf-8", errors="replace")
        except Exception:
            text = ""
    auth: dict = {}
    try:
        parsed = json.loads(text or "")
        if isinstance(parsed, dict):
            auth = parsed
    except Exception:
        auth = {}

    auth_type = str(auth.get("type") or "")
    ticket = str(auth.get("ticket") or "") if auth_type == "auth" else ""
    resume_tok = str(auth.get("resume") or "") if auth_type == "resume" else ""

    if auth_type not in ("auth", "resume") or (auth_type == "auth" and not ticket) or (
        auth_type == "resume" and not resume_tok
    ):
        await websocket.send_text(
            "\r\n*** Missing console auth (ticket or resume) ***\r\n"
        )
        await websocket.close(code=4403)
        return

    try:
        with Session(engine) as session:
            user = _user_from_cookie(websocket, session)
            if not user:
                await websocket.close(code=4401)
                return
            server = session.get(Server, server_id)
            if not server:
                await websocket.close(code=4404)
                return

            sv = user_session_version(user)
            expected_sv = sv
            ip = _ws_ip()
            device_id = cons.ensure_device_id(
                websocket.cookies.get(cons.CONSOLE_DEVICE_COOKIE)
            )
            user_id = int(user.id)
            server_hostname = (
                server.hostname or getattr(server, "ip_address", None) or server.name
            )

            if auth_type == "resume":
                is_resume = True
                try:
                    held = cons.claim_resume(
                        resume_tok,
                        user_id=user_id,
                        server_id=int(server_id),
                        session_version=sv,
                        device_id=device_id,
                        client_ip=ip,
                    )
                except (cons.ConsoleDisabled, cons.ConsoleDenied) as e:
                    await websocket.send_text(f"\r\n*** Resume failed: {e} ***\r\n")
                    try:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "ph_session",
                                    "ended": True,
                                    "reason": "resume_failed",
                                }
                            )
                        )
                    except Exception:
                        pass
                    await websocket.close(code=4403)
                    return
                # Slot already held from original open
                slot_held = True
                client = held.client
                channel = held.channel
                ticket_payload = held.ticket_payload
                started_mono = held.started_mono
                last_activity = held.last_activity_mono
                resume_id = held.resume_id
                opened = datetime.utcnow()
                # Replay buffered output while detached
                buffered = held.take_out()
                if buffered:
                    try:
                        await websocket.send_bytes(buffered)
                    except Exception:
                        try:
                            await websocket.send_text(
                                buffered.decode("utf-8", errors="replace")
                            )
                        except Exception:
                            pass
                await websocket.send_text(
                    json.dumps({"type": "ph_session", "resume": resume_id, "resumed": True})
                )
                await websocket.send_text(
                    f"\r\n*** Resumed console → {server_hostname} "
                    f"(idle {cons.idle_sec()}s / max {cons.max_session_sec()}s) ***\r\n"
                )
            else:
                try:
                    ticket_payload = cons.consume_ticket(
                        ticket,
                        user_id=user_id,
                        server_id=int(server_id),
                        session_version=sv,
                        client_ip=ip,
                        device_id=device_id,
                    )
                    cons.try_acquire_slot(user_id)
                    slot_held = True
                except (cons.ConsoleDisabled, cons.ConsoleDenied) as e:
                    await websocket.send_text(f"\r\n*** {e} ***\r\n")
                    await websocket.close(code=4403)
                    return

                opened = datetime.utcnow()
                demo_note = " demo_sim=1" if cons.is_demo_console() else ""
                ident_note = ""
                ident_row = None
                from ..services import ssh_identities as ident_svc

                iid = ticket_payload.get("iid")
                if iid:
                    ident_row = ident_svc.get_by_id(session, int(server_id), int(iid))
                if (ticket_payload.get("role") or "") == ident_svc.ROLE_PRIVILEGED and (
                    ident_row is None or ident_row.role != ident_svc.ROLE_PRIVILEGED
                ):
                    await websocket.send_text("\r\n*** Privileged identity is no longer available ***\r\n")
                    await websocket.close(code=4403)
                    return
                if ident_row:
                    ident_note = (
                        f" identity={ident_row.role}:{ident_row.username}"
                        f" fp={ident_row.key_fingerprint or '-'}"
                    )
                why = (ticket_payload.get("why") or "").strip()
                if why:
                    ident_note += f" reason={why[:200]}"
                _audit(
                    session,
                    user_id=user.id,
                    server_id=server_id,
                    action="ssh_console_open",
                    details=(
                        f"ip={ip or '?'} user={user.email} "
                        f"bind_ip={cons.bind_ip_enabled()} bind_device={cons.bind_device_enabled()}"
                        f"{demo_note}{ident_note}"
                    ),
                )
                server_snap = ident_svc.overlay_server_for_identity(server, ident_row)

        if not is_resume:
            # Production: Paramiko. Demo D5: in-process simulated shell (no TCP).
            client, channel = await asyncio.to_thread(
                cons.open_session_channel, server_snap
            )
            started_mono = time.monotonic()
            last_activity = started_mono
            await websocket.send_text(
                json.dumps({"type": "ph_session", "resume": resume_id, "resumed": False})
            )
            if cons.is_demo_console():
                await websocket.send_text(
                    f"\r\n*** Demo console (simulated) → {server_hostname} "
                    f"— no live SSH · idle {cons.idle_sec()}s / max {cons.max_session_sec()}s ***\r\n"
                )
            else:
                await websocket.send_text(
                    f"\r\n*** PiHerder console → {server_hostname} "
                    f"(idle {cons.idle_sec()}s / max {cons.max_session_sec()}s · "
                    f"survives app switch) ***\r\n"
                )

        stop = asyncio.Event()
        last_revalidate = time.monotonic()
        park_on_exit = True  # default: park on WS drop unless bye/timeout/error

        async def revalidate_bindings() -> bool:
            nonlocal last_revalidate
            now = time.monotonic()
            if now - last_revalidate < cons.revalidate_sec():
                return True
            last_revalidate = now
            with Session(engine) as session:
                ok, reason = cons.session_still_valid(
                    session, user_id=int(user_id), expected_sv=expected_sv
                )
                if not ok:
                    await websocket.send_text(
                        f"\r\n*** Session ended ({reason}) — reconnect from PiHerder ***\r\n"
                    )
                    return False
            cur_device = websocket.cookies.get(cons.CONSOLE_DEVICE_COOKIE) or device_id
            cur_ip = _ws_ip()
            ok, reason = cons.binding_still_valid(
                ticket_payload, client_ip=cur_ip, device_id=cur_device
            )
            if not ok:
                # IP change while attached: warn but do not kill if device still matches
                # (mobile network handoff). Device change always kills.
                if reason == "device_changed":
                    await websocket.send_text(
                        f"\r\n*** Binding failed ({reason}) — shell closed ***\r\n"
                    )
                    return False
                if reason == "ip_changed" and not (
                    cons.bind_device_enabled() and device_id and cur_device == device_id
                ):
                    await websocket.send_text(
                        f"\r\n*** Binding failed ({reason}) — shell closed ***\r\n"
                    )
                    return False
            with Session(engine) as session:
                u2 = _user_from_cookie(websocket, session)
                if not u2 or int(u2.id) != int(user_id):
                    await websocket.send_text(
                        "\r\n*** Login session missing — shell closed ***\r\n"
                    )
                    return False
            return True

        async def pump_ssh_out():
            nonlocal last_activity, park_on_exit, intentional_close
            while not stop.is_set():
                try:
                    if not await revalidate_bindings():
                        # Auth lost — destroy, do not park for arbitrary reclaim
                        park_on_exit = False
                        intentional_close = True
                        stop.set()
                        break
                    if channel.recv_ready():
                        data = channel.recv(8192)
                        if not data:
                            park_on_exit = False
                            stop.set()
                            break
                        last_activity = time.monotonic()
                        try:
                            await websocket.send_bytes(data)
                        except Exception:
                            await websocket.send_text(
                                data.decode("utf-8", errors="replace")
                            )
                    elif channel.recv_stderr_ready():
                        data = channel.recv_stderr(4096)
                        if data:
                            last_activity = time.monotonic()
                            await websocket.send_bytes(data)
                    elif channel.exit_status_ready():
                        park_on_exit = False
                        stop.set()
                        break
                    else:
                        now = time.monotonic()
                        if now - last_activity > cons.idle_sec():
                            await websocket.send_text("\r\n*** Idle timeout ***\r\n")
                            park_on_exit = False
                            stop.set()
                            break
                        if now - started_mono > cons.max_session_sec():
                            await websocket.send_text(
                                "\r\n*** Session time limit ***\r\n"
                            )
                            park_on_exit = False
                            stop.set()
                            break
                        # Faster poll while the user is typing so echo feels immediate
                        if now - last_activity < 0.8:
                            await asyncio.sleep(0.005)
                        else:
                            await asyncio.sleep(0.02)
                except Exception as e:
                    logger.debug("console pump out: %s", e)
                    # WS likely dead — leave park_on_exit True so SSH is held
                    stop.set()
                    break

        out_task = asyncio.create_task(pump_ssh_out())

        try:
            while not stop.is_set():
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=1.0)
                except asyncio.TimeoutError:
                    if not await revalidate_bindings():
                        park_on_exit = False
                        stop.set()
                    continue
                except WebSocketDisconnect:
                    stop.set()
                    break

                if message.get("type") == "websocket.disconnect":
                    stop.set()
                    break

                if "bytes" in message and message["bytes"] is not None:
                    data = message["bytes"]
                    last_activity = time.monotonic()
                    # Prefer in-loop send for typing latency; channel is non-blocking.
                    try:
                        channel.send(data)
                    except Exception:
                        await asyncio.to_thread(channel.send, data)
                elif "text" in message and message["text"] is not None:
                    text_in = message["text"]
                    last_activity = time.monotonic()
                    if text_in.startswith("{") and '"type"' in text_in:
                        try:
                            ctl = json.loads(text_in)
                            t = ctl.get("type")
                            if t == "resize":
                                cols = max(20, min(int(ctl.get("cols") or 80), 500))
                                rows = max(5, min(int(ctl.get("rows") or 24), 200))
                                await asyncio.to_thread(
                                    channel.resize_pty, width=cols, height=rows
                                )
                                continue
                            if t == "ping":
                                try:
                                    await websocket.send_text(
                                        json.dumps(
                                            {
                                                "type": "ph_session",
                                                "resume": resume_id,
                                                "pong": True,
                                            }
                                        )
                                    )
                                except Exception:
                                    pass
                                continue
                            if t == "bye":
                                intentional_close = True
                                park_on_exit = False
                                stop.set()
                                break
                            # Explicit stdin (hex) — used for Tab/Esc/arrows so C0
                            # bytes are never lost in text frames / proxies.
                            if t == "stdin":
                                hx = str(ctl.get("hex") or "").strip()
                                raw = b""
                                if hx:
                                    try:
                                        raw = bytes.fromhex(hx)
                                    except ValueError:
                                        raw = b""
                                if not raw and ctl.get("data") is not None:
                                    raw = str(ctl.get("data") or "").encode(
                                        "utf-8", errors="replace"
                                    )
                                if raw:
                                    try:
                                        channel.send(raw)
                                    except Exception:
                                        await asyncio.to_thread(channel.send, raw)
                                continue
                            # Unknown JSON control — do not dump into the PTY
                            continue
                        except Exception:
                            pass
                    try:
                        channel.send(text_in)
                    except Exception:
                        await asyncio.to_thread(channel.send, text_in)
        finally:
            stop.set()
            out_task.cancel()
            try:
                await out_task
            except Exception:
                pass

    except Exception as e:
        logger.warning("console session failed server_id=%s: %s", server_id, e)
        park_on_exit = False
        try:
            await websocket.send_text(f"\r\n*** Connection failed: {e} ***\r\n")
        except Exception:
            pass
    finally:
        # Soft-resume: park SSH if browser dropped WS (app switch / sleep)
        do_park = (
            park_on_exit
            and not intentional_close
            and channel is not None
            and client is not None
            and user_id is not None
            and not getattr(channel, "closed", False)
        )
        if do_park:
            try:
                # channel.closed may not exist on all paramiko versions
                if channel.exit_status_ready() and not channel.recv_ready():
                    do_park = False
            except Exception:
                pass
        if do_park:
            held = cons.HeldConsole(
                resume_id=resume_id,
                user_id=int(user_id),
                server_id=server_id_i,
                session_version=int(expected_sv),
                ticket_payload=dict(ticket_payload or {}),
                device_id=device_id or "",
                client=client,
                channel=channel,
                started_mono=started_mono,
                last_activity_mono=last_activity,
                held_at_mono=time.monotonic(),
                server_hostname=server_hostname,
            )
            cons.park_console(held)
            # Slot stays acquired; hold-watch drains output + enforces timeouts
            asyncio.create_task(_console_hold_watch(resume_id))
            client = None  # prevent close below
            channel = None
            slot_held = False  # don't release in finally
            logger.info(
                "console parked resume=%s user=%s server=%s",
                resume_id[:10],
                user_id,
                server_id_i,
            )
        else:
            # Tell client not to soft-resume (exit, idle, bye, max session, auth loss).
            try:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "ph_session",
                            "ended": True,
                            "reason": "closed" if intentional_close else "ended",
                        }
                    )
                )
            except Exception:
                pass
            if channel is not None:
                try:
                    channel.close()
                except Exception:
                    pass
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            if slot_held and user_id is not None:
                cons.release_slot(user_id)
            if user_id is not None and opened is not None and not is_resume:
                try:
                    with Session(engine) as session:
                        dur = int((datetime.utcnow() - opened).total_seconds())
                        _audit(
                            session,
                            user_id=user_id,
                            server_id=server_id_i,
                            action="ssh_console_close",
                            details=f"duration_sec={dur} ip={ip or '?'}",
                        )
                except Exception:
                    logger.exception("console close audit failed")
            elif user_id is not None and intentional_close:
                try:
                    with Session(engine) as session:
                        _audit(
                            session,
                            user_id=user_id,
                            server_id=server_id_i,
                            action="ssh_console_close",
                            details=f"bye ip={ip or '?'}",
                        )
                except Exception:
                    pass
        try:
            await websocket.close()
        except Exception:
            pass

"""Web SSH console — mint ticket + WebSocket PTY (v1.2 Stream W).

Private keys stay server-side. Default kill switch: PIHERDER_SSH_CONSOLE=false.

**In-app only (anti-exploit):**
  · Session cookie + operator+ (no Bearer API path)
  · Ticket mint requires same-site browser Origin/Referer (blocks cross-site POST)
  · WebSocket requires matching Origin and session cookie
  · Ticket sent in first WS message (not query string — no log/Referer leak)
  · 2FA step-up (TOTP, backup, or passkey) before grant/ticket
  · CSP + frame-ancestors none (cannot embed console)
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
from sqlmodel import Session

from .. import templates as templates_mod
from ..database import get_session, engine
from ..models import Server, User
from ..security.auth import (
    get_operator_user,
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
from ..services import ssh as ssh_service

router = APIRouter()
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
    """Console always requires enrolled 2FA + valid code (W3) when grant missing."""
    if not wa_svc.user_has_2fa(session, user):
        return False, "enroll_2fa"
    from ..security.auth import (
        decrypt_totp_secret,
        verify_totp_code,
        consume_backup_code,
    )

    code = (totp_code or "").strip().replace(" ", "")
    if not code:
        return False, "2fa_required"
    if getattr(user, "totp_enabled", False) and user.totp_secret_encrypted:
        try:
            secret = decrypt_totp_secret(user.totp_secret_encrypted)
            if verify_totp_code(secret, code):
                return True, ""
        except Exception:
            pass
    if consume_backup_code(session, int(user.id), code):
        return True, ""
    return False, "2fa_bad_code"


@router.get("/{server_id}/console", response_class=HTMLResponse)
async def console_page(
    request: Request,
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Console shell page (xterm multi-shell). Ticket minted via POST before WS connect."""
    server = session.get(Server, server_id)
    if not server:
        return RedirectResponse("/servers", status_code=303)

    enabled = cons.console_enabled()
    has_2fa = wa_svc.user_has_2fa(session, user)
    has_key = bool(
        getattr(server, "ssh_private_key_encrypted", None)
        or getattr(server, "ssh_password_encrypted", None)
    )
    sv = user_session_version(user)
    grant_ok = cons.grant_valid(
        request.cookies.get(cons.CONSOLE_GRANT_COOKIE),
        user_id=int(user.id),
        server_id=int(server_id),
        session_version=sv,
    )
    remaining = cons.slots_remaining(int(user.id)) if enabled else 0
    has_passkeys = False
    try:
        has_passkeys = wa_svc.has_passkeys(session, int(user.id))
    except Exception:
        has_passkeys = False

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_console.html",
        context={
            "title": f"Console · {server.name}",
            "user": user,
            "server": server,
            "console_enabled": enabled,
            "has_2fa": has_2fa,
            "has_passkeys": has_passkeys,
            "has_ssh_cred": has_key,
            "grant_active": grant_ok,
            "grant_minutes": cons.grant_minutes(),
            "require_2fa_every_shell": cons.require_2fa_every_shell(),
            "max_shells": cons.max_per_user(),
            "slots_remaining": remaining,
            "ticket_ttl": cons.ticket_ttl_sec(),
            "idle_sec": cons.idle_sec(),
            "max_session_sec": cons.max_session_sec(),
        },
    )


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


@router.post("/{server_id}/console/ticket")
async def mint_console_ticket(
    request: Request,
    server_id: int,
    totp_code: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Mint single-use ticket; 2FA required unless a valid per-host grant cookie exists.

    Ticket is returned in JSON only — never put on the WebSocket URL (log/Referer leak).
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

    if not (
        getattr(server, "ssh_private_key_encrypted", None)
        or getattr(server, "ssh_password_encrypted", None)
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
    grant_cookie = request.cookies.get(cons.CONSOLE_GRANT_COOKIE)
    has_grant = cons.grant_valid(
        grant_cookie,
        user_id=int(user.id),
        server_id=int(server_id),
        session_version=sv,
    )
    set_grant = False

    if not has_grant:
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

    try:
        if cons.slots_remaining(int(user.id)) <= 0:
            return JSONResponse({"ok": False, "error": "limit"}, status_code=429)
        ticket = cons.mint_ticket(
            user_id=int(user.id),
            server_id=int(server_id),
            session_version=sv,
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
        "require_2fa_every_shell": cons.require_2fa_every_shell(),
        "slots_remaining": max(0, cons.slots_remaining(int(user.id)) - 1),
        "max_shells": cons.max_per_user(),
    }
    response = JSONResponse(body)
    if set_grant:
        grant = cons.mint_grant(
            user_id=int(user.id),
            server_id=int(server_id),
            session_version=sv,
        )
        response.set_cookie(
            cons.CONSOLE_GRANT_COOKIE,
            grant,
            **cookie_auth_kwargs(max_age=cons.grant_minutes() * 60),
        )
    return response


@router.post("/{server_id}/console/webauthn/options")
async def console_webauthn_options(
    request: Request,
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """PublicKeyCredentialRequestOptions for console step-up (passkey)."""
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked
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
    user: User = Depends(get_operator_user),
):
    """Verify passkey for console; sets grant cookie (unless every-shell 2FA)."""
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked
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
    payload = {
        "ok": True,
        "grant_active": not cons.require_2fa_every_shell(),
        "grant_minutes": cons.grant_minutes(),
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
            )
            payload["ws_path"] = f"/servers/{server_id}/console/ws"
        except cons.ConsoleDenied as e:
            return JSONResponse(
                {"ok": False, "error": "denied", "detail": str(e)}, status_code=403
            )
    resp = JSONResponse(payload)
    resp.delete_cookie(wa_svc.CHALLENGE_COOKIE_AUTH, **cookie_delete_kwargs())
    if not cons.require_2fa_every_shell():
        grant = cons.mint_grant(
            user_id=int(user.id),
            server_id=int(server_id),
            session_version=sv,
        )
        resp.set_cookie(
            cons.CONSOLE_GRANT_COOKIE,
            grant,
            **cookie_auth_kwargs(max_age=cons.grant_minutes() * 60),
        )
    return resp


@router.post("/{server_id}/console/grant/revoke")
async def revoke_console_grant(
    request: Request,
    server_id: int,
    user: User = Depends(get_operator_user),
):
    """Drop the short-lived console grant (force re-2FA next shell)."""
    del server_id, user
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked
    response = JSONResponse({"ok": True})
    response.delete_cookie(cons.CONSOLE_GRANT_COOKIE, **cookie_delete_kwargs())
    return response


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
    if not role_at_least(user, ROLE_OPERATOR):
        return None
    return user


@router.websocket("/{server_id}/console/ws")
async def console_websocket(websocket: WebSocket, server_id: int):
    """
    WebSocket terminal bridge (in-app only).

    1. Origin must match Host (browser same-origin; no random WS clients)
    2. Session cookie + operator+
    3. First text message: ``{"type":"auth","ticket":"..."}`` (ticket never in URL)
    4. Then binary/text PTY traffic; JSON resize control messages
    """
    # Reject before accept when Origin is wrong (some clients never send Origin)
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

    # --- auth handshake: wait for ticket in first message (not query string) ---
    ticket = ""
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
    try:
        auth = json.loads(text or "")
        if isinstance(auth, dict) and auth.get("type") == "auth":
            ticket = str(auth.get("ticket") or "")
    except Exception:
        ticket = ""
    if not ticket:
        # Legacy fallback: query param (discouraged; will be removed)
        ticket = websocket.query_params.get("ticket") or ""
    if not ticket:
        await websocket.send_text("\r\n*** Missing console ticket ***\r\n")
        await websocket.close(code=4403)
        return

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
        try:
            cons.consume_ticket(
                ticket,
                user_id=int(user.id),
                server_id=int(server_id),
                session_version=sv,
            )
            cons.try_acquire_slot(int(user.id))
            slot_held = True
        except (cons.ConsoleDisabled, cons.ConsoleDenied) as e:
            await websocket.send_text(f"\r\n*** {e} ***\r\n")
            await websocket.close(code=4403)
            return

        try:
            if websocket.client:
                ip = websocket.client.host
        except Exception:
            ip = None
        xff = websocket.headers.get("x-forwarded-for") or websocket.headers.get("x-real-ip")
        if xff:
            ip = xff.split(",")[0].strip()

        opened = datetime.utcnow()
        _audit(
            session,
            user_id=user.id,
            server_id=server_id,
            action="ssh_console_open",
            details=f"ip={ip or '?'} user={user.email}",
        )
        user_id = int(user.id)
        server_snap = SimpleNamespace(
            id=server.id,
            name=server.name,
            hostname=server.hostname,
            ip_address=getattr(server, "ip_address", None),
            ssh_port=server.ssh_port,
            ssh_username=server.ssh_username,
            ssh_private_key_encrypted=server.ssh_private_key_encrypted,
            ssh_password_encrypted=server.ssh_password_encrypted,
        )
        server_hostname = (
            server.hostname or getattr(server, "ip_address", None) or server.name
        )

    client = None
    channel = None
    try:
        client = await asyncio.to_thread(ssh_service.get_ssh_client, server_snap)
        channel = await asyncio.to_thread(
            lambda: client.invoke_shell(term="xterm-256color", width=120, height=40)
        )
        channel.settimeout(0.0)

        await websocket.send_text(
            f"\r\n*** PiHerder console → {server_hostname} "
            f"(idle {cons.idle_sec()}s / max {cons.max_session_sec()}s) ***\r\n"
        )

        stop = asyncio.Event()
        last_activity = time.monotonic()
        started = time.monotonic()

        async def pump_ssh_out():
            nonlocal last_activity
            while not stop.is_set():
                try:
                    if channel.recv_ready():
                        data = channel.recv(8192)
                        if not data:
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
                        stop.set()
                        break
                    else:
                        now = time.monotonic()
                        if now - last_activity > cons.idle_sec():
                            await websocket.send_text("\r\n*** Idle timeout ***\r\n")
                            stop.set()
                            break
                        if now - started > cons.max_session_sec():
                            await websocket.send_text(
                                "\r\n*** Session time limit ***\r\n"
                            )
                            stop.set()
                            break
                        await asyncio.sleep(0.02)
                except Exception as e:
                    logger.debug("console pump out: %s", e)
                    stop.set()
                    break

        out_task = asyncio.create_task(pump_ssh_out())

        try:
            while not stop.is_set():
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=1.0)
                except asyncio.TimeoutError:
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
                    await asyncio.to_thread(channel.send, data)
                elif "text" in message and message["text"] is not None:
                    text = message["text"]
                    last_activity = time.monotonic()
                    if text.startswith("{") and '"type"' in text:
                        try:
                            ctl = json.loads(text)
                            if ctl.get("type") == "resize":
                                cols = max(20, min(int(ctl.get("cols") or 80), 500))
                                rows = max(5, min(int(ctl.get("rows") or 24), 200))
                                await asyncio.to_thread(
                                    channel.resize_pty, width=cols, height=rows
                                )
                                continue
                        except Exception:
                            pass
                    await asyncio.to_thread(channel.send, text)
        finally:
            stop.set()
            out_task.cancel()
            try:
                await out_task
            except Exception:
                pass

    except Exception as e:
        logger.warning("console session failed server_id=%s: %s", server_id, e)
        try:
            await websocket.send_text(f"\r\n*** Connection failed: {e} ***\r\n")
        except Exception:
            pass
    finally:
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
        try:
            await websocket.close()
        except Exception:
            pass
        if user_id is not None and opened is not None:
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

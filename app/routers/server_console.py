"""Web SSH console — mint ticket + WebSocket PTY (v1.2 Stream W).

Private keys stay server-side. Default kill switch: PIHERDER_SSH_CONSOLE=false.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from .. import templates as templates_mod
from ..database import get_session, engine
from ..models import Server, User
from ..security.auth import (
    get_operator_user,
    role_at_least,
    ROLE_OPERATOR,
    decode_token_payload,
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
    """Console always requires enrolled 2FA + valid code (W3)."""
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
    """Console shell page (xterm). Ticket minted via POST before WS connect."""
    server = session.get(Server, server_id)
    if not server:
        return RedirectResponse("/servers", status_code=303)

    enabled = cons.console_enabled()
    has_2fa = wa_svc.user_has_2fa(session, user)
    has_key = bool(getattr(server, "ssh_private_key_encrypted", None) or getattr(server, "ssh_password_encrypted", None))
    err = request.query_params.get("error") or ""
    msg = request.query_params.get("msg") or ""

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_console.html",
        context={
            "title": f"Console · {server.name}",
            "user": user,
            "server": server,
            "console_enabled": enabled,
            "has_2fa": has_2fa,
            "has_ssh_cred": has_key,
            "error": err,
            "msg": msg,
            "ticket_ttl": cons.ticket_ttl_sec(),
            "idle_sec": cons.idle_sec(),
            "max_session_sec": cons.max_session_sec(),
        },
    )


@router.post("/{server_id}/console/ticket")
async def mint_console_ticket(
    request: Request,
    server_id: int,
    totp_code: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Mint single-use ticket after 2FA; returns JSON for the console page."""
    from fastapi.responses import JSONResponse

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
            {"ok": False, "error": "no_ssh", "detail": "No SSH key or password stored for this host"},
            status_code=400,
        )

    ok, err = _verify_console_2fa(session, user, totp_code=totp_code)
    if not ok:
        ip = client_ip_from_request(request) or ""
        _audit(
            session,
            user_id=user.id,
            server_id=server_id,
            action="ssh_console_denied",
            details=f"2FA failed ({err}) ip={ip}",
            status="failed",
        )
        return JSONResponse({"ok": False, "error": err}, status_code=403)

    try:
        # Soft check slots before mint (real acquire on WS open)
        g, by_u = cons.live_counts()
        if g >= cons.max_global() or by_u.get(int(user.id), 0) >= cons.max_per_user():
            return JSONResponse({"ok": False, "error": "limit"}, status_code=429)
        ticket = cons.mint_ticket(user_id=int(user.id), server_id=int(server_id))
    except cons.ConsoleDisabled as e:
        return JSONResponse({"ok": False, "error": "disabled", "detail": str(e)}, status_code=403)
    except cons.ConsoleDenied as e:
        return JSONResponse({"ok": False, "error": "denied", "detail": str(e)}, status_code=403)

    return JSONResponse(
        {
            "ok": True,
            "ticket": ticket,
            "ws_path": f"/servers/{server_id}/console/ws",
            "idle_sec": cons.idle_sec(),
            "max_session_sec": cons.max_session_sec(),
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
    except (TypeError, ValueError):
        return None
    user = session.get(User, uid)
    if not user or not user.is_active:
        return None
    if not role_at_least(user, ROLE_OPERATOR):
        return None
    return user


@router.websocket("/{server_id}/console/ws")
async def console_websocket(websocket: WebSocket, server_id: int):
    """
    WebSocket terminal bridge.

    Query: ?ticket=...
    Client → server: text (keystrokes) or JSON resize {"type":"resize","cols":N,"rows":M}
    Server → client: binary or text PTY output
    """
    from types import SimpleNamespace
    import json

    ticket = websocket.query_params.get("ticket") or ""
    await websocket.accept()

    user_id: Optional[int] = None
    server_id_i = int(server_id)
    ip: Optional[str] = None
    opened: Optional[datetime] = None
    slot_held = False
    server_snap = None

    with Session(engine) as session:
        user = _user_from_cookie(websocket, session)
        if not user:
            await websocket.close(code=4401)
            return
        server = session.get(Server, server_id)
        if not server:
            await websocket.close(code=4404)
            return

        try:
            cons.consume_ticket(ticket, user_id=int(user.id), server_id=int(server_id))
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
        # Detach-safe snapshot for SSH connect after session closes
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
        server_hostname = server.hostname or getattr(server, "ip_address", None) or server.name

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
                            await websocket.send_text("\r\n*** Session time limit ***\r\n")
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

"""Add-host wizard — multi-step onboarding (H2.75 P2 / v0.7.0).

Orchestrates existing create / SSH / feature paths. Server row is source of truth.
Primary entry: GET /servers/new · Advanced single form: GET /servers/new/advanced
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select
from sqlalchemy import func
from starlette.concurrency import run_in_threadpool

from .. import templates as templates_mod
from ..database import get_session
from ..models import Server, User
from ..security import encryption
from ..security.auth import get_operator_user
from ..services import host_deps as host_deps_svc
from ..services import ssh as ssh_service
from ..services import ssh_onboarding
from ..services.server_audit import record_server_audit
from .server_common import server_redirect

router = APIRouter()

# Ordered steps — keys used in URLs and templates
WIZARD_STEPS: list[tuple[str, str]] = [
    ("identity", "Identity"),
    ("trust", "Trust"),
    ("connect", "Connect"),
    ("privilege", "Privilege"),
    ("features", "Features"),
    ("schedules", "Schedules"),
    ("network", "Network"),
    ("done", "Done"),
]
STEP_KEYS = [k for k, _ in WIZARD_STEPS]
STEP_LABELS = {k: lab for k, lab in WIZARD_STEPS}


def wizard_path(
    step: str = "identity",
    server_id: Optional[int] = None,
    **flash: str,
) -> str:
    """Build /servers/new?... URL for a wizard step (optional msg/error/detail)."""
    key = (step or "identity").strip().lower()
    if key not in STEP_KEYS:
        key = "identity"
    q: dict[str, str] = {"step": key}
    if server_id is not None:
        q["server_id"] = str(int(server_id))
    for k in ("msg", "error", "detail"):
        v = flash.get(k)
        if v:
            q[k] = str(v)[:200]
    return f"/servers/new?{urlencode(q)}"


def step_index(step: str) -> int:
    key = (step or "identity").strip().lower()
    try:
        return STEP_KEYS.index(key)
    except ValueError:
        return 0


def next_step_key(step: str) -> Optional[str]:
    i = step_index(step)
    if i + 1 < len(STEP_KEYS):
        return STEP_KEYS[i + 1]
    return None


def prev_step_key(step: str) -> Optional[str]:
    i = step_index(step)
    if i > 0:
        return STEP_KEYS[i - 1]
    return None


def infer_resume_step(server: Server) -> str:
    """Best-effort resume when opening wizard with only server_id."""
    has_key = bool((server.ssh_private_key_encrypted or "").strip())
    has_pw = bool((server.ssh_password_encrypted or "").strip())
    if not has_key and not has_pw:
        return "trust"
    # Connect and later — operator can jump; default after trust is connect
    return "connect"


def _require_server(session: Session, server_id: Optional[int]) -> Server:
    if not server_id:
        raise HTTPException(400, "server_id required for this step")
    server = session.get(Server, int(server_id))
    if not server:
        raise HTTPException(404, "Server not found")
    return server


def _wizard_context(
    *,
    user: User,
    step: str,
    server: Optional[Server] = None,
    error: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    key = step if step in STEP_KEYS else "identity"
    idx = step_index(key)
    steps_ui = []
    for i, (sk, lab) in enumerate(WIZARD_STEPS):
        steps_ui.append(
            {
                "key": sk,
                "label": lab,
                "index": i + 1,
                "state": (
                    "done"
                    if i < idx
                    else ("current" if i == idx else "todo")
                ),
            }
        )
    ctx: dict[str, Any] = {
        "title": f"Add server — {STEP_LABELS.get(key, key)}",
        "user": user,
        "wizard_step": key,
        "wizard_steps": steps_ui,
        "wizard_step_index": idx + 1,
        "wizard_step_total": len(WIZARD_STEPS),
        "wizard_prev": prev_step_key(key),
        "wizard_next": next_step_key(key),
        "server": server,
        "error": error,
    }
    if extra:
        ctx.update(extra)
    return ctx


@router.get("/new", response_class=HTMLResponse)
async def wizard_get(
    request: Request,
    step: str = "identity",
    server_id: Optional[int] = None,
    msg: str = "",
    error: str = "",
    detail: str = "",
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Guided multi-step add-host wizard."""
    key = (step or "identity").strip().lower()
    if key not in STEP_KEYS:
        return RedirectResponse(wizard_path("identity"), status_code=303)

    server: Optional[Server] = None
    if server_id is not None:
        server = session.get(Server, int(server_id))
        if not server:
            return RedirectResponse(wizard_path("identity"), status_code=303)
        # Identity is only for brand-new rows; resume later steps
        if key == "identity":
            resume = infer_resume_step(server)
            return RedirectResponse(
                wizard_path(resume, server.id), status_code=303
            )
    elif key != "identity":
        # Later steps need a server
        return RedirectResponse(wizard_path("identity"), status_code=303)

    has_pw = bool(server and (server.ssh_password_encrypted or "").strip())
    has_key = bool(server and (server.ssh_private_key_encrypted or "").strip())
    pub = (server.ssh_public_key or "").strip() if server else ""
    has_real_pub = bool(server and ssh_onboarding.is_real_public_key(pub))
    key_install_script = ""
    if server and has_real_pub and pub:
        try:
            key_install_script = ssh_onboarding.build_key_install_script(
                pub, username=server.ssh_username or ""
            )
        except Exception:
            key_install_script = ""
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="add_server_wizard.html",
        context=_wizard_context(
            user=user,
            step=key,
            server=server,
            extra={
                "flash_msg": (msg or "").strip(),
                "flash_error": (error or "").strip(),
                "flash_detail": (detail or "").strip()[:200],
                "has_ssh_password": has_pw,
                "has_ssh_key": has_key,
                "ssh_public_key": pub,
                "has_real_public_key": has_real_pub,
                "key_install_script": key_install_script,
            },
        ),
    )


@router.get("/new/advanced", response_class=HTMLResponse)
async def wizard_advanced_form(
    request: Request,
    user: User = Depends(get_operator_user),
):
    """Classic single-page add form (secondary path)."""
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="add_server.html",
        context={
            "title": "Add Server (advanced)",
            "user": user,
            "advanced": True,
        },
    )


@router.post("/new/identity")
async def wizard_identity_post(
    name: str = Form(...),
    hostname: str = Form(...),
    ssh_username: str = Form("bjorn"),
    ssh_port: int = Form(22),
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Step 1 — create server row (keys come in Trust step)."""
    sname = (name or "").strip()
    host = (hostname or "").strip()
    if not sname or not host:
        raise HTTPException(400, "Name and hostname are required")
    try:
        port = int(ssh_port)
    except (TypeError, ValueError):
        port = 22
    if port < 1 or port > 65535:
        port = 22

    current_max = session.scalar(select(func.max(Server.sort_order)))
    next_sort = int(current_max or 0) + 10
    server = Server(
        name=sname,
        hostname=host,
        ssh_username=(ssh_username or "bjorn").strip() or "bjorn",
        ssh_port=port,
        sort_order=next_sort,
        backup_enabled=True,
    )
    session.add(server)
    session.commit()
    session.refresh(server)

    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_create",
        details={
            "name": server.name,
            "hostname": server.hostname,
            "ssh_username": server.ssh_username,
            "auth_method": "wizard_pending_trust",
            "message": f"Server {server.name} added via wizard (identity)",
        },
    )
    session.commit()
    return RedirectResponse(wizard_path("trust", server.id), status_code=303)


@router.post("/new/trust")
async def wizard_trust_post(
    server_id: int = Form(...),
    key_mode: str = Form("generate"),
    private_key: str = Form(""),
    ssh_password: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Step 2 — generate/upload key and optional bootstrap password."""
    server = _require_server(session, server_id)
    mode = (key_mode or "generate").strip().lower()
    priv_enc = None
    pub = None
    pw_enc = None
    comment = f"piherder@{server.hostname or server.name or 'server'}"

    if mode == "generate":
        pub, priv = ssh_service.generate_keypair(comment=comment)
        priv_enc = encryption.encrypt_str(priv)
        if ssh_password and ssh_password.strip():
            pw_enc = encryption.encrypt_str(ssh_password.strip())
    elif mode == "password":
        if not ssh_password or not ssh_password.strip():
            raise HTTPException(400, "Password required when using password auth")
        pub = "(password auth - no public key)"
        pw_enc = encryption.encrypt_str(ssh_password.strip())
    else:
        if not private_key.strip():
            raise HTTPException(400, "Private key required for upload mode")
        priv_plain = private_key.strip()
        priv_enc = encryption.encrypt_str(priv_plain)
        try:
            pub = ssh_onboarding.public_key_from_private(priv_plain, comment=comment)
        except Exception:
            pub = "(provided with private key - test connection to verify)"
        if ssh_password and ssh_password.strip():
            pw_enc = encryption.encrypt_str(ssh_password.strip())

    server.ssh_private_key_encrypted = priv_enc
    server.ssh_public_key = pub
    server.ssh_password_encrypted = pw_enc
    session.add(server)
    auth_method = {
        "generate": "generated_key",
        "upload": "uploaded_key",
        "password": "password_auth",
    }.get(mode, mode)
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_wizard_trust",
        details={
            "auth_method": auth_method,
            "message": f"Wizard trust step for {server.name}",
        },
    )
    if pw_enc:
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_password_set",
            message="SSH password set on wizard trust step",
        )
    session.commit()
    return RedirectResponse(wizard_path("connect", server.id), status_code=303)


@router.post("/new/continue")
async def wizard_continue_post(
    server_id: int = Form(...),
    step: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Advance a stub / guidance step without extra business logic."""
    del user
    server = _require_server(session, server_id)
    key = (step or "").strip().lower()
    nxt = next_step_key(key)
    if not nxt:
        return RedirectResponse(wizard_path("done", server.id), status_code=303)
    return RedirectResponse(wizard_path(nxt, server.id), status_code=303)


@router.post("/new/connect/test")
async def wizard_connect_test(
    server_id: int = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Test SSH from Connect step; stay in wizard."""
    server = _require_server(session, server_id)
    result = await run_in_threadpool(ssh_onboarding.test_connection_detail, server)
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_ssh_test",
        status="success" if result.ok else "failed",
        message=result.message,
        details={k: v for k, v in result.details.items() if k not in ("new_private_key",)},
    )
    if result.ok:
        try:
            await run_in_threadpool(host_deps_svc.check_and_persist, session, server)
        except Exception:
            pass
    session.commit()
    if result.ok:
        return RedirectResponse(
            wizard_path("connect", server.id, msg="ssh_ok"),
            status_code=303,
        )
    return RedirectResponse(
        wizard_path(
            "connect",
            server.id,
            error="ssh_fail",
            detail=result.message[:180],
        ),
        status_code=303,
    )


@router.post("/new/connect/deploy-key")
async def wizard_connect_deploy_key(
    server_id: int = Form(...),
    ssh_password: str = Form(""),
    clear_password_after: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Deploy public key from Connect step; stay in wizard."""
    server = _require_server(session, server_id)
    password_override = ssh_password.strip() if ssh_password and ssh_password.strip() else None
    result = await run_in_threadpool(
        ssh_onboarding.deploy_public_key,
        server,
        password_override=password_override,
    )
    if result.ok and result.details.get("public_key"):
        derived = result.details["public_key"]
        if derived and server.ssh_public_key != derived:
            server.ssh_public_key = derived
    if result.ok:
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_ssh_key_deployed",
            message=result.message,
            details={
                "already_auth": result.details.get("already_auth"),
                "installed": result.details.get("installed"),
                "already_present": result.details.get("already_present"),
                "via": "wizard",
            },
        )
        if clear_password_after:
            server.ssh_password_encrypted = None
            record_server_audit(
                session,
                server_id=server.id,
                user_id=user.id,
                action="server_password_clear",
                message="SSH password cleared after key deploy (wizard)",
            )
        session.add(server)
        session.commit()
        try:
            await run_in_threadpool(host_deps_svc.check_and_persist, session, server)
        except Exception:
            pass
        return RedirectResponse(
            wizard_path("connect", server.id, msg="key_deployed"),
            status_code=303,
        )
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_ssh_key_deployed",
        status="failed",
        message=result.message,
    )
    session.commit()
    return RedirectResponse(
        wizard_path(
            "connect",
            server.id,
            error="key_deploy_fail",
            detail=result.message[:180],
        ),
        status_code=303,
    )


@router.post("/new/connect/clear-password")
async def wizard_connect_clear_password(
    server_id: int = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Clear stored bootstrap SSH password (keys-only)."""
    server = _require_server(session, server_id)
    if not (server.ssh_password_encrypted or "").strip():
        return RedirectResponse(
            wizard_path("connect", server.id, msg="password_already_clear"),
            status_code=303,
        )
    server.ssh_password_encrypted = None
    session.add(server)
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_password_clear",
        message="SSH password cleared from wizard Connect step",
    )
    session.commit()
    return RedirectResponse(
        wizard_path("connect", server.id, msg="password_cleared"),
        status_code=303,
    )


@router.post("/new/features")
async def wizard_features_post(
    server_id: int = Form(...),
    backup_enabled: Optional[str] = Form(None),
    os_patch_enabled: Optional[str] = Form(None),
    container_patch_enabled: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Step 5 — feature toggles (reuse Server flags)."""
    server = _require_server(session, server_id)

    def _on(v: Optional[str]) -> bool:
        return (v or "").strip().lower() in ("1", "on", "true", "yes")

    server.backup_enabled = _on(backup_enabled)
    server.os_patch_enabled = _on(os_patch_enabled)
    server.container_patch_enabled = _on(container_patch_enabled)
    session.add(server)
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_wizard_features",
        details={
            "backup": server.backup_enabled,
            "os_patch": server.os_patch_enabled,
            "container": server.container_patch_enabled,
            "message": f"Wizard features for {server.name}",
        },
    )
    session.commit()
    return RedirectResponse(wizard_path("schedules", server.id), status_code=303)


@router.get("/new/exit")
async def wizard_save_exit(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Leave wizard; open server detail (partial setup OK)."""
    del user
    server = session.get(Server, int(server_id))
    if not server:
        return RedirectResponse("/servers", status_code=303)
    return RedirectResponse(
        server_redirect(server.id, msg="wizard_saved"),
        status_code=303,
    )

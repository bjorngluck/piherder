"""SSH access, key deploy, least-priv, host deps (extracted from servers.py)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from sqlmodel import Session, select

from .. import templates as templates_mod
from ..config import settings
from ..database import get_session
from ..models import Server, User
from ..security import encryption
from ..security.auth import get_current_user
from ..services import host_deps as host_deps_svc
from ..services import ssh as ssh_service
from ..services import ssh_onboarding
from ..services.server_audit import record_server_audit
from .server_common import server_redirect

router = APIRouter()
logger = logging.getLogger("piherder.servers")

def host_cleanup_script_for_server(server: Server) -> str:
    """Parameterized host cleanup shell for this server's SSH user / docker base."""
    _base = (server.docker_base_dir or "~/docker").strip()
    _compose_owner = "bjorn"
    _compose_tree = _base
    if _base.startswith("/home/"):
        parts = _base.strip("/").split("/")
        if len(parts) >= 2:
            _compose_owner = parts[1]
            _compose_tree = _base
    elif _base.startswith("~/"):
        _compose_tree = _base[2:] or "docker"
    return ssh_onboarding.build_piherder_user_cleanup_script(
        server.ssh_username or "piherder",
        remove_user=False,
        compose_owner=_compose_owner,
        compose_tree=_compose_tree if str(_compose_tree).startswith("/") else None,
    )


@router.get("/{server_id}/ssh/cleanup-script")
async def download_host_cleanup_script(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Download host-side piherder user cleanup script (.sh) for this server."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404, "Server not found")
    script = host_cleanup_script_for_server(server)
    user_slug = "".join(
        c if c.isalnum() or c in "-_" else "-"
        for c in (server.ssh_username or "piherder")
    ) or "piherder"
    host_slug = "".join(
        c if c.isalnum() or c in ".-_" else "-"
        for c in (server.hostname or server.name or "host")
    ) or "host"
    filename = f"cleanup-piherder-user-{user_slug}-{host_slug}.sh"
    return Response(
        content=script,
        media_type="text/x-shellscript; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/{server_id}/audit/ssh-key-viewed", response_class=JSONResponse)
async def audit_ssh_key_viewed(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_ssh_key_viewed",
        message=f"SSH public key viewed for {server.name}",
    )
    session.commit()
    return {"ok": True}


@router.post("/{server_id}/ssh/generate-key")
async def ssh_generate_key(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Create a keypair when the server was added password-only or has no key."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    if server.ssh_private_key_encrypted:
        return RedirectResponse(
            server_redirect(server_id, error="key_exists", detail="Server already has a private key. Use Rotate to change it."),
            status_code=303,
        )
    comment = f"piherder@{server.hostname or server.name}"
    pub, priv = ssh_service.generate_keypair(comment=comment)
    server.ssh_public_key = pub
    server.ssh_private_key_encrypted = encryption.encrypt_str(priv)
    session.add(server)
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_ssh_key_deployed",
        message="SSH keypair generated (not yet deployed to host)",
        details={"generated_only": True},
    )
    session.commit()
    return RedirectResponse(
        server_redirect(server_id, show_ssh_key="1", msg="key_generated"),
        status_code=303,
    )


@router.post("/{server_id}/ssh/test")
async def ssh_test_connection(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
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
        return RedirectResponse(server_redirect(server_id, msg="ssh_ok"), status_code=303)
    return RedirectResponse(
        server_redirect(server_id, error="ssh_fail", detail=result.message[:180]),
        status_code=303,
    )


@router.post("/{server_id}/host-deps/check")
async def check_host_dependencies(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Probe remote tools for enabled features; store snapshot on server."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        result = await run_in_threadpool(host_deps_svc.check_and_persist, session, server)
        overall = (result or {}).get("overall") or "unknown"
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_host_deps",
            status="success" if overall in ("ok", "warn") else "failed",
            message=f"Host dependencies: {overall}",
            details={
                "overall": overall,
                "checks": [
                    {
                        "id": c.get("id"),
                        "status": c.get("status"),
                        "required": c.get("required"),
                    }
                    for c in (result or {}).get("checks") or []
                ],
            },
        )
        session.commit()
        return RedirectResponse(
            server_redirect(server_id, msg="host_deps_ok", detail=overall),
            status_code=303,
        )
    except Exception as e:
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_host_deps",
            status="failed",
            message=str(e)[:200],
        )
        session.commit()
        return RedirectResponse(
            server_redirect(server_id, error="host_deps_fail", detail=str(e)[:180]),
            status_code=303,
        )


@router.post("/{server_id}/ssh/deploy-key")
async def ssh_deploy_key(
    server_id: int,
    ssh_password: str = Form(""),
    clear_password_after: Optional[str] = Form(None),
    store_password: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    password_override = ssh_password.strip() if ssh_password and ssh_password.strip() else None
    if store_password and password_override:
        server.ssh_password_encrypted = encryption.encrypt_str(password_override)
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_password_set",
            message="SSH password stored for deploy",
        )

    result = await run_in_threadpool(
        ssh_onboarding.deploy_public_key,
        server,
        password_override=password_override,
    )

    # Persist derived public key if we only had a placeholder
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
            },
        )
        if clear_password_after:
            server.ssh_password_encrypted = None
            record_server_audit(
                session,
                server_id=server.id,
                user_id=user.id,
                action="server_password_clear",
                message="SSH password cleared after key deploy",
            )
        session.add(server)
        session.commit()
        try:
            await run_in_threadpool(host_deps_svc.check_and_persist, session, server)
        except Exception:
            pass
        return RedirectResponse(server_redirect(server_id, msg="key_deployed"), status_code=303)

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
        server_redirect(server_id, error="key_deploy_fail", detail=result.message[:180]),
        status_code=303,
    )


@router.post("/{server_id}/ssh/rotate-key")
async def ssh_rotate_key(
    server_id: int,
    ssh_password: str = Form(""),
    confirm: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    if (confirm or "").strip().lower() != "rotate":
        return RedirectResponse(
            server_redirect(server_id, error="key_rotate_confirm"),
            status_code=303,
        )

    password_override = ssh_password.strip() if ssh_password and ssh_password.strip() else None
    result = await run_in_threadpool(
        ssh_onboarding.rotate_keypair,
        server,
        password_override=password_override,
    )

    if not result.ok:
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_ssh_key_rotated",
            status="failed",
            message=result.message,
        )
        session.commit()
        return RedirectResponse(
            server_redirect(server_id, error="key_rotate_fail", detail=result.message[:180]),
            status_code=303,
        )

    new_pub = result.details["new_public_key"]
    new_priv = result.details["new_private_key"]
    server.ssh_public_key = new_pub
    server.ssh_private_key_encrypted = encryption.encrypt_str(new_priv)
    session.add(server)
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_ssh_key_rotated",
        message=result.message,
        details={
            "removed_old": result.details.get("removed_old"),
            "installed": result.details.get("installed"),
        },
    )
    session.commit()
    return RedirectResponse(server_redirect(server_id, msg="key_rotated"), status_code=303)


def repoint_ssh_username(
    server: Server,
    new_user: str,
    *,
    clear_password: bool = True,
) -> tuple[str, str, bool]:
    """
    Switch Server.ssh_username and freeze ~/ docker paths under previous home.

    After least-priv re-point there is no separate "bjorn credentials" row —
    only one username + one keypair + optional password. Drop stored password
    (bootstrap leftover); keep the private key (now used as the new user).

    Returns (previous_username, new_username, password_cleared).
    """
    new_user = (new_user or "").strip()
    if not new_user:
        raise ValueError("Username required")
    prev = (server.ssh_username or "").strip()
    server.ssh_username = new_user
    fixed_base = ssh_onboarding.preserve_docker_base_after_user_switch(
        server.docker_base_dir or "~/docker",
        prev,
        new_user,
    )
    if fixed_base != (server.docker_base_dir or ""):
        server.docker_base_dir = fixed_base
    password_cleared = False
    if clear_password and server.ssh_password_encrypted:
        server.ssh_password_encrypted = None
        password_cleared = True
    return prev, new_user, password_cleared


@router.post("/{server_id}/ssh/set-username")
async def ssh_set_username(
    server_id: int,
    ssh_username: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Re-point PiHerder's SSH username only (no remote user creation).
    Use after you already ran the least-priv script / created piherder on the host.
    """
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        prev, new_user, pw_cleared = repoint_ssh_username(server, ssh_username, clear_password=True)
    except ValueError as e:
        return RedirectResponse(
            server_redirect(server_id, error="username_invalid", detail=str(e)[:120]),
            status_code=303,
        )
    if prev == new_user and not pw_cleared:
        return RedirectResponse(
            server_redirect(server_id, msg="username_unchanged"),
            status_code=303,
        )
    session.add(server)
    fields = ["ssh_username", "docker_base_dir"]
    if pw_cleared:
        fields.append("ssh_password_cleared")
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_password_clear",
            message="SSH password cleared after username re-point (key-only)",
        )
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_update",
        details={
            "fields": fields,
            "previous_username": prev,
            "new_username": new_user,
            "docker_base_dir": server.docker_base_dir,
            "password_cleared": pw_cleared,
            "message": f"SSH username re-pointed {prev} → {new_user}",
        },
    )
    session.commit()
    return RedirectResponse(
        server_redirect(server_id, msg="username_set", detail=new_user),
        status_code=303,
    )


@router.post("/{server_id}/ssh/provision-user")
async def ssh_provision_user(
    server_id: int,
    new_username: str = Form("piherder"),
    ssh_password: str = Form(""),
    include_backup: Optional[str] = Form("1"),
    include_docker: Optional[str] = Form(None),
    include_os_patch: Optional[str] = Form(None),
    run_on_host: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Generate least-priv script (always available via detail page).
    When run_on_host is set, execute on remote (Debian / Pi OS / Ubuntu only).
    """
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    uname = (new_username or "piherder").strip()
    backup = bool(include_backup)
    docker = bool(include_docker)
    os_patch = bool(include_os_patch)

    if not run_on_host:
        # Copy-only path: just flash that script is on page (client-side preview).
        return RedirectResponse(
            server_redirect(server_id, msg="provision_script"),
            status_code=303,
        )

    password_override = ssh_password.strip() if ssh_password and ssh_password.strip() else None
    result = await run_in_threadpool(
        ssh_onboarding.provision_least_priv_user,
        server,
        uname,
        backup=backup,
        docker=docker,
        os_patch=os_patch,
        password_override=password_override,
    )

    if not result.ok:
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_ssh_user_provisioned",
            status="failed",
            message=result.message,
            details={
                "os": (result.details.get("os") or {}).get("name"),
                "new_username": uname,
            },
        )
        session.commit()
        return RedirectResponse(
            server_redirect(server_id, error="provision_fail", detail=result.message[:180]),
            status_code=303,
        )

    new_user = result.details.get("new_username") or uname
    prev, new_user, pw_cleared = repoint_ssh_username(server, new_user, clear_password=True)
    session.add(server)
    # expire so next request cannot serve a stale identity-map value
    session.commit()
    session.refresh(server)
    if pw_cleared:
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_password_clear",
            message="SSH password cleared after least-priv re-point (key-only as new user)",
        )
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_ssh_user_provisioned",
        message=result.message,
        details={
            "new_username": new_user,
            "previous_username": prev,
            "docker_base_dir": server.docker_base_dir,
            "password_cleared": pw_cleared,
            "docker": docker,
            "os_patch": os_patch,
            "backup": backup,
        },
    )
    session.commit()
    try:
        await run_in_threadpool(host_deps_svc.check_and_persist, session, server)
    except Exception:
        pass
    return RedirectResponse(
        server_redirect(server_id, msg="user_provisioned", detail=new_user),
        status_code=303,
    )


# (backup progress + logs stream moved to server_backups.py)

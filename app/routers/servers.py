from fastapi import APIRouter, Depends, Form, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response, StreamingResponse
from sqlmodel import Session, select
from sqlalchemy import func
import json
from typing import Optional, List
from starlette.concurrency import run_in_threadpool
from ..database import get_session, engine
from ..models import Server, AuditLog, Job
from datetime import datetime
from ..security import encryption
import asyncio
from ..services import ssh as ssh_service
from ..services import ssh_onboarding
from ..services import jobs as job_service
from ..services import backup as backup_svc
from ..services import diagnostics as diag_svc
from ..services import host_deps as host_deps_svc
from ..services import os_patching
from ..services import server_lifecycle
from ..services.app_settings import format_datetime_in_app_tz
from ..services.server_audit import record_server_audit
from urllib.parse import quote
from .. import templates as templates_mod
from ..security.auth import get_current_user
from ..models import User
from ..config import settings
try:
    import pycron
except ImportError:
    pycron = None
import time
import logging

router = APIRouter()

# Mount sub-routers (keep paths unchanged)
from .server_docker import router as docker_router
from .server_backups import router as backups_router
from .server_services import router as services_router
router.include_router(docker_router, prefix="")
router.include_router(backups_router, prefix="")
router.include_router(services_router, prefix="")
logger = logging.getLogger("piherder.servers")


def _server_redirect(server_id: int, **params: str) -> str:
    url = f"/servers/{server_id}"
    if params:
        qs = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items() if v is not None)
        if qs:
            url = f"{url}?{qs}"
    return url


@router.get("", response_class=HTMLResponse)
async def list_servers(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    filter: str = "",
):
    """Extremely lean Servers list - pure DB read.
    last_backup_at is populated by the worker on success.
    Optional filter: attention | os | reboot | containers
    """
    start = time.time()
    filt = (filter or "").strip().lower()
    if filt not in ("", "all", "attention", "os", "reboot", "containers"):
        filt = "all"
    if filt == "":
        filt = "all"

    try:
        rows = list(session.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
    except Exception:
        rows = list(session.exec(select(Server).order_by(Server.name)).all())

    running_backup_ids = set(
        session.exec(
            select(Job.server_id).where(
                Job.job_type == "backup",
                Job.status.in_(["pending", "running"]),
            )
        ).all()
    )

    def _has_os(s: Server) -> bool:
        return s.os_updates_count is not None and s.os_updates_count > 0

    def _has_cont(s: Server) -> bool:
        return s.container_updates_count is not None and s.container_updates_count > 0

    def _needs_attention(s: Server) -> bool:
        return bool(s.reboot_pending or _has_os(s) or _has_cont(s))

    # Counts for filter chips (full fleet, before filter)
    filter_counts = {
        "all": len(rows),
        "attention": sum(1 for s in rows if _needs_attention(s)),
        "os": sum(1 for s in rows if _has_os(s)),
        "reboot": sum(1 for s in rows if s.reboot_pending),
        "containers": sum(1 for s in rows if _has_cont(s)),
    }

    if filt == "attention":
        rows = [s for s in rows if _needs_attention(s)]
    elif filt == "os":
        rows = [s for s in rows if _has_os(s)]
    elif filt == "reboot":
        rows = [s for s in rows if s.reboot_pending]
    elif filt == "containers":
        rows = [s for s in rows if _has_cont(s)]

    servers = []
    for row in rows:
        d = row.model_dump(exclude={"audit_logs", "jobs"})
        if row.last_backup_at:
            d["last_backup"] = row.last_backup_at
            d["last_backup_str"] = format_datetime_in_app_tz(row.last_backup_at)
        d["backup_running"] = row.id in running_backup_ids
        d["needs_attention"] = _needs_attention(row)
        d["has_os_updates"] = _has_os(row)
        d["has_container_updates"] = _has_cont(row)
        # Phased-only (Ubuntu) — visibility, not attention
        phased = 0
        total_up = None
        if row.os_updates_summary:
            try:
                meta = json.loads(row.os_updates_summary)
                if isinstance(meta, dict):
                    phased = int(meta.get("phased_count") or 0)
                    total_up = meta.get("total_upgradable")
            except Exception:
                pass
        d["os_phased_count"] = phased
        d["os_total_upgradable"] = total_up
        servers.append(d)

    # Kuma SSH reachability chips (from integration bindings cache)
    try:
        from ..services.integrations import registry as integ_reg
        from ..services.integrations import uptime_kuma as kuma_svc

        by_server = integ_reg.bindings_by_server(session, role=integ_reg.ROLE_SSH)
        for d in servers:
            binds = by_server.get(d.get("id")) or []
            if not binds:
                d["kuma_ssh"] = None
                continue
            # Prefer worst state if multiple integrations
            order = {"down": 0, "pending": 1, "maintenance": 2, "unknown": 3, "up": 4}
            best = sorted(binds, key=lambda b: order.get((b.last_state or "unknown"), 9))[0]
            integ = integ_reg.get_integration(session, best.integration_id)
            open_url = (
                integ_reg.binding_open_url(integ, best) if integ else ""
            )
            d["kuma_ssh"] = {
                "state": best.last_state or "unknown",
                "label": best.external_label or best.external_id,
                "message": best.last_message or "",
                "integration_id": best.integration_id,
                "open_url": open_url,
            }
    except Exception as e:
        logger.debug("kuma chips skip: %s", e)

    total = time.time() - start
    if total > 0.3:
        logger.warning(f"[list_servers] Total render took {total:.2f}s for {len(servers)} server(s)")
    else:
        logger.debug(f"[list_servers] Total render took {total:.2f}s")

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_list.html",
        context={
            "title": "Servers",
            "servers": servers,
            "user": user,
            "lean_page": True,
            "filter": filt,
            "filter_counts": filter_counts,
        },
    )


@router.post("/reorder")
async def reorder_servers(
    order: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Save full list order from drag-and-drop (comma-separated server ids)."""
    raw = [p.strip() for p in (order or "").split(",") if p.strip()]
    ids: list[int] = []
    for p in raw:
        try:
            ids.append(int(p))
        except ValueError:
            continue
    if not ids:
        return RedirectResponse("/servers?error=reorder_empty", status_code=303)

    # Only known servers; preserve any missing at the end
    try:
        all_servers = list(session.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
    except Exception:
        all_servers = list(session.exec(select(Server).order_by(Server.name)).all())
    by_id = {s.id: s for s in all_servers if s.id is not None}
    ordered: list[Server] = []
    seen = set()
    for sid in ids:
        s = by_id.get(sid)
        if s and sid not in seen:
            ordered.append(s)
            seen.add(sid)
    for s in all_servers:
        if s.id not in seen:
            ordered.append(s)

    for i, s in enumerate(ordered):
        s.sort_order = i * 10
        session.add(s)
    record_server_audit(
        session,
        server_id=None,
        user_id=user.id,
        action="server_reorder",
        details={"message": f"Reordered {len(ordered)} server(s)", "order": [s.id for s in ordered]},
    )
    session.commit()
    return RedirectResponse("/servers?reordered=1", status_code=303)


@router.post("/{server_id}/move/{direction}")
async def move_server(
    server_id: int,
    direction: str,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    """Legacy single-step move (kept for compatibility). Prefer /servers/reorder."""
    if direction not in ("up", "down"):
        raise HTTPException(400)
    try:
        servers = list(session.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
    except Exception:
        servers = list(session.exec(select(Server).order_by(Server.name)).all())
    try:
        idx = next(i for i, s in enumerate(servers) if s.id == server_id)
    except StopIteration:
        raise HTTPException(404)
    if direction == "up" and idx > 0:
        servers[idx], servers[idx-1] = servers[idx-1], servers[idx]
    elif direction == "down" and idx < len(servers) - 1:
        servers[idx], servers[idx+1] = servers[idx+1], servers[idx]
    for i, s in enumerate(servers):
        s.sort_order = i * 10
        session.add(s)
    record_server_audit(
        session,
        server_id=server_id,
        user_id=user.id,
        action="server_move",
        details={"direction": direction, "message": f"Moved {direction} in server list"},
    )
    session.commit()
    return RedirectResponse("/servers", status_code=303)


@router.get("/add", response_class=HTMLResponse)
async def add_server_form(request: Request, user: User = Depends(get_current_user)):
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="add_server.html",
        context={"title": "Add Server", "user": user}
    )


@router.post("/add")
async def add_server(
    name: str = Form(...),
    hostname: str = Form(...),
    ssh_username: str = Form("bjorn"),
    ssh_port: int = Form(22),
    key_mode: str = Form("generate"),
    private_key: str = Form(""),
    ssh_password: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    priv_enc = None
    pub = None
    pw_enc = None
    host = hostname.strip()
    sname = name.strip()
    comment = f"piherder@{host or sname or 'server'}"

    if key_mode == "generate":
        pub, priv = ssh_service.generate_keypair(comment=comment)
        priv_enc = encryption.encrypt_str(priv)
        # Optional one-time password for Deploy key after create
        if ssh_password and ssh_password.strip():
            pw_enc = encryption.encrypt_str(ssh_password.strip())
    elif key_mode == "password":
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

    current_max = session.scalar(select(func.max(Server.sort_order)))
    next_sort = int(current_max or 0) + 10
    server = Server(
        name=sname,
        hostname=host,
        ssh_username=ssh_username,
        ssh_port=ssh_port,
        ssh_private_key_encrypted=priv_enc,
        ssh_public_key=pub,
        ssh_password_encrypted=pw_enc,
        sort_order=next_sort,
        backup_enabled=True,
    )
    session.add(server)
    session.commit()
    session.refresh(server)

    auth_method = {"generate": "generated_key", "upload": "uploaded_key", "password": "password_auth"}.get(
        key_mode, key_mode
    )
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_create",
        details={
            "name": server.name,
            "hostname": server.hostname,
            "ssh_username": server.ssh_username,
            "auth_method": auth_method,
            "message": f"Server {server.name} added",
        },
    )
    if pw_enc:
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_password_set",
            message="SSH password set on server create",
        )
    session.commit()

    if key_mode == "generate" or (key_mode == "upload" and ssh_onboarding.is_real_public_key(pub)):
        return RedirectResponse(
            _server_redirect(server.id, show_ssh_key="1", msg="server_added"),
            status_code=303,
        )
    return RedirectResponse(_server_redirect(server.id, msg="server_added"), status_code=303)


@router.get("/{server_id}", response_class=HTMLResponse)
async def server_detail(
    server_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    show_ssh_key = request.query_params.get("show_ssh_key") == "1"
    edit_mode = request.query_params.get("edit") == "1"
    flash_msg = request.query_params.get("msg") or ""
    flash_error = request.query_params.get("error") or ""
    flash_detail = request.query_params.get("detail") or ""

    server_dict = server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"})
    # Template helpers for SSH onboarding UI (not persisted fields)
    server_dict["has_ssh_key"] = bool(server.ssh_private_key_encrypted)
    server_dict["has_ssh_password"] = bool(server.ssh_password_encrypted)
    server_dict["has_real_public_key"] = ssh_onboarding.is_real_public_key(server.ssh_public_key)

    reboot_initiated = request.query_params.get("rebooted") == "1"

    key_install_script = ""
    least_priv_script = ""
    compose_acl_script = ""
    host_cleanup_script = ""
    # Option B ACL: share compose tree with least-priv user (guess owner from absolute path)
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
    compose_acl_script = ssh_onboarding.build_compose_tree_acl_script(
        service_user=server.ssh_username or "piherder",
        compose_owner=_compose_owner,
        compose_dir=_compose_tree if _compose_tree.startswith("/") else (_compose_tree or "docker"),
    )
    host_cleanup_script = _host_cleanup_script_for_server(server)
    if ssh_onboarding.is_real_public_key(server.ssh_public_key):
        key_install_script = ssh_onboarding.build_key_install_script(
            server.ssh_public_key, username=server.ssh_username
        )
        least_priv_script = ssh_onboarding.build_least_priv_script(
            "piherder",
            server.ssh_public_key,
            backup=True,
            docker=bool(server.container_patch_enabled),
            os_patch=bool(server.os_patch_enabled),
        )
    elif server.ssh_private_key_encrypted:
        try:
            derived = ssh_onboarding.public_key_from_private(
                ssh_service.get_private_key_plain(server),
                comment=f"piherder@{server.hostname or server.name}",
            )
            key_install_script = ssh_onboarding.build_key_install_script(
                derived, username=server.ssh_username
            )
            least_priv_script = ssh_onboarding.build_least_priv_script(
                "piherder",
                derived,
                backup=True,
                docker=bool(server.container_patch_enabled),
                os_patch=bool(server.os_patch_enabled),
            )
        except Exception:
            pass

    backup_profiles = []
    overall_last_backup = None
    last_backup_status = None
    recent_backups = []
    recent_jobs = []
    active_jobs = []
    global_backup_defaults = {}
    current_sources = []
    diagnostics = {"error": "n/a"}
    current_backup_job = None   # DB-backed status (worker writes here)

    try:
        backup_profiles = backup_svc.get_backup_profiles_db(server)
        overall_last_backup = server.last_backup_at

        # Latest backup Job from DB (source of truth for running state)
        current_backup_job = session.exec(
            select(Job)
            .where(Job.server_id == server.id, Job.job_type == "backup")
            .order_by(Job.created_at.desc())
            .limit(1)
        ).first()

        last_backup_log = session.exec(
            select(AuditLog)
            .where(AuditLog.server_id == server.id, AuditLog.action == "backup")
            .order_by(AuditLog.started_at.desc())
            .limit(1)
        ).first()
        last_backup_status = (
            backup_svc.effective_backup_status(
                last_backup_log.status, last_backup_log.output_snippet
            )
            if last_backup_log
            else None
        )

        recent_backups = session.exec(
            select(AuditLog)
            .where(AuditLog.server_id == server.id, AuditLog.action == "backup")
            .order_by(AuditLog.started_at.desc())
            .limit(10)
        ).all()

        recent_jobs = job_service.list_jobs_for_server(session, server.id, limit=20)
        active_jobs = job_service.list_jobs_for_server(
            session, server.id, limit=10, active_only=True
        )

        for log in recent_backups:
            object.__setattr__(log, 'parsed', None)
            fj = (log.output_snippet or '').strip() or '{}'
            object.__setattr__(log, 'full_json', fj)
            if log.output_snippet:
                try:
                    data = json.loads(log.output_snippet)
                    results = data.get("results", [])
                    total_bytes = sum(r.get("size_bytes", 0) for r in results)
                    object.__setattr__(log, 'parsed', {
                        "sources": len(results),
                        "success_count": sum(1 for r in results if backup_svc.backup_source_ok(r)),
                        "total_size": total_bytes,
                        "total_size_human": backup_svc.human_size(total_bytes),
                    })
                    object.__setattr__(log, 'full_json', json.dumps(data, indent=2))
                except Exception:
                    pass

        global_backup_defaults = backup_svc.global_backup_defaults_from_server(server)
        current_sources = [p.get("source") for p in backup_profiles]

        # Skip SSH diagnostics on page load — backup pages must stay fast.
    except Exception:
        diagnostics = {"error": "Could not load server details"}

    os_phased_count = 0
    os_total_upgradable = None
    if server.os_updates_summary:
        try:
            _om = json.loads(server.os_updates_summary)
            if isinstance(_om, dict):
                os_phased_count = int(_om.get("phased_count") or 0)
                os_total_upgradable = _om.get("total_upgradable")
        except Exception:
            pass

    # Prefetch docker inventory in background (non-blocking) when Docker feature is on
    inventory_meta = {}
    try:
        from ..services import docker_inventory as inventory_svc

        if server.container_patch_enabled:
            inventory_meta = inventory_svc.inventory_meta(server)
            if inventory_svc.is_stale(server) or inventory_svc.is_refresh_stuck(server):
                inventory_svc.request_refresh(
                    background_tasks,
                    server.id,
                    force=False,
                    server=server,
                    session=session,
                )
                session.refresh(server)
                inventory_meta = inventory_svc.inventory_meta(server)
    except Exception:
        inventory_meta = {}

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_detail.html",
        context={
            "title": server.name,
            "server": server_dict,
            "inventory_meta": inventory_meta,
            "os_phased_count": os_phased_count,
            "os_total_upgradable": os_total_upgradable,
            "backup_profiles": backup_profiles,
            "overall_last_backup": overall_last_backup,
            "last_backup_status": last_backup_status,
            "recent_backups": recent_backups,
            "recent_jobs": recent_jobs,
            "active_jobs": active_jobs,
            "current_backup_job": current_backup_job,
            "running_backup_job": job_service.get_running_backup_job(session, server.id),
            "full_backup_job": job_service.get_active_job_for_source(session, server.id, None),
            "active_backup_jobs": job_service.get_active_backup_jobs(session, server.id),
            "backup_active": bool(job_service.get_active_backup_jobs(session, server.id)),
            "user": user,
            "settings": settings,
            "global_backup_defaults": global_backup_defaults,
            "current_sources": current_sources,
            "diagnostics": diagnostics,
            "reboot_initiated": reboot_initiated,
            "show_ssh_key": show_ssh_key,
            "edit_mode": edit_mode,
            "flash_msg": flash_msg,
            "flash_error": flash_error,
            "flash_detail": flash_detail,
            "key_install_script": key_install_script,
            "least_priv_script": least_priv_script,
            "compose_acl_script": compose_acl_script,
            "host_cleanup_script": host_cleanup_script,
            "docker_base_expanded": ssh_service.docker_base_expanded(server),
            "haos_guidance": ssh_onboarding.HAOS_GUIDANCE,
            "host_deps": host_deps_svc.parse_host_deps(server),
            "kuma_ssh": _kuma_ssh_for_server(session, server.id),
            "kuma_host_services": _kuma_host_services_for_server(session, server.id),
            "kuma_docker_service_count": _kuma_docker_service_count(session, server.id),
            "grafana_dashboards": _grafana_dashboards_for_server(session, server.id),
            "lean_page": True,
        }
    )


def _kuma_ssh_for_server(session: Session, server_id: int) -> Optional[dict]:
    """Best SSH binding snapshot for server detail monitoring card."""
    try:
        from ..services.integrations import registry as integ_reg

        binds = integ_reg.list_bindings(
            session, server_id=server_id, role=integ_reg.ROLE_SSH
        )
        if not binds:
            return None
        order = {"down": 0, "pending": 1, "maintenance": 2, "unknown": 3, "up": 4}
        best = sorted(binds, key=lambda b: order.get((b.last_state or "unknown"), 9))[0]
        integ = integ_reg.get_integration(session, best.integration_id)
        open_url = integ_reg.binding_open_url(integ, best) if integ else ""
        return {
            "state": best.last_state or "unknown",
            "label": best.external_label or best.external_id,
            "message": best.last_message or "",
            "integration_id": best.integration_id,
            "checked_at": best.last_checked_at,
            "open_url": open_url,
        }
    except Exception:
        return None


def _kuma_host_services_for_server(session: Session, server_id: int) -> list[dict]:
    """Host-scoped HTTP/TLS services (not Docker) — e.g. Home Assistant on HAOS."""
    try:
        from ..services.integrations import registry as integ_reg

        return integ_reg.host_service_chips_for_server(session, server_id)
    except Exception:
        return []


def _grafana_dashboards_for_server(session: Session, server_id: int) -> list:
    """Grafana dashboard deep-link chips for server detail."""
    try:
        from ..services.integrations import registry as integ_reg

        return integ_reg.grafana_chips_for_server(session, server_id)
    except Exception:
        return []


def _kuma_docker_service_count(session: Session, server_id: int) -> int:
    try:
        from ..services.integrations import registry as integ_reg

        return sum(
            1
            for b in integ_reg.service_bindings_for_server(session, server_id)
            if integ_reg.is_docker_service_binding(b)
        )
    except Exception:
        return 0


def _host_cleanup_script_for_server(server: Server) -> str:
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
    script = _host_cleanup_script_for_server(server)
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


@router.post("/{server_id}/delete")
async def delete_server(
    server_id: int,
    confirm_name: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Remove server from PiHerder fleet only — does not change the remote host."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404, "Server not found")
    try:
        snap = server_lifecycle.delete_server_from_fleet(
            session,
            server,
            confirm_name=confirm_name,
            user_id=user.id,
        )
    except server_lifecycle.ServerDeleteError as e:
        return RedirectResponse(
            _server_redirect(server_id, error=e.code, detail=e.message),
            status_code=303,
        )
    name = quote(str(snap.get("name") or ""), safe="")
    host = quote(str(snap.get("hostname") or ""), safe="")
    return RedirectResponse(
        f"/servers?msg=server_deleted&name={name}&hostname={host}",
        status_code=303,
    )


# (backup routes moved to server_backups.py sub-router)


# (run_backup / stop_backup moved to server_backups.py)


@router.post("/{server_id}/reboot")
async def reboot_server(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Send reboot to the host via SSH (passwordless sudo full path).

    Least-priv sudoers allow ``/usr/sbin/reboot`` — bare ``sudo reboot`` is not
    always matched. Clear local reboot_pending optimistically after the command
    is accepted so the UI does not look stuck.
    """
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    success = False
    details = "Reboot initiated"
    # Full paths that match least-priv onboard sudoers / common layouts
    reboot_cmds = (
        "sudo -n /usr/sbin/reboot",
        "sudo -n /sbin/reboot",
        "sudo -n /usr/bin/systemctl reboot",
    )
    try:
        client = ssh_service.get_ssh_client(server)
        last_err = ""
        try:
            for cmd in reboot_cmds:
                try:
                    # Non-blocking start; reboot often kills the channel mid-flight
                    _stdin, stdout, stderr = client.exec_command(cmd, timeout=5)
                    # Brief wait for immediate "not allowed" / missing binary
                    import time as _time

                    _time.sleep(0.4)
                    if stdout.channel.exit_status_ready():
                        code = stdout.channel.recv_exit_status()
                        err = (stderr.read() or b"").decode(errors="replace")[:200]
                        out = (stdout.read() or b"").decode(errors="replace")[:200]
                        if code == 0:
                            success = True
                            details = f"Reboot accepted ({cmd})"
                            break
                        last_err = (err or out or f"exit {code}").strip()
                        # try next path
                        continue
                    # No exit yet — host is likely going down (success)
                    success = True
                    details = f"Reboot command sent ({cmd})"
                    break
                except Exception as e:
                    # Connection dropped after reboot is normal
                    msg = str(e).lower()
                    if any(
                        x in msg
                        for x in ("eof", "reset", "closed", "timeout", "timed out")
                    ):
                        success = True
                        details = f"Reboot sent (connection closed: {cmd})"
                        break
                    last_err = str(e)[:200]
            if not success and last_err:
                details = f"Reboot command failed: {last_err}"
        finally:
            try:
                client.close()
            except Exception:
                pass
    except Exception as e:
        details = f"Reboot command failed to send: {e}"

    if success:
        # Optimistic clear — host will re-set on next OS check if still required
        server.reboot_pending = False
        session.add(server)
        try:
            from ..services import notifications as notif_svc

            notif_svc.resolve_by_fingerprint(
                session, f"reboot_pending:server:{server_id}"
            )
        except Exception:
            pass

    try:
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="reboot",
            status="success" if success else "failed",
            message=details,
        )
        session.commit()
    except Exception:
        pass

    if success:
        return RedirectResponse(f"/servers/{server_id}?rebooted=1", status_code=303)
    return RedirectResponse(
        f"/servers/{server_id}?error=reboot_fail&detail={quote(details[:180])}",
        status_code=303,
    )


@router.post("/{server_id}/update")
async def update_server(
    server_id: int,
    name: str = Form(...),
    hostname: str = Form(...),
    ssh_username: str = Form(...),
    ssh_port: int = Form(22),
    ssh_password: str = Form(""),
    clear_password: Optional[str] = Form(None),
    docker_base_dir: str = Form("~/docker"),
    backup_enabled: bool = Form(False),
    container_patch_enabled: bool = Form(False),
    os_patch_enabled: bool = Form(False),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404, "Server not found")

    changed: list[str] = []
    new_name = name.strip()
    new_host = hostname.strip()
    new_user = ssh_username.strip()
    new_docker_base = (docker_base_dir or "~/docker").strip() or "~/docker"
    if server.name != new_name:
        changed.append("name")
    if server.hostname != new_host:
        changed.append("hostname")
    if server.ssh_username != new_user:
        changed.append("ssh_username")
    if server.ssh_port != ssh_port:
        changed.append("ssh_port")
    if (server.docker_base_dir or "") != new_docker_base:
        changed.append("docker_base_dir")
    if server.backup_enabled != backup_enabled:
        changed.append("backup_enabled")
    if server.container_patch_enabled != container_patch_enabled:
        changed.append("container_patch_enabled")
    if server.os_patch_enabled != os_patch_enabled:
        changed.append("os_patch_enabled")

    server.name = new_name
    server.hostname = new_host
    server.ssh_username = new_user
    server.ssh_port = ssh_port
    server.docker_base_dir = new_docker_base

    if clear_password:
        server.ssh_password_encrypted = None
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_password_clear",
            message="SSH password cleared",
        )
    elif ssh_password and ssh_password.strip():
        try:
            server.ssh_password_encrypted = encryption.encrypt_str(ssh_password.strip())
        except Exception as e:
            raise HTTPException(500, f"Failed to encrypt password: {e}")
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_password_set",
            message="SSH password updated",
        )

    server.backup_enabled = backup_enabled
    server.container_patch_enabled = container_patch_enabled
    server.os_patch_enabled = os_patch_enabled

    if changed:
        record_server_audit(
            session,
            server_id=server.id,
            user_id=user.id,
            action="server_update",
            details={"fields": changed, "message": f"Updated {', '.join(changed)}"},
        )

    session.add(server)
    session.commit()

    return RedirectResponse(_server_redirect(server_id), status_code=303)


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
            _server_redirect(server_id, error="key_exists", detail="Server already has a private key. Use Rotate to change it."),
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
        _server_redirect(server_id, show_ssh_key="1", msg="key_generated"),
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
        return RedirectResponse(_server_redirect(server_id, msg="ssh_ok"), status_code=303)
    return RedirectResponse(
        _server_redirect(server_id, error="ssh_fail", detail=result.message[:180]),
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
            _server_redirect(server_id, msg="host_deps_ok", detail=overall),
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
            _server_redirect(server_id, error="host_deps_fail", detail=str(e)[:180]),
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
        return RedirectResponse(_server_redirect(server_id, msg="key_deployed"), status_code=303)

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
        _server_redirect(server_id, error="key_deploy_fail", detail=result.message[:180]),
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
            _server_redirect(server_id, error="key_rotate_confirm"),
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
            _server_redirect(server_id, error="key_rotate_fail", detail=result.message[:180]),
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
    return RedirectResponse(_server_redirect(server_id, msg="key_rotated"), status_code=303)


def _repoint_ssh_username(
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
        prev, new_user, pw_cleared = _repoint_ssh_username(server, ssh_username, clear_password=True)
    except ValueError as e:
        return RedirectResponse(
            _server_redirect(server_id, error="username_invalid", detail=str(e)[:120]),
            status_code=303,
        )
    if prev == new_user and not pw_cleared:
        return RedirectResponse(
            _server_redirect(server_id, msg="username_unchanged"),
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
        _server_redirect(server_id, msg="username_set", detail=new_user),
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
            _server_redirect(server_id, msg="provision_script"),
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
            _server_redirect(server_id, error="provision_fail", detail=result.message[:180]),
            status_code=303,
        )

    new_user = result.details.get("new_username") or uname
    prev, new_user, pw_cleared = _repoint_ssh_username(server, new_user, clear_password=True)
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
        _server_redirect(server_id, msg="user_provisioned", detail=new_user),
        status_code=303,
    )


# (backup progress + logs stream moved to server_backups.py)

@router.get("/{server_id}/os-patch-progress", response_class=JSONResponse)
async def get_os_patch_progress(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    prog = os_patching.get_os_patch_progress(server.hostname)
    return {
        "current": prog.get("current"),
        "log_lines": prog.get("log_lines", []),
        "done": bool(prog.get("done")),
        "finished_ok": prog.get("finished_ok"),
        "hostname": server.hostname,
    }


@router.get("/{server_id}/os-patch/logs/stream")
async def stream_os_patch_logs(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    async def event_generator():
        last_index = 0
        while True:
            prog = os_patching.get_os_patch_progress(server.hostname)
            lines = prog.get("log_lines", [])
            for line in lines[last_index:]:
                yield f"data: {line}\n\n"
            last_index = len(lines)
            await asyncio.sleep(2.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/{server_id}/diagnostics", response_class=JSONResponse)
async def get_server_diagnostics(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    force: bool = False,
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        data = await run_in_threadpool(diag_svc.run_diagnostics, server, bool(force))
        return data
    except Exception as e:
        return {"error": str(e)[:200], "hostname": server.hostname
    }


@router.post("/{server_id}/run/container_patch")
async def run_container_patch(
    request: Request,
    server_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    job = job_service.create_job_and_run(
        background_tasks, session, server, "container_patch", user_id=user.id
    )
    if request.headers.get("X-PiHerder-Async") == "1":
        return JSONResponse({"job_id": job.id, "status": job.status, "job_type": "container_patch"})
    return RedirectResponse(f"/servers/{server_id}", status_code=303)


@router.post("/{server_id}/run/os_patch")
async def run_os_patch(
    request: Request,
    server_id: int,
    background_tasks: BackgroundTasks,
    steps: list[str] = Form([]),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    # Normalize: allowed steps only; upgrade XOR full-upgrade; default if empty
    steps = os_patching.normalize_os_patch_steps(steps or None)
    if not steps:
        steps = ["update", "upgrade", "autoremove"]
    job = job_service.create_job_and_run(
        background_tasks, session, server, "os_patch", user_id=user.id, os_steps=steps
    )
    if request.headers.get("X-PiHerder-Async") == "1":
        return JSONResponse({"job_id": job.id, "status": job.status, "job_type": "os_patch"})
    return RedirectResponse(_server_redirect(server_id), status_code=303)


@router.get("/{server_id}/jobs", response_class=JSONResponse)
async def list_server_jobs(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    limit: int = 25,
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    active_only: bool = False,
):
    """Queue + history for a server (running/pending and recent finished)."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    jobs = job_service.list_jobs_for_server(
        session,
        server_id,
        limit=limit,
        status=status,
        job_type=job_type,
        active_only=active_only,
    )
    return {
        "server_id": server_id,
        "jobs": [job_service.job_public_dict(j) for j in jobs],
        "active_count": sum(1 for j in jobs if j.status in ("pending", "running")),
    }


@router.get("/{server_id}/jobs/{job_id}", response_class=JSONResponse)
async def get_server_job_status(
    server_id: int,
    job_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Poll endpoint for holding modal: status + log_lines + summary."""
    job = session.get(Job, job_id)
    if not job or job.server_id != server_id:
        raise HTTPException(404)
    server = session.get(Server, server_id)
    details: dict = {}
    if job.details:
        try:
            parsed = json.loads(job.details)
            if isinstance(parsed, dict):
                details = parsed
        except Exception:
            details = {"raw": (job.details or "")[:400]}
    log_lines = list(details.get("log_lines") or [])
    current = details.get("current")
    # Merge live in-memory tails so JobHold shows progress before DB flush catches up
    if server and job.status in ("pending", "running"):
        host = server.hostname or ""
        if job.job_type == "container_patch":
            try:
                from ..services import container_patching
                prog = container_patching.get_container_patch_progress(host)
                if prog.get("log_lines"):
                    log_lines = prog["log_lines"]
                if prog.get("current"):
                    current = prog["current"]
            except Exception:
                pass
        elif job.job_type == "os_patch":
            try:
                prog = os_patching.get_os_patch_progress(host)
                if prog.get("log_lines"):
                    log_lines = prog["log_lines"]
                if prog.get("current"):
                    current = prog["current"]
            except Exception:
                pass
    done = job.status in ("success", "failed")
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "done": done or bool(details.get("done")),
        "current": current,
        "log_lines": log_lines,
        "summary": details.get("summary") or "",
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _validate_cron(cron: str | None) -> str | None:
    if not cron:
        return None
    cron = cron.strip() or None
    if not cron:
        return None
    if pycron:
        try:
            parts = cron.split()
            if len(parts) != 5:
                raise ValueError("not 5 fields")
            pycron.is_now(cron, datetime.now())
        except Exception:
            raise HTTPException(400, "Invalid cron expression. Example: '0 6 * * *'")
    return cron


def _sync_server_schedules(server: Server):
    try:
        from ..main import scheduler, HAS_SCHEDULER
        from ..services.scheduler import sync_server_cron_jobs
        sync_server_cron_jobs(scheduler, HAS_SCHEDULER, server)
    except Exception as e:
        logger.warning(f"Could not sync schedules for server {server.id}: {e}")


@router.post("/{server_id}/check/os-updates")
async def check_os_updates_now(
    request: Request,
    server_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    # Queue on shared pool (same as scheduled checks)
    job = job_service.enqueue_os_update_check(server.id, user_id=user.id)
    if not job:
        raise HTTPException(500, "Could not queue OS update check")
    if request.headers.get("X-PiHerder-Async") == "1":
        return JSONResponse({"job_id": job.id, "status": job.status, "job_type": "os_update_check"})
    return RedirectResponse(f"/servers/{server_id}?os_check=1", status_code=303)


@router.post("/{server_id}/check/container-updates")
async def check_container_updates_now(
    request: Request,
    server_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    job = job_service.enqueue_container_update_check(server.id, user_id=user.id)
    if not job:
        raise HTTPException(500, "Could not queue container update check")
    if request.headers.get("X-PiHerder-Async") == "1":
        return JSONResponse({"job_id": job.id, "status": job.status, "job_type": "container_update_check"})
    return RedirectResponse(f"/servers/{server_id}?container_check=1", status_code=303)


@router.post("/{server_id}/schedule/os-check")
async def save_os_check_schedule(
    server_id: int,
    os_check_enabled: Optional[str] = Form(None),
    os_check_schedule: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    cron = _validate_cron(os_check_schedule)
    server.os_check_schedule = cron
    server.os_check_enabled = os_check_enabled in ("1", "on", "true") and bool(cron)
    session.add(server)
    session.commit()
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_os_check_schedule",
        details={"enabled": server.os_check_enabled, "cron": cron},
    )
    session.commit()
    _sync_server_schedules(server)
    return RedirectResponse(_server_redirect(server_id), status_code=303)


@router.post("/{server_id}/schedule/container-check")
async def save_container_check_schedule(
    server_id: int,
    container_check_enabled: Optional[str] = Form(None),
    container_check_schedule: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    cron = _validate_cron(container_check_schedule)
    server.container_check_schedule = cron
    server.container_check_enabled = container_check_enabled in ("1", "on", "true") and bool(cron)
    session.add(server)
    session.commit()
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_container_check_schedule",
        details={"enabled": server.container_check_enabled, "cron": cron},
    )
    session.commit()
    _sync_server_schedules(server)
    return RedirectResponse(_server_redirect(server_id), status_code=303)


@router.post("/{server_id}/schedule/os-apply")
async def save_os_apply_schedule(
    server_id: int,
    os_apply_enabled: Optional[str] = Form(None),
    os_apply_schedule: str = Form(""),
    os_apply_only_if_updates: Optional[str] = Form(None),
    os_apply_use_full_upgrade: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Opt-in scheduled OS patch apply. Requires os_patch_enabled. Default steps: update+upgrade+autoremove."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    if not server.os_patch_enabled:
        raise HTTPException(400, "Enable OS patch feature on this server first")
    cron = _validate_cron(os_apply_schedule)
    want = os_apply_enabled in ("1", "on", "true") and bool(cron)
    use_full = os_apply_use_full_upgrade in ("1", "on", "true")
    steps = ["update", "full-upgrade" if use_full else "upgrade", "autoremove"]
    server.os_apply_schedule = cron
    server.os_apply_enabled = want
    # Unchecked checkbox is omitted from form → False
    server.os_apply_only_if_updates = os_apply_only_if_updates in ("1", "on", "true")
    server.os_apply_steps = json.dumps(steps)
    session.add(server)
    session.commit()
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_os_apply_schedule",
        details={
            "enabled": server.os_apply_enabled,
            "cron": cron,
            "steps": steps,
            "only_if_updates": server.os_apply_only_if_updates,
        },
    )
    session.commit()
    _sync_server_schedules(server)
    return RedirectResponse(_server_redirect(server_id), status_code=303)


@router.post("/{server_id}/schedule/container-apply")
async def save_container_apply_schedule(
    server_id: int,
    container_apply_enabled: Optional[str] = Form(None),
    container_apply_schedule: str = Form(""),
    container_apply_only_if_updates: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Opt-in scheduled container patch (pull + up -d when IDs change)."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    if not server.container_patch_enabled:
        raise HTTPException(400, "Enable container patch feature on this server first")
    cron = _validate_cron(container_apply_schedule)
    want = container_apply_enabled in ("1", "on", "true") and bool(cron)
    server.container_apply_schedule = cron
    server.container_apply_enabled = want
    server.container_apply_only_if_updates = container_apply_only_if_updates in ("1", "on", "true")
    session.add(server)
    session.commit()
    record_server_audit(
        session,
        server_id=server.id,
        user_id=user.id,
        action="server_container_apply_schedule",
        details={
            "enabled": server.container_apply_enabled,
            "cron": cron,
            "only_if_updates": server.container_apply_only_if_updates,
        },
    )
    session.commit()
    _sync_server_schedules(server)
    return RedirectResponse(_server_redirect(server_id), status_code=303)


# Docker routes extracted to server_docker.py (sub-router included at top of file)


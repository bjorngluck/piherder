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
# Wizard /new* must register before /{server_id}
from .server_wizard import router as wizard_router
from .server_docker import router as docker_router
from .server_backups import router as backups_router
from .server_services import router as services_router
router.include_router(wizard_router, prefix="")
router.include_router(docker_router, prefix="")
router.include_router(backups_router, prefix="")
router.include_router(services_router, prefix="")
from .server_ssh import router as ssh_router
from .server_patch import router as patch_router
router.include_router(ssh_router, prefix="")
router.include_router(patch_router, prefix="")
logger = logging.getLogger("piherder.servers")

from .server_common import server_redirect as _server_redirect, safe_close_ssh as _safe_close_ssh
from .server_ssh import host_cleanup_script_for_server as _host_cleanup_script_for_server

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
    pulse = {
        "total": filter_counts["all"],
        "attention": filter_counts["attention"],
        "os": filter_counts["os"],
        "reboot": filter_counts["reboot"],
        "containers": filter_counts["containers"],
        "backup": sum(1 for s in rows if s.backup_enabled),
        "docker": sum(1 for s in rows if s.container_patch_enabled),
        "os_feat": sum(1 for s in rows if s.os_patch_enabled),
        "named": sum(1 for s in rows if (s.dns_name or "").strip()),
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
            "pulse": pulse,
            "user": user,
            "filter": filt,
            "filter_counts": filter_counts,
        },
    )


@router.post("/bulk")
async def bulk_server_actions(
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    action: str = Form(...),
    server_ids: str = Form(""),
):
    """Enqueue the same job on multiple hosts (feature-flag aware).

    Actions:
      - check_os / check_containers
      - os_patch / container_patch
      - backup

    Servers missing the required feature flag are skipped (not errors).
    Exclusive jobs already active are skipped and reported as skipped.
    """
    act = (action or "").strip().lower()
    allowed = {
        "check_os",
        "check_containers",
        "os_patch",
        "container_patch",
        "backup",
    }
    if act not in allowed:
        raise HTTPException(400, detail=f"Unknown bulk action: {act}")

    ids: list[int] = []
    for p in (server_ids or "").replace(" ", ",").split(","):
        p = p.strip()
        if not p:
            continue
        try:
            ids.append(int(p))
        except ValueError:
            continue
    # de-dupe preserve order
    seen: set[int] = set()
    ordered_ids: list[int] = []
    for sid in ids:
        if sid not in seen:
            seen.add(sid)
            ordered_ids.append(sid)

    started: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    for sid in ordered_ids:
        server = session.get(Server, sid)
        if not server:
            skipped.append({"server_id": sid, "reason": "not_found"})
            continue

        try:
            if act == "check_os":
                if not server.os_patch_enabled:
                    skipped.append({"server_id": sid, "name": server.name, "reason": "os_disabled"})
                    continue
                job = job_service.enqueue_os_update_check(server.id, user_id=user.id)
                if not job:
                    failed.append({"server_id": sid, "name": server.name, "reason": "enqueue_failed"})
                    continue
                # existing active job is returned by enqueue — count as started (attached)
                started.append(
                    {
                        "server_id": sid,
                        "name": server.name,
                        "job_id": job.id,
                        "job_type": job.job_type,
                    }
                )
            elif act == "check_containers":
                if not server.container_patch_enabled:
                    skipped.append(
                        {"server_id": sid, "name": server.name, "reason": "docker_disabled"}
                    )
                    continue
                job = job_service.enqueue_container_update_check(server.id, user_id=user.id)
                if not job:
                    failed.append({"server_id": sid, "name": server.name, "reason": "enqueue_failed"})
                    continue
                started.append(
                    {
                        "server_id": sid,
                        "name": server.name,
                        "job_id": job.id,
                        "job_type": job.job_type,
                    }
                )
            elif act == "os_patch":
                if not server.os_patch_enabled:
                    skipped.append({"server_id": sid, "name": server.name, "reason": "os_disabled"})
                    continue
                try:
                    job = job_service.create_job_and_run(
                        background_tasks,
                        session,
                        server,
                        "os_patch",
                        user_id=user.id,
                    )
                    started.append(
                        {
                            "server_id": sid,
                            "name": server.name,
                            "job_id": job.id,
                            "job_type": job.job_type,
                        }
                    )
                except job_service.JobAlreadyActive as e:
                    skipped.append(
                        {
                            "server_id": sid,
                            "name": server.name,
                            "reason": "already_active",
                            "job_id": e.job.id,
                        }
                    )
            elif act == "container_patch":
                if not server.container_patch_enabled:
                    skipped.append(
                        {"server_id": sid, "name": server.name, "reason": "docker_disabled"}
                    )
                    continue
                try:
                    job = job_service.create_job_and_run(
                        background_tasks,
                        session,
                        server,
                        "container_patch",
                        user_id=user.id,
                    )
                    started.append(
                        {
                            "server_id": sid,
                            "name": server.name,
                            "job_id": job.id,
                            "job_type": job.job_type,
                        }
                    )
                except job_service.JobAlreadyActive as e:
                    skipped.append(
                        {
                            "server_id": sid,
                            "name": server.name,
                            "reason": "already_active",
                            "job_id": e.job.id,
                        }
                    )
            elif act == "backup":
                if not server.backup_enabled:
                    skipped.append(
                        {"server_id": sid, "name": server.name, "reason": "backup_disabled"}
                    )
                    continue
                try:
                    job = job_service.create_job_and_run(
                        background_tasks,
                        session,
                        server,
                        "backup",
                        user_id=user.id,
                    )
                    started.append(
                        {
                            "server_id": sid,
                            "name": server.name,
                            "job_id": job.id,
                            "job_type": job.job_type,
                        }
                    )
                except job_service.BackupAlreadyRunning as e:
                    skipped.append(
                        {
                            "server_id": sid,
                            "name": server.name,
                            "reason": "already_active",
                            "job_id": e.job.id,
                        }
                    )
        except Exception as e:
            logger.warning("bulk action %s failed for server %s: %s", act, sid, e)
            failed.append(
                {"server_id": sid, "name": getattr(server, "name", str(sid)), "reason": str(e)[:120]}
            )

    payload = {
        "action": act,
        "started": started,
        "skipped": skipped,
        "failed": failed,
        "started_count": len(started),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
    }
    if request.headers.get("X-PiHerder-Async") == "1":
        return JSONResponse(payload)
    # Redirect with summary for non-async form posts
    q = (
        f"bulk={act}"
        f"&started={len(started)}"
        f"&skipped={len(skipped)}"
        f"&failed={len(failed)}"
    )
    return RedirectResponse(f"/servers?{q}", status_code=303)


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
    """Legacy URL — advanced single form (wizard primary is /servers/new)."""
    # Viewer can open form but POST still hits create; match prior open GET.
    # Prefer operator for consistency with wizard.
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="add_server.html",
        context={"title": "Add Server (advanced)", "user": user, "advanced": True},
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

    # Prefill Host DNS form from saved fields, Pi-hole A records, or heuristics
    dns_form = {
        "dns_name": "",
        "ip_address": "",
        "dns_ip_override": "",
        "dns_manage_a": False,
        "is_saved": False,
        "source": "empty",
        "pihole_match": None,
    }
    try:
        from ..services import dns_fabric as fabric
        from ..services.app_settings import load_settings as load_app_settings

        base = (load_app_settings().get("dns_base_domain") or "").strip()
        dns_form = fabric.host_dns_form_defaults(
            session, server, base_domain=base, probe_pihole=True
        )
    except Exception:
        pass

    fabric_rack = None
    hosts_map_url = f"/dns/physical?focus=n:host-{server.id}#map"
    try:
        from ..services import dns_fabric as fabric

        fabric_rack = fabric.fabric_rack_for_server(session, server.id)
        hosts_map_url = fabric.hosts_map_url(server_id=server.id)
    except Exception:
        fabric_rack = None

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_detail.html",
        context={
            "title": server.name,
            "server": server_dict,
            "dns_form": dns_form,
            "fabric_rack": fabric_rack,
            "hosts_map_url": hosts_map_url,
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

    Important: schedule reboot slightly deferred so the SSH command can return
    and PiHerder can finish the HTTP response + audit. Immediate ``reboot``
    often kills the channel (and, if this is the PiHerder host, the whole stack)
    mid-request, which looks like a hang.
    """
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    success = False
    details = "Reboot initiated"
    # Deferred + backgrounded so the SSH command returns quickly.
    # Least-priv sudoers only allow the reboot binary (not sudo sh), so we
    # background via the login shell + nohup, and sudo only the reboot path.
    # sleep 1 gives PiHerder time to finish HTTP/audit when rebooting its own host.
    reboot_cmds = (
        "nohup sh -c 'sleep 1; sudo -n /usr/sbin/reboot' >/dev/null 2>&1 &",
        "nohup sh -c 'sleep 1; sudo -n /sbin/reboot' >/dev/null 2>&1 &",
        "nohup sh -c 'sleep 1; sudo -n /usr/bin/systemctl reboot' >/dev/null 2>&1 &",
    )
    try:
        client = ssh_service.get_ssh_client(server)
        last_err = ""
        try:
            for cmd in reboot_cmds:
                try:
                    # Short channel timeout; backgrounded reboot should return fast
                    _stdin, stdout, stderr = client.exec_command(cmd, timeout=8)
                    import time as _time

                    deadline = _time.monotonic() + 1.5
                    while _time.monotonic() < deadline:
                        if stdout.channel.exit_status_ready():
                            break
                        _time.sleep(0.1)
                    if stdout.channel.exit_status_ready():
                        code = stdout.channel.recv_exit_status()
                        err = (stderr.read() or b"").decode(errors="replace")[:200]
                        out = (stdout.read() or b"").decode(errors="replace")[:200]
                        if code == 0:
                            success = True
                            details = "Reboot scheduled (host will restart shortly)"
                            break
                        last_err = (err or out or f"exit {code}").strip()
                        continue
                    # Background job started; shell still open — treat as success
                    success = True
                    details = "Reboot command sent"
                    break
                except Exception as e:
                    # Connection dropped after reboot is normal
                    msg = str(e).lower()
                    if any(
                        x in msg
                        for x in ("eof", "reset", "closed", "timeout", "timed out")
                    ):
                        success = True
                        details = "Reboot sent (connection closed)"
                        break
                    last_err = str(e)[:200]
            if not success and last_err:
                details = f"Reboot command failed: {last_err}"
        finally:
            _safe_close_ssh(client, timeout=1.5)
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
    # Optional — only General form sends these. Features form must NOT send
    # empty dns_* fields (that used to wipe DNS identity on every feature save).
    include_dns: Optional[str] = Form(None),
    dns_name: str = Form(""),
    dns_ip_override: str = Form(""),
    ip_address: str = Form(""),
    dns_manage_a: Optional[str] = Form(None),
    dns_sync_now: Optional[str] = Form(None),
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
    touch_dns = include_dns in ("1", "on", "true", "yes")
    new_ip = (ip_address or "").strip() or None
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
    if touch_dns and (server.ip_address or None) != new_ip:
        changed.append("ip_address")
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
    if touch_dns:
        server.ip_address = new_ip

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

    # Host DNS only when General form explicitly includes the DNS section
    if touch_dns:
        from urllib.parse import quote

        try:
            from ..services import dns_fabric as fabric

            dns_result = fabric.update_server_dns(
                session,
                server,
                dns_name=dns_name or None,
                dns_manage_a=dns_manage_a in ("on", "1", "true"),
                dns_ip_override=dns_ip_override or None,
                user_id=user.id,
                sync_now=dns_sync_now in ("on", "1", "true"),
            )
        except Exception as e:
            msg = getattr(e, "message", None) or str(e)
            return RedirectResponse(
                f"/servers/{server_id}?error={quote(msg[:220])}",
                status_code=303,
            )

        action = (dns_result or {}).get("action") or "saved"
        sync = (dns_result or {}).get("sync") or []
        if action == "synced" and sync:
            ok = sum(1 for r in sync if r.get("ok"))
            n = len(sync)
            return RedirectResponse(
                f"/servers/{server_id}?msg=dns_synced&detail={quote(f'{ok}/{n}')}",
                status_code=303,
            )
        if action == "removed":
            ok = sum(1 for r in sync if r.get("ok")) if sync else 0
            n = len(sync) if sync else 0
            return RedirectResponse(
                f"/servers/{server_id}?msg=dns_removed&detail={quote(f'{ok}/{n}')}",
                status_code=303,
            )
        if (dns_name or "").strip():
            return RedirectResponse(
                f"/servers/{server_id}?msg=dns_saved",
                status_code=303,
            )

    return RedirectResponse(_server_redirect(server_id), status_code=303)



# SSH routes → server_ssh.py · patch/check/schedule → server_patch.py
# Docker → server_docker.py · backups → server_backups.py

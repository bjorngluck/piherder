"""
Docker sub-router for PiHerder.

Extracted from routers/servers.py to keep the main servers router lean.
All routes under /servers/{server_id}/docker/* 
"""

from fastapi import APIRouter, Depends, Form, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from sqlmodel import Session
import json
from typing import Optional
from datetime import datetime
from urllib.parse import quote

from ..database import get_session
from ..models import Server
from ..services.audit_write import make_audit_log
from ..services import docker_management as docker_svc
from ..services import docker_inventory as inventory_svc
from ..services import env_file_ui
from ..services.service_migrate import host_lock as host_lock_svc
from ..services.service_migrate import preflight as migrate_preflight
from ..services.nav_shortcuts import host_feature_context
from .. import templates as templates_mod
from ..security.auth import (
    get_current_user,
    get_operator_user,
    secrets_unlock_active,
    role_at_least,
    ROLE_OPERATOR,
)
from ..models import User

router = APIRouter()

from .server_docker_compose import router as compose_router
router.include_router(compose_router, prefix="")

def _invalidate_inventory(session: Session, server: Server, background_tasks: Optional[BackgroundTasks] = None):
    """Clear short cache + mark DB inventory stale (+ optional BG refresh)."""
    try:
        inventory_svc.invalidate_after_mutation(session, server, background_tasks)
    except Exception:
        try:
            docker_svc._CACHE.clear()
        except Exception:
            pass


@router.get("/{server_id}/docker", response_class=HTMLResponse)
async def docker_page(
    server_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Shell-first: chrome immediately. Stack from DB snapshot; BG refresh if stale."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    force = request.query_params.get("nocache") in ("1", "true", "yes")
    if force:
        try:
            docker_svc._CACHE.clear()
        except Exception:
            pass
        inventory_svc.request_refresh(
            background_tasks, server_id, force=True, server=server, session=session
        )
    elif inventory_svc.is_stale(server) or inventory_svc.is_refresh_stuck(server):
        inventory_svc.request_refresh(
            background_tasks, server_id, force=False, server=server, session=session
        )

    # No blocking SSH here — fragment renders snapshot (or skeleton while first refresh runs).
    update_check = request.query_params.get("update_check")
    update_status = request.query_params.get("status")
    build_status = request.query_params.get("build_status")
    inv_meta = inventory_svc.inventory_meta(server)

    from ..services.nav_shortcuts import host_feature_context
    from ..services import list_query as lq

    _nav = host_feature_context(session, int(user.id) if user else None, server, "docker")
    lp = lq.docker_params(request)
    docker_keep = lq.query_string(
        {
            "q": lp["q"],
            "status": lp["status"],
            "per_page": lp["per_page"],
            "refresh": request.query_params.get("refresh") or "",
        }
    )
    resp = templates_mod.templates.TemplateResponse(
        request=request,
        name="docker.html",
        context={
            "title": f"Docker - {server.name}",
            "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}),
            "containers": [],
            "projects": [],
            "orphan_containers": [],
            "docker_shell": True,
            "inventory_meta": inv_meta,
            "force_refresh": force,
            "user": user,
            "update_check": update_check,
            "update_status": update_status,
            "build_status": build_status,
            "q": lp["q"],
            "docker_status": lp["status"],
            "page": lp["page"],
            "per_page": lp["per_page"],
            "per_page_choices": list(lq.PER_PAGE_CHOICES),
            "docker_keep": docker_keep,
            **_nav,
        }
    )
    lq.attach_per_page_cookie(resp, lp["per_page"])
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@router.get("/{server_id}/docker/container/mounts")
async def docker_container_mounts(
    server_id: int,
    name: str,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """L3: full volume paths + host disk usage for one container (on expand)."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    ref = (name or "").strip()
    if not ref:
        raise HTTPException(400, "name required")
    result = docker_svc.get_container_mounts_detail(server, ref)
    return JSONResponse(result)


@router.post("/{server_id}/docker/container/{action}")
async def docker_container_action(
    server_id: int,
    action: str,
    name: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    from ..services.input_validation import (
        DOCKER_CONTAINER_ACTIONS,
        ValidationError,
        allowlist,
        clamp_str,
    )

    try:
        action = allowlist(
            (action or "").strip().lower(),
            DOCKER_CONTAINER_ACTIONS,
            field="action",
        )
        name = clamp_str(name, max_len=200, field="container", allow_empty=False)
    except ValidationError as e:
        raise HTTPException(400, detail=str(e)) from e

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    result = docker_svc.container_action(server, name, action)
    ok = bool(result.get("success"))
    try:
        _invalidate_inventory(session, server)
        audit = make_audit_log(
            user_id=user.id if user else None,
            server_id=server_id,
            action=f"docker_container_{action}",
            status="success" if ok else "failed",
            details=f"Container {name}",
            output_snippet=str(result)[:500],
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        session.add(audit)
        session.commit()
    except Exception:
        pass
    from urllib.parse import quote
    if ok:
        return RedirectResponse(
            f"/servers/{server_id}/docker?nocache=1&msg=container_{action}",
            status_code=303,
        )
    detail = quote((result.get("error") or result.get("output") or "failed")[:160], safe="")
    return RedirectResponse(
        f"/servers/{server_id}/docker?nocache=1&error=container_{action}&detail={detail}",
        status_code=303,
    )


@router.post("/{server_id}/docker/redeploy")
async def redeploy(
    request: Request,
    server_id: int,
    background_tasks: BackgroundTasks,
    project_path: str = Form(...),
    pull: str = Form("true"),
    compose_file: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Deploy/redeploy a compose project as a Job with live log (B07).

    Optional ``compose_file`` basename scopes deploy to a compose set
    (``docker compose -f …``) still under the same project directory.
    """
    from ..services import jobs as job_service
    from urllib.parse import quote
    import os

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    do_pull = (pull or "true").strip().lower() in ("1", "true", "yes", "on")
    proj_name = os.path.basename((project_path or "").rstrip("/")) or project_path
    path = (project_path or "").strip()
    set_file = (compose_file or "").strip().split("/")[-1]
    compose_files = [set_file] if set_file else None
    already_active = False
    try:
        job = job_service.enqueue_docker_stack_deploy(
            server.id,
            path,
            pull=do_pull,
            compose_files=compose_files,
            user_id=user.id if user else None,
            background_tasks=background_tasks,
        )
    except job_service.JobAlreadyActive as e:
        job = e.job
        already_active = True
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not job:
        raise HTTPException(500, "Could not queue stack deploy")

    if request.headers.get("X-PiHerder-Async") == "1":
        return JSONResponse(
            {
                "job_id": job.id,
                "status": job.status,
                "job_type": "docker_stack_deploy",
                "project": proj_name,
                "already_active": already_active,
            },
            status_code=409 if already_active else 200,
        )

    return RedirectResponse(
        f"/servers/{server_id}/docker?deploy=queued&project={quote(str(proj_name), safe='')}&job_id={job.id}",
        status_code=303,
    )


@router.post("/{server_id}/docker/compose/{action}")
async def compose_project_action(
    request: Request,
    server_id: int,
    action: str,
    background_tasks: BackgroundTasks,
    project_path: str = Form(...),
    service: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Compose project action: stop/start/restart/down.

    Whole-project stop/start/restart run as Jobs with live log (H2.75 P1).
    Single-service stop/start/restart and ``down`` stay synchronous.
    """
    from ..services import jobs as job_service
    from ..security.auth import role_at_least, ROLE_OPERATOR
    from urllib.parse import quote
    import os

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    act = (action or "").strip().lower()
    path = (project_path or "").strip()
    svc = (service or "").strip() or None

    # Bulk lifecycle → Job + JobHold (exclusive with other stack mutations)
    if act in ("stop", "start", "restart") and not svc:
        if not role_at_least(user, ROLE_OPERATOR):
            raise HTTPException(403, "Operator or admin role required")
        already_active = False
        try:
            job = job_service.enqueue_docker_stack_lifecycle(
                server.id,
                path,
                act,
                user_id=user.id if user else None,
                background_tasks=background_tasks,
            )
        except job_service.JobAlreadyActive as e:
            job = e.job
            already_active = True
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if not job:
            raise HTTPException(500, f"Could not queue stack {act}")

        proj_name = os.path.basename(path.rstrip("/")) or path
        job_type = f"docker_stack_{act}"
        if request.headers.get("X-PiHerder-Async") == "1":
            return JSONResponse(
                {
                    "job_id": job.id,
                    "status": job.status,
                    "job_type": job_type,
                    "project": proj_name,
                    "action": act,
                    "already_active": already_active,
                },
                status_code=409 if already_active else 200,
            )
        return RedirectResponse(
            f"/servers/{server_id}/docker?lifecycle={act}"
            f"&project={quote(str(proj_name), safe='')}"
            f"&job_id={job.id}",
            status_code=303,
        )

    # Single-service lifecycle or compose down (undeploy)
    res = docker_svc.compose_action(server, path, act, service=svc)
    try:
        details = f"Project {path}"
        if svc:
            details += f" service={svc}"
        audit = make_audit_log(
            user_id=user.id if user else None,
            server_id=server_id,
            action=f"docker_compose_{act}",
            status="success" if res.get("success") else "failed",
            details=details,
            output_snippet=str(res)[:500],
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        session.add(audit)
        session.commit()
    except Exception:
        pass
    try:
        if res.get("success"):
            _invalidate_inventory(session, server)
    except Exception:
        pass
    return RedirectResponse(f"/servers/{server_id}/docker?nocache=1", status_code=303)


@router.get("/{server_id}/docker/logs/{container}")
async def get_docker_logs(
    server_id: int,
    container: str,
    lines: int = 200,
    format: str = None,
    request: Request = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    project_path = request.query_params.get("project_path") if request else None
    logs = docker_svc.get_logs(server, container, lines=lines, project_path=project_path)

    is_json = (format == "json") or (request and "application/json" in (request.headers.get("accept") or "").lower())
    if is_json:
        return JSONResponse({"container": container, "logs": logs})

    resp = templates_mod.templates.TemplateResponse(
        request=request,
        name="docker_logs.html",
        context={
            "title": f"Logs - {container}",
            "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}),
            "container": container,
            "logs": logs,
            "lines": lines,
            "user": user
        }
    )
    # Prevent browser caching of the logs page (so layout changes are visible)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@router.get("/{server_id}/docker/containers-fragment", response_class=HTMLResponse)
async def containers_fragment(server_id: int, request: Request, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    """Legacy fragment: full containers table (kept for compatibility)."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        import app.services.docker_management as _dm
        if request.query_params.get("nocache"):
            _dm._CACHE.clear()
    except Exception:
        pass

    try:
        interval = max(60, int(request.query_params.get("refresh", "120")))
    except Exception:
        interval = 120

    try:
        containers = docker_svc.list_containers(server)
    except Exception as e:
        containers = [{"name": "error", "status": str(e), "state": "error", "image": "", "version": "", "ports_display": "", "running": False}]

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="docker_containers_table.html",
        context={"server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}), "containers": containers, "refresh": interval}
    )


@router.get("/{server_id}/docker/stack-fragment", response_class=HTMLResponse)
async def stack_fragment(
    server_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Compose + nested services from DB snapshot; kick BG refresh when stale."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    try:
        interval = max(60, int(request.query_params.get("refresh", "120")))
    except Exception:
        interval = 120

    force = request.query_params.get("nocache") in ("1", "true", "yes")
    if force:
        try:
            docker_svc._CACHE.clear()
        except Exception:
            pass
        inventory_svc.request_refresh(
            background_tasks, server_id, force=True, server=server, session=session
        )
    elif inventory_svc.is_stale(server) or inventory_svc.is_refresh_stuck(server):
        inventory_svc.request_refresh(
            background_tasks, server_id, force=False, server=server, session=session
        )

    # Re-read after possible status flip to refreshing
    session.refresh(server)
    inv = inventory_svc.parse_inventory(server)
    inv_meta = inventory_svc.inventory_meta(server)
    status = inv_meta.get("status") or "never"
    refreshing = status == "refreshing" or (
        force and status not in ("ok",) and not inv
    )
    # Poll faster while first load / in-flight refresh so UI swaps when ready
    poll_fast = refreshing or (not inv and status in ("never", "error", "refreshing"))

    projects = list((inv or {}).get("projects") or [])
    orphan_containers = list((inv or {}).get("orphan_containers") or [])
    # Re-annotate update flags from latest check summary (cheap, no SSH)
    if projects or orphan_containers:
        projects, orphan_containers = docker_svc.annotate_update_flags(
            projects, orphan_containers, server
        )

    from ..services import list_query as lq

    lp = lq.docker_params(request)
    filtered = lq.filter_docker_stack(
        projects,
        orphan_containers,
        q=lp["q"],
        status=lp["status"],
        page=lp["page"],
        per_page=lp["per_page"],
        force_project=lp["project"] or None,
    )
    inventory_project_count = len(projects)
    inventory_orphan_count = len(orphan_containers)
    projects = filtered["projects"]
    orphan_containers = filtered["orphan_containers"]

    # Template-managed stacks (StackDeployment desired state)
    template_deployments_count = 0
    try:
        from ..services.service_templates.deploy import (
            annotate_projects_with_deployments,
            deployments_index_by_project,
        )

        dep_idx = deployments_index_by_project(session, server_id)
        template_deployments_count = len(dep_idx)
        if projects:
            annotate_projects_with_deployments(projects, dep_idx)
    except Exception:
        pass

    kuma_by_project: dict = {}
    kuma_by_container: dict = {}
    grafana_by_project: dict = {}
    grafana_by_container: dict = {}
    fabric_by_project: dict = {}
    hosts_map_url = f"/dns/physical?focus=n:host-{server_id}#map"
    try:
        from ..services.integrations import registry as integ_reg

        kuma_idx = integ_reg.kuma_index_for_server(session, server_id)
        kuma_by_project = kuma_idx.get("by_project") or {}
        kuma_by_container = kuma_idx.get("by_container") or {}
        gf_idx = integ_reg.grafana_index_for_server(session, server_id)
        grafana_by_project = gf_idx.get("by_project") or {}
        grafana_by_container = gf_idx.get("by_container") or {}
    except Exception:
        pass
    try:
        from ..services import dns_fabric as fabric

        fidx = fabric.fabric_index_for_server(session, server_id)
        fabric_by_project = fidx.get("by_project") or {}
        hosts_map_url = fidx.get("hosts_map_url") or hosts_map_url
    except Exception:
        fabric_by_project = {}

    try:
        host_lock_svc.annotate_projects(session, server, projects)
    except Exception:
        pass
    can_host_lock = role_at_least(user, ROLE_OPERATOR)

    docker_keep = lq.query_string(
        {
            "q": lp["q"],
            "status": lp["status"],
            "per_page": lp["per_page"],
            "refresh": interval,
        }
    )
    pager_query = lq.query_string(
        {
            "q": lp["q"],
            "status": lp["status"],
            "per_page": lp["per_page"],
            "refresh": interval,
            "project": lp["project"],
        }
    )
    resp = templates_mod.templates.TemplateResponse(
        request=request,
        name="docker_stack.html",
        context={
            "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}),
            "projects": projects,
            "orphan_containers": orphan_containers,
            "refresh": interval,
            "docker_shell": False,
            "inventory_meta": inv_meta,
            "inventory_refreshing": refreshing or status == "refreshing",
            "inventory_poll_fast": poll_fast,
            "q": lp["q"],
            "docker_status": lp["status"],
            "page": filtered["page"],
            "per_page": lp["per_page"],
            "per_page_choices": list(lq.PER_PAGE_CHOICES),
            "total": filtered["total"],
            "total_pages": filtered["total_pages"],
            "pager_query": pager_query,
            "pager_path": f"/servers/{server_id}/docker",
            "docker_keep": docker_keep,
            "docker_filtered": filtered["filtered"],
            "docker_forced_project": filtered["forced_project"],
            "inventory_project_count": inventory_project_count,
            "inventory_orphan_count": inventory_orphan_count,
            "pending_update_projects": sorted(
                docker_svc.parse_container_updates_summary(server).get("projects") or []
            ),
            "kuma_by_project": kuma_by_project,
            "kuma_by_container": kuma_by_container,
            "grafana_by_project": grafana_by_project,
            "grafana_by_container": grafana_by_container,
            "fabric_by_project": fabric_by_project,
            "hosts_map_url": hosts_map_url,
            "template_deployments_count": template_deployments_count,
            "user": user,
            "can_host_lock": can_host_lock,
            "migrate_enabled": host_lock_svc.migrate_enabled(),
        },
    )
    return lq.attach_per_page_cookie(resp, lp["per_page"])


def _docker_redirect(server_id: int, *, msg: str = "", error: str = "", detail: str = "") -> RedirectResponse:
    q = ["nocache=1"]
    if msg:
        q.append(f"msg={quote(msg, safe='')}")
    if error:
        q.append(f"error={quote(error, safe='')}")
    if detail:
        q.append(f"detail={quote(detail[:160], safe='')}")
    return RedirectResponse(
        f"/servers/{server_id}/docker?{'&'.join(q)}",
        status_code=303,
    )


@router.post("/{server_id}/docker/host-lock")
async def docker_host_lock(
    request: Request,
    server_id: int,
    project: str = Form(...),
    reason: str = Form("operator"),
    note: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        row = host_lock_svc.set_host_lock(
            session,
            server,
            project,
            reason=reason,
            note=note,
            user_id=user.id if user else None,
        )
        session.add(
            make_audit_log(
                user_id=user.id if user else None,
                server_id=server_id,
                action="service_host_lock",
                status="success",
                details=(
                    f"project={row.compose_project} reason={row.lock_reason}"
                    + (f" note={row.lock_note}" if row.lock_note else "")
                ),
            )
        )
        session.commit()
    except host_lock_svc.HostLockError as e:
        return _docker_redirect(server_id, error="host_lock", detail=e.message)
    return _docker_redirect(server_id, msg="host_locked")


@router.post("/{server_id}/docker/host-unlock")
async def docker_host_unlock(
    request: Request,
    server_id: int,
    project: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        row = host_lock_svc.unlock_host(session, server, project)
        session.add(
            make_audit_log(
                user_id=user.id if user else None,
                server_id=server_id,
                action="service_host_unlock",
                status="success",
                details=f"project={row.compose_project}",
            )
        )
        session.commit()
    except host_lock_svc.HostLockError as e:
        return _docker_redirect(server_id, error="host_lock", detail=e.message)
    return _docker_redirect(server_id, msg="host_unlocked")


def _require_migrate_surface() -> None:
    if not host_lock_svc.migrate_surface_allowed():
        raise HTTPException(404, "Service migration is off")


@router.get("/{server_id}/docker/migrate", response_class=HTMLResponse)
async def docker_migrate_wizard(
    server_id: int,
    request: Request,
    project: str = "",
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    _require_migrate_surface()
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        proj = host_lock_svc.compose_project_name(project)
    except host_lock_svc.HostLockError as e:
        raise HTTPException(e.status_code, detail=e.message) from e
    dests = migrate_preflight.eligible_destinations(session, server)
    lock = host_lock_svc.lock_state(session, server, proj)
    _nav = host_feature_context(session, int(user.id) if user else None, server, "docker")
    try:
        session.add(
            make_audit_log(
                user_id=user.id if user else None,
                server_id=server_id,
                action="service_migrate_preview",
                status="success",
                details=f"project={proj}",
            )
        )
        session.commit()
    except Exception:
        session.rollback()
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="docker_migrate.html",
        context={
            "title": f"Move {proj} — {server.name}",
            "server": server,
            "project": proj,
            "destinations": dests,
            "host_lock": lock,
            "user": user,
            **_nav,
        },
    )


@router.get("/{server_id}/docker/migrate/preflight", response_class=HTMLResponse)
async def docker_migrate_preflight(
    server_id: int,
    request: Request,
    project: str = "",
    dest: str = "",
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    _require_migrate_surface()
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        dest_id = int((dest or "").strip() or "0")
    except ValueError:
        dest_id = 0
    dest_server = session.get(Server, dest_id) if dest_id else None
    if not dest_server:
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="partials/docker_migrate_preflight.html",
            context={"result": None, "project": project, "server": server},
        )
    try:
        proj = host_lock_svc.compose_project_name(project)
    except host_lock_svc.HostLockError as e:
        raise HTTPException(e.status_code, detail=e.message) from e
    from ..services.service_migrate.facts import herder_free_bytes, probe_host_facts

    src_facts = probe_host_facts(server)
    dest_facts = probe_host_facts(dest_server)
    result = migrate_preflight.run_preflight(
        session,
        source=server,
        dest=dest_server,
        project=proj,
        source_facts=src_facts,
        dest_facts=dest_facts,
        herder_free=herder_free_bytes(),
    )
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="partials/docker_migrate_preflight.html",
        context={"result": result, "project": proj, "server": server},
    )


@router.post("/{server_id}/docker/migrate")
async def docker_migrate_start(
    request: Request,
    server_id: int,
    background_tasks: BackgroundTasks,
    project: str = Form(...),
    dest: str = Form(...),
    leftover: str = Form("stopped"),
    devices_ack: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    _require_migrate_surface()
    from ..services import jobs as job_service

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        dest_id = int((dest or "").strip() or "0")
    except ValueError:
        dest_id = 0
    dest_server = session.get(Server, dest_id) if dest_id else None
    if not dest_server:
        raise HTTPException(400, "destination required")
    try:
        left = (leftover or "stopped").strip().lower()
        if left not in ("stopped", "down"):
            left = "stopped"
        ack = (devices_ack or "").strip().lower() in ("1", "true", "on", "yes")
        job = job_service.enqueue_service_migrate(
            server_id,
            dest_id,
            project,
            user_id=user.id if user else None,
            background_tasks=background_tasks,
            leftover=left,
            devices_ack=ack,
        )
    except job_service.JobAlreadyActive as e:
        if request.headers.get("X-PiHerder-Async") == "1":
            return JSONResponse(
                {
                    "job_id": e.job.id,
                    "status": e.job.status,
                    "job_type": "service_migrate",
                    "already_active": True,
                },
                status_code=409,
            )
        raise HTTPException(409, "A stack or backup job is already running on source or dest") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except host_lock_svc.HostLockError as e:
        raise HTTPException(e.status_code, e.message) from e
    if request.headers.get("X-PiHerder-Async") == "1":
        return JSONResponse(
            {
                "job_id": job.id,
                "status": job.status,
                "job_type": "service_migrate",
                "project": project,
                "already_active": False,
            }
        )
    return RedirectResponse(
        f"/jobs?highlight={job.id}",
        status_code=303,
    )


@router.post("/{server_id}/docker/check-updates")
async def check_updates(
    request: Request,
    server_id: int,
    background_tasks: BackgroundTasks,
    project_path: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Check one compose project for registry image updates as a Job (B07)."""
    from ..services import jobs as job_service
    from urllib.parse import quote
    import os

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    path = (project_path or "").strip()
    proj_name = os.path.basename(path.rstrip("/")) or path
    already_active = False
    try:
        job = job_service.enqueue_docker_stack_check(
            server.id,
            path,
            user_id=user.id if user else None,
            background_tasks=background_tasks,
        )
    except job_service.JobAlreadyActive as e:
        job = e.job
        already_active = True
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not job:
        raise HTTPException(500, "Could not queue stack check")

    if request.headers.get("X-PiHerder-Async") == "1":
        return JSONResponse(
            {
                "job_id": job.id,
                "status": job.status,
                "job_type": "docker_stack_check",
                "project": proj_name,
                "already_active": already_active,
            },
            status_code=409 if already_active else 200,
        )

    return RedirectResponse(
        f"/servers/{server_id}/docker?update_check={quote(path, safe='')}&status=queued&job_id={job.id}",
        status_code=303,
    )


@router.get("/{server_id}/docker/logs/{container}/stream")
async def stream_container_logs(
    server_id: int,
    container: str,
    lines: int = 30,
    project_path: str = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """SSE log stream — operator+ (live SSH). Viewers use cached inventory, not a PTY."""
    _ = user
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    return StreamingResponse(
        docker_svc.stream_logs(server, container, lines=lines, project_path=project_path),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@router.get("/{server_id}/docker/build-progress", response_class=HTMLResponse)
async def build_progress(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    """Render the build progress page. The actual build runs when the SSE /build-stream connects."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    project = request.query_params.get("project") or ""
    services_param = request.query_params.get("services") or ""
    services = [s.strip() for s in services_param.split(",") if s.strip()]
    no_cache = (request.query_params.get("no_cache") or "false").lower() in ("true", "1", "yes")

    try:
        audit = make_audit_log(
            user_id=user.id if user else None,
            server_id=server_id,
            action="docker_compose_build",
            status="started",
            details=f"Project {project} services={services} no_cache={no_cache}",
            started_at=datetime.utcnow(),
        )
        session.add(audit)
        session.commit()
    except Exception:
        pass

    resp = templates_mod.templates.TemplateResponse(
        request=request,
        name="docker_build_progress.html",
        context={
            "title": f"Build - {project}",
            "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}),
            "server_id": server_id,
            "project": project,
            "services": services,
            "no_cache": no_cache,
            "user": user,
        }
    )
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@router.get("/{server_id}/docker/build-stream")
async def build_stream_get_blocked(
    server_id: int,
    user: User = Depends(get_operator_user),
):
    """Builds are POST-only (mutating GET was CSRF + unquoted path)."""
    _ = (server_id, user)
    raise HTTPException(
        405,
        detail="Use POST /docker/build-stream with a compose project name",
    )


@router.post("/{server_id}/docker/build-stream")
async def build_stream(
    server_id: int,
    project: str = Form(...),
    services: str = Form(""),
    no_cache: str = Form("false"),
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """SSE stream of ``docker compose build`` (operator+, POST, named project only)."""
    _ = user
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    try:
        project_path = docker_svc.resolve_compose_project_path(server, project)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(404, detail="Project not found")
        raise HTTPException(400, detail="Invalid project")
    except Exception as e:
        raise HTTPException(500, detail=f"Failed to inspect projects on host: {str(e)[:120]}")

    svc_list = [s.strip() for s in services.split(",") if s.strip()] if services else None
    no_cache_bool = str(no_cache).lower() in ("true", "1", "yes")

    return StreamingResponse(
        docker_svc.stream_compose_build(server, project_path, services=svc_list, no_cache=no_cache_bool),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


# === Cleanup unused/dangling routes (were referenced in docker.html template but missing after router split) ===
@router.get("/{server_id}/docker/unused", response_class=HTMLResponse)
async def list_unused_route(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    try:
        data = docker_svc.list_unused_images_and_containers(server)
    except Exception as e:
        data = {
            "dangling_images": [],
            "exited_containers": [],
            "success": False,
            "errors": [str(e)[:200]],
        }

    from ..services.docker_unused_html import render_unused_list_html

    return HTMLResponse(render_unused_list_html(data))


@router.post("/{server_id}/docker/prune-unused")
async def prune_unused_route(
    server_id: int,
    prune_type: str = Form("both"),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    from ..services.input_validation import PRUNE_TYPES, ValidationError, allowlist

    try:
        prune_type = allowlist(
            (prune_type or "both").strip().lower(),
            PRUNE_TYPES,
            field="prune_type",
            default="both",
        )
    except ValidationError as e:
        raise HTTPException(400, detail=str(e)) from e

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    try:
        res = docker_svc.prune_unused(server, prune_type=prune_type)
        ok = "ok" if res.get("success") else "fail"
        # record audit
        try:
            audit = make_audit_log(
                user_id=user.id if user else None,
                server_id=server_id,
                action="docker_prune_unused",
                status="success" if res.get("success") else "failed",
                details=f"prune_type={prune_type}",
                output_snippet=str(res.get("output", ""))[:500],
                started_at=datetime.utcnow(),
                finished_at=datetime.utcnow(),
            )
            session.add(audit)
            session.commit()
        except Exception:
            pass
    except Exception as e:
        ok = "fail"
        # best effort audit fail
        try:
            audit = make_audit_log(
                user_id=user.id if user else None,
                server_id=server_id,
                action="docker_prune_unused",
                status="failed",
                details=f"prune_type={prune_type}",
                output_snippet=str(e)[:300],
                started_at=datetime.utcnow(),
                finished_at=datetime.utcnow(),
            )
            session.add(audit)
            session.commit()
        except Exception:
            pass
        return RedirectResponse(f"/servers/{server_id}/docker?prune=fail&prune_type={prune_type}", status_code=303)

    return RedirectResponse(f"/servers/{server_id}/docker?prune={ok}&prune_type={prune_type}", status_code=303)

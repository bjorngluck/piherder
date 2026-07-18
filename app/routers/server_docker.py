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

from ..database import get_session
from ..models import Server
from ..services.audit_write import make_audit_log
from ..services import docker_management as docker_svc
from ..services import docker_inventory as inventory_svc
from ..services import env_file_ui
from .. import templates as templates_mod
from ..security.auth import get_current_user, secrets_unlock_active
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
        }
    )
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
    action = (action or "").strip().lower()
    if action not in ("start", "stop", "restart"):
        raise HTTPException(400, "Invalid container action")

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
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Deploy/redeploy a compose project as a Job with live log (B07)."""
    from ..services import jobs as job_service
    from urllib.parse import quote
    import os

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    do_pull = (pull or "true").strip().lower() in ("1", "true", "yes", "on")
    proj_name = os.path.basename((project_path or "").rstrip("/")) or project_path
    path = (project_path or "").strip()
    already_active = False
    try:
        job = job_service.enqueue_docker_stack_deploy(
            server.id,
            path,
            pull=do_pull,
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
    user: User = Depends(get_current_user)
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

    return templates_mod.templates.TemplateResponse(
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
        },
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
async def stream_container_logs(server_id: int, container: str, lines: int = 30, project_path: str = None, session: Session = Depends(get_session)):

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
async def build_stream(
    server_id: int,
    project: str = None,
    services: str = "",
    no_cache: str = "false",
    session: Session = Depends(get_session),
):
    """SSE endpoint that runs docker compose build on the host and streams output."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    if not project:
        raise HTTPException(400, detail="project is required")

    # Resolve project name -> full path (same pattern as file-content, edit, etc.)
    try:
        projects = docker_svc.list_compose_projects(server)
    except Exception as e:
        raise HTTPException(500, detail=f"Failed to inspect projects on host: {str(e)[:120]}")
    proj = next((p for p in projects if p.get("name") == project), None)
    if not proj:
        if project.startswith("/"):
            project_path = project  # allow direct path as fallback
        else:
            raise HTTPException(404, detail="Project not found")
    else:
        project_path = proj["path"]

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

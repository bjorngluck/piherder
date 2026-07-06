"""
Docker sub-router for PiHerder.

Extracted from routers/servers.py to keep the main servers router lean.
All routes under /servers/{server_id}/docker/* 
"""

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from sqlmodel import Session
import json
from typing import Optional
from datetime import datetime

from ..database import get_session
from ..models import Server, AuditLog
from ..services import docker_management as docker_svc
from .. import templates as templates_mod
from ..security.auth import get_current_user
from ..models import User

router = APIRouter()


@router.get("/{server_id}/docker", response_class=HTMLResponse)
async def docker_page(server_id: int, request: Request, session: Session = Depends(get_session), user: User = Depends(get_current_user)):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        import app.services.docker_management as _dm
        if request.query_params.get("nocache"):
            _dm._CACHE.clear()
    except:
        pass

    try:
        containers = docker_svc.list_containers(server)
    except Exception as e:
        containers = [{
            "id": "",
            "name": "error",
            "image": "",
            "version": "",
            "status": str(e)[:300],
            "state": "error",
            "running": False,
            "ports": [],
            "ports_display": "—",
            "created": "",
            "command": "",
        }]

    try:
        projects = docker_svc.list_compose_projects(server)
    except Exception:
        projects = []

    update_check = request.query_params.get("update_check")
    update_status = request.query_params.get("status")
    build_status = request.query_params.get("build_status")

    resp = templates_mod.templates.TemplateResponse(
        request=request,
        name="docker.html",
        context={
            "title": f"Docker - {server.name}",
            "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}),
            "containers": containers,
            "projects": projects,
            "user": user,
            "update_check": update_check,
            "update_status": update_status,
            "build_status": build_status
        }
    )
    # Prevent browser caching of the dynamic docker management page (so UI changes to modals/logs are visible immediately)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@router.post("/{server_id}/docker/container/{action}")
async def docker_container_action(
    server_id: int,
    action: str,
    name: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    result = docker_svc.container_action(server, name, action)
    try:
        audit = AuditLog(
            user_id=user.id if user else None,
            server_id=server_id,
            action=f"docker_container_{action}",
            status="success" if result.get("success") else "failed",
            details=f"Container {name}",
            output_snippet=str(result)[:500],
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        session.add(audit)
        session.commit()
    except Exception:
        pass
    return RedirectResponse(f"/servers/{server_id}/docker", status_code=303)


@router.get("/{server_id}/docker/compose/{project}/file-content", response_class=JSONResponse)
async def get_file_content(
    server_id: int,
    project: str,
    file: str = "compose",
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj:
        raise HTTPException(404)

    if file == "dockerfile":
        if not proj.get("dockerfile_path"):
            raise HTTPException(404, "No Dockerfile for this project")
        content = docker_svc.read_dockerfile(server, proj["dockerfile_path"])
        return {"ok": True, "file": "dockerfile", "content": content}
    else:
        live_files = docker_svc.get_project_live_files(server, proj["path"])
        for key in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            if key in live_files:
                return {"ok": True, "file": key, "content": live_files[key]}
        content = next(iter(live_files.values()), "") if live_files else ""
        return {"ok": True, "file": key, "content": content}
    content = next(iter(live_files.values()), "") if live_files else ""
    return {"ok": True, "file": key, "content": content}


@router.get("/{server_id}/docker/compose/{project}/edit", response_class=HTMLResponse)
async def edit_compose(
    server_id: int,
    project: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj:
        raise HTTPException(404)

    live_files = docker_svc.get_project_live_files(server, proj["path"])
    live_compose = live_files.get("docker-compose.yml") or live_files.get("docker-compose.yaml") or live_files.get("compose.yml") or live_files.get("compose.yaml") or ""
    content = live_compose
    drafts = docker_svc.get_versions(server.id, project, limit=10)

    load_draft_id = request.query_params.get("load_draft")
    editing_version_id = None
    if load_draft_id:
        try:
            dv = next((d for d in drafts if str(d.id) == load_draft_id), None)
            if dv:
                f = json.loads(dv.files)
                content = f.get('Dockerfile') or content
                if dv.is_draft:
                    editing_version_id = dv.id
        except:
            pass

    live_version = None
    live_clean = live_compose.strip() if live_compose else ''
    for d in drafts:
        if not d.is_draft:
            try:
                f = json.loads(d.files or '{}')
                c = f.get('Dockerfile') or ''
                if c.strip() == live_clean:
                    live_version = d
                    break
            except:
                pass

    errors_param = request.query_params.get("errors")
    errors = []
    if errors_param:
        try:
            import json as _json
            errors = sorted(_json.loads(errors_param) or [], key=lambda e: e.get("line", 0))
        except:
            errors = []

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="docker_compose_edit.html",
        context={
            "title": f"Edit {project}",
            "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}),
            "project": project,
            "content": content,
            "user": user,
            "errors": errors,
            "is_dockerfile": False,
            "drafts": drafts,
            "live_version": live_version,
            "editing_version_id": editing_version_id,
        }
    )


@router.get("/{server_id}/docker/compose/{project}/dockerfile/edit", response_class=HTMLResponse)
async def edit_dockerfile(
    server_id: int,
    project: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj or not proj.get("dockerfile_path"):
        raise HTTPException(404, "No Dockerfile path resolved for this project")

    live_content = docker_svc.read_dockerfile(server, proj["dockerfile_path"])
    content = live_content
    all_drafts = docker_svc.get_versions(server.id, project, limit=10)
    drafts = []
    for d in all_drafts:
        try:
            f = json.loads(d.files or '{}')
            if 'Dockerfile' in f:
                drafts.append(d)
        except:
            pass

    load_draft_id = request.query_params.get("load_draft")
    editing_version_id = None
    if load_draft_id:
        try:
            dv = next((d for d in drafts if str(d.id) == load_draft_id), None)
            if dv and dv.is_draft:
                editing_version_id = dv.id
                f = json.loads(dv.files or '{}')
                content = f.get('Dockerfile') or content
        except:
            pass

    live_version = None
    live_clean = live_content.strip() if live_content else ''
    for d in drafts:
        if not d.is_draft:
            try:
                f = json.loads(d.files or '{}')
                c = f.get('Dockerfile') or ''
                if c.strip() == live_clean:
                    live_version = d
                    break
            except:
                pass

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="docker_compose_edit.html",
        context={
            "title": f"Edit Dockerfile - {project}",
            "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}),
            "project": {"name": project, "path": proj["dockerfile_path"]},  # reuse fields
            "content": content,
            "user": user,
            "errors": [],
            "is_dockerfile": True,
            "drafts": df_drafts,
            "live_version": live_version,
            "editing_version_id": editing_version_id,
        }
    )


@router.post("/{server_id}/docker/compose/{project}/dockerfile/save")
async def save_dockerfile(
    server_id: int,
    project: str,
    content: str = Form(...),
    action: str = Form("deploy"),
    editing_version_id: Optional[int] = Form(None),
    via_modal: bool = Form(False),
    request: Request = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj or not proj.get("dockerfile_path"):
        raise HTTPException(404)

    if via_modal:
        return JSONResponse({"ok": False, "message": "Dockerfile editing is temporarily disabled."})

    return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/dockerfile/edit?disabled=1", status_code=303)


@router.get("/{server_id}/docker/new-project", response_class=HTMLResponse)
async def new_docker_project_form(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="new_docker_project.html",
        context={"title": "New Docker Service", "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}), "user": user}
    )


@router.post("/{server_id}/docker/new-project")
async def create_docker_project(
    server_id: int,
    project_name: str = Form(...),
    compose_content: str = Form(...),
    dockerfile_content: str = Form(""),
    git_url: str = Form(""),
    deploy_now: str = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    base_files = {"docker-compose.yml": compose_content}
    if dockerfile_content.strip():
        base_files["Dockerfile"] = dockerfile_content

    ok = docker_svc.create_new_docker_project(server, project_name, base_files, git_url=git_url or None)
    if ok and deploy_now:
        try:
            full = f"{server.docker_base_dir.replace('~', f'/home/{server.ssh_username}')}/{project_name}"
            docker_svc.redeploy_project(server, full, pull=True)
        except:
            pass

    try:
        from datetime import datetime as dt
        dv = docker_svc.save_draft_version(server.id, project_name, base_files, session)
        if ok and deploy_now:
            dv.is_draft = False
            dv.deployed_at = dt.utcnow()
            session.add(dv)
            session.commit()
    except:
        pass

    return RedirectResponse(f"/servers/{server_id}/docker?new_project={project_name}", status_code=303)


@router.post("/{server_id}/docker/compose/{project}/save-draft")
async def save_draft(
    server_id: int,
    project: str,
    content: str = Form(...),
    editing_version_id: Optional[int] = Form(None),
    via_modal: bool = Form(False),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    if via_modal:
        return JSONResponse({"ok": False, "message": "Draft saving temporarily disabled."})

    return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/edit?draft_disabled=1", status_code=303)


@router.post("/{server_id}/docker/compose/{project}/deploy-version")
async def deploy_version_route(
    server_id: int,
    project: str,
    version_id: int = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj:
        raise HTTPException(404)

    ok = docker_svc.deploy_version(server.id, version_id, server, proj["path"], session)
    status = "deployed" if ok else "failed"
    return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/edit?deploy_status={status}", status_code=303)


@router.get("/{server_id}/docker/compose/{project}/rollback/{version_id}")
async def rollback_version(
    server_id: int,
    project: str,
    version_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj:
        raise HTTPException(404)

    ok = docker_svc.deploy_version(server.id, version_id, server, proj["path"], session)
    return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/edit?rollback={ 'ok' if ok else 'fail'}", status_code=303)


@router.post("/{server_id}/docker/compose/{project}/validate", response_class=JSONResponse)
async def validate_compose(
    server_id: int,
    project: str,
    content: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    result = docker_svc.validate_compose_content(content)
    return JSONResponse(result)


@router.post("/{server_id}/docker/compose/{project}/save")
async def save_compose(
    server_id: int,
    project: str,
    content: str = Form(...),
    editing_version_id: Optional[int] = Form(None),
    via_modal: bool = Form(False),
    request: Request = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj:
        raise HTTPException(404)

    if via_modal:
        return JSONResponse({"ok": False, "message": "Compose saving temporarily disabled."})

    return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/edit?compose_disabled=1", status_code=303)


@router.post("/{server_id}/docker/redeploy")
async def redeploy(
    server_id: int,
    project_path: str = Form(...),
    pull: str = Form("true"),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    docker_svc.redeploy_project(server, project_path, pull=(pull == "true"))
    try:
        audit = AuditLog(
            user_id=user.id if user else None,
            server_id=server_id,
            action="docker_redeploy",
            status="success",
            details=f"Project {project_path}",
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        session.add(audit)
        session.commit()
    except Exception:
        pass
    return RedirectResponse(f"/servers/{server_id}/docker", status_code=303)


@router.post("/{server_id}/docker/compose/{action}")
async def compose_project_action(
    server_id: int,
    action: str,
    project_path: str = Form(...),
    service: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    svc = service or None
    res = docker_svc.compose_action(server, project_path, action, service=svc)
    try:
        details = f"Project {project_path}"
        if svc:
            details += f" service={svc}"
        audit = AuditLog(
            user_id=user.id if user else None,
            server_id=server_id,
            action=f"docker_compose_{action}",
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
    return RedirectResponse(f"/servers/{server_id}/docker", status_code=303)


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

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    try:
        import app.services.docker_management as _dm
        if request.query_params.get("nocache"):
            _dm._CACHE.clear()
    except:
        pass

    try:
        interval = max(60, int(request.query_params.get("refresh", "120")))
    except:
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


@router.post("/{server_id}/docker/check-updates")
async def check_updates(
    server_id: int,
    project_path: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    result = docker_svc.check_compose_updates(server, project_path)
    status = "ok" if result.get("success") else "fail"
    try:
        audit = AuditLog(
            user_id=user.id if user else None,
            server_id=server_id,
            action="docker_check-updates",
            status="success",
            details=f"Project {project_path}",
            output_snippet=str(result)[:300],
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        session.add(audit)
        session.commit()
    except Exception:
        pass
    return RedirectResponse(f"/servers/{server_id}/docker?update_check={project_path}&status={status}", status_code=303)


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
        data = {"dangling_images": [], "exited_containers": [], "success": False, "errors": [str(e)[:200]]}

    # Build a small HTML snippet for the fetch().innerHTML
    lines = []
    di = data.get("dangling_images", []) or []
    ec = data.get("exited_containers", []) or []
    if not di and not ec:
        lines.append("<div class='text-zinc-400'>No dangling images or exited containers found.</div>")
    else:
        if di:
            lines.append("<div class='text-amber-400 font-medium'>Dangling images:</div>")
            lines.append("<pre class='whitespace-pre-wrap text-[10px]'>" + "\n".join(di) + "</pre>")
        if ec:
            lines.append("<div class='text-amber-400 font-medium mt-1'>Exited containers:</div>")
            lines.append("<pre class='whitespace-pre-wrap text-[10px]'>" + "\n".join(ec) + "</pre>")
    if data.get("errors"):
        lines.append("<div class='text-red-400 mt-1'>Errors: " + "; ".join(data["errors"]) + "</div>")
    if data.get("success") is False:
        lines.append("<div class='text-xs text-zinc-500'>Command may have partially failed (non-zero exit).</div>")

    html = "\n".join(lines)
    return HTMLResponse(html)


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
            audit = AuditLog(
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
            audit = AuditLog(
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

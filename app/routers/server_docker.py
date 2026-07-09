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
from ..models import Server, AuditLog
from ..services import docker_management as docker_svc
from ..services import docker_inventory as inventory_svc
from .. import templates as templates_mod
from ..security.auth import get_current_user
from ..models import User

router = APIRouter()


def _invalidate_inventory(session: Session, server: Server, background_tasks: Optional[BackgroundTasks] = None):
    """Clear short cache + mark DB inventory stale (+ optional BG refresh)."""
    try:
        inventory_svc.invalidate_after_mutation(session, server, background_tasks)
    except Exception:
        try:
            docker_svc._CACHE.clear()
        except Exception:
            pass


def _parse_files_json(raw: Optional[str]) -> Optional[dict]:
    if not raw or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        out = {}
        for k, v in data.items():
            sk = str(k)
            if sk.startswith("__"):
                continue
            if "/" in sk or "\\" in sk or sk in (".", "..") or not sk:
                continue
            out[sk] = v if isinstance(v, str) else ("" if v is None else str(v))
        return out
    except Exception:
        return None


def _snapshot_for_save(
    server: Server,
    project: str,
    project_path: str,
    *,
    session: Session,
    editing_version_id: Optional[int],
    files_json: Optional[str],
    single_updates: Optional[dict] = None,
) -> dict:
    """Build a multi-file snapshot: live + optional draft base, then apply updates."""
    live = docker_svc.get_project_live_files(server, project_path) or {}
    base = dict(live)
    if editing_version_id:
        try:
            from ..models import DockerVersion

            dv = session.get(DockerVersion, editing_version_id)
            if dv and dv.server_id == server.id and dv.project_name == project:
                base = docker_svc.merge_project_files(base, docker_svc.parse_version_files(dv))
        except Exception:
            pass
    # Prefer full client map when present (multi-tab editor), then apply active-file edit
    client_map = _parse_files_json(files_json)
    merged = docker_svc.merge_project_files(base, client_map or {})
    if single_updates:
        merged = docker_svc.merge_project_files(merged, single_updates)
    return merged


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
        audit = AuditLog(
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

    try:
        _invalidate_inventory(session, server)
    except Exception:
        pass
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
        compose_key = None
        content = ""
        for key in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            if key in live_files:
                compose_key = key
                content = live_files[key]
                break
        if not compose_key and live_files:
            compose_key = next(iter(live_files.keys()))
            content = live_files[compose_key]
        return {"ok": True, "file": compose_key or "docker-compose.yml", "content": content}


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

    try:
        _invalidate_inventory(session, server)
    except Exception:
        pass
    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj:
        raise HTTPException(404)

    live_files = docker_svc.get_project_live_files(server, proj["path"]) or {}
    project_files = dict(live_files)
    live_compose_key = docker_svc.primary_compose_key(live_files)
    live_compose = live_files.get(live_compose_key, "") if live_compose_key else ""

    all_drafts = docker_svc.get_versions(server.id, project, limit=10)
    # Show versions that have any project file (multi-file snapshots)
    drafts = list(all_drafts)

    load_draft_id = request.query_params.get("load_draft")
    editing_version_id = None
    if load_draft_id:
        try:
            dv = next((d for d in drafts if str(d.id) == load_draft_id), None)
            if dv:
                f = docker_svc.parse_version_files(dv)
                # Prefer draft snapshot entirely when loading a version
                if f:
                    project_files = docker_svc.merge_project_files(live_files, f)
                if dv.is_draft:
                    editing_version_id = dv.id
        except Exception:
            pass

    active_file = request.query_params.get("file") or ""
    file_names = docker_svc.sort_project_filenames(list(project_files.keys()))
    if not file_names:
        # Empty project — seed primary compose name from discovery
        seed = (proj.get("compose_file") or "docker-compose.yml").split("/")[-1]
        if seed not in docker_svc.COMPOSE_BASENAMES:
            seed = "docker-compose.yml"
        project_files = {seed: ""}
        file_names = [seed]
    if active_file not in project_files:
        active_file = docker_svc.primary_compose_key(project_files) or file_names[0]
    content = project_files.get(active_file, "")

    live_version = None
    for d in drafts:
        if d.is_draft:
            continue
        try:
            f = docker_svc.parse_version_files(d)
            # Match live when all non-meta host files equal
            host_keys = set(live_files.keys())
            if host_keys and all(
                (f.get(k) or "").strip() == (live_files.get(k) or "").strip() for k in host_keys
            ):
                live_version = d
                break
        except Exception:
            pass

    errors_param = request.query_params.get("errors")
    errors = []
    if errors_param:
        try:
            errors = sorted(json.loads(errors_param) or [], key=lambda e: e.get("line", 0))
        except Exception:
            errors = []

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="docker_compose_edit.html",
        context={
            "title": f"Edit {project}",
            "server": server.model_dump(exclude={"audit_logs", "jobs", "docker_versions"}),
            "project": {"name": project, "path": proj.get("path") or ""},
            "content": content,
            "project_files": project_files,
            "file_names": file_names,
            "active_file": active_file,
            "files_json": json.dumps(project_files, ensure_ascii=False),
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

    try:
        _invalidate_inventory(session, server)
    except Exception:
        pass
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
            "drafts": drafts,
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
    via_modal: str = Form("false"),
    request: Request = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    try:
        _invalidate_inventory(session, server)
    except Exception:
        pass
    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj or not proj.get("dockerfile_path"):
        raise HTTPException(404)

    df_path = proj["dockerfile_path"]
    # Merge Dockerfile into multi-file snapshot so we don't drop compose/override/.env
    files = _snapshot_for_save(
        server,
        project,
        proj["path"],
        session=session,
        editing_version_id=editing_version_id,
        files_json=None,
        single_updates={"Dockerfile": content},
    )
    is_draft_action = (str(action).lower() == "draft")
    is_via_modal = str(via_modal).lower() in ("1", "true", "yes", "on")
    try:
        if is_draft_action:
            # Save draft only (no write to host, no deploy)
            dv = docker_svc.save_draft_version(server.id, project, files, session, update_existing_draft_id=editing_version_id)
            if is_via_modal:
                return JSONResponse({"ok": True, "message": f"Draft v{dv.version} saved (Dockerfile)"})
            return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/dockerfile/edit?saved_draft={dv.version}", status_code=303)
        else:
            # Write to host + record as deployed version
            written, werr = docker_svc.write_dockerfile(server, df_path, content)
            if not written:
                msg = "Failed to write Dockerfile to host (check SSH user can write the file / permissions / ownership)."
                if werr:
                    msg = f"{msg} Detail: {werr}"
                if is_via_modal:
                    return JSONResponse({"ok": False, "message": msg})
                return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/dockerfile/edit?write_failed=1", status_code=303)

            try:
                _invalidate_inventory(session, server)
            except Exception:
                pass

            dv = docker_svc.save_draft_version(server.id, project, files, session, update_existing_draft_id=editing_version_id)
            dv.is_draft = False
            dv.deployed_at = datetime.utcnow()
            session.add(dv)
            session.commit()

            try:
                audit = AuditLog(
                    user_id=user.id if user else None,
                    server_id=server_id,
                    action="docker_dockerfile_save",
                    status="success",
                    details=f"Project {project} Dockerfile saved v{dv.version}",
                    started_at=datetime.utcnow(),
                    finished_at=datetime.utcnow(),
                )
                session.add(audit)
                session.commit()
            except Exception:
                pass

            if is_via_modal:
                return JSONResponse({"ok": True, "message": f"Dockerfile saved v{dv.version}"})
            return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/dockerfile/edit?saved=1", status_code=303)
    except Exception as e:
        if is_via_modal:
            return JSONResponse({"ok": False, "message": str(e)[:120]})
        return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/dockerfile/edit", status_code=303)


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
            from ..services.ssh import docker_base_expanded
            full = f"{docker_base_expanded(server)}/{project_name}"
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
    content: str = Form(""),
    active_file: str = Form(""),
    files_json: Optional[str] = Form(None),
    editing_version_id: Optional[int] = Form(None),
    via_modal: str = Form("false"),
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

    updates = {}
    if active_file and content is not None:
        updates[active_file] = content
    files = _snapshot_for_save(
        server,
        project,
        proj["path"],
        session=session,
        editing_version_id=editing_version_id,
        files_json=files_json,
        single_updates=updates or None,
    )
    is_via_modal = str(via_modal).lower() in ("1", "true", "yes", "on")
    try:
        dv = docker_svc.save_draft_version(server.id, project, files, session, update_existing_draft_id=editing_version_id)
        if is_via_modal:
            return JSONResponse({"ok": True, "message": f"Draft v{dv.version} saved"})
        return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/edit?saved_draft={dv.version}", status_code=303)
    except Exception as e:
        if is_via_modal:
            return JSONResponse({"ok": False, "message": f"Failed to save draft: {str(e)[:80]}"})
        return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/edit?draft_failed=1", status_code=303)


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

    try:
        _invalidate_inventory(session, server)
    except Exception:
        pass
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

    try:
        _invalidate_inventory(session, server)
    except Exception:
        pass
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
    content: str = Form(""),
    active_file: str = Form(""),
    files_json: Optional[str] = Form(None),
    editing_version_id: Optional[int] = Form(None),
    via_modal: str = Form("false"),
    request: Request = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):

    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    try:
        _invalidate_inventory(session, server)
    except Exception:
        pass
    projects = docker_svc.list_compose_projects(server)
    proj = next((p for p in projects if p["name"] == project), None)
    if not proj:
        raise HTTPException(404)

    project_path = proj["path"]
    updates = {}
    if active_file and content is not None:
        updates[active_file] = content
    files = _snapshot_for_save(
        server,
        project,
        project_path,
        session=session,
        editing_version_id=editing_version_id,
        files_json=files_json,
        single_updates=updates or None,
    )
    is_via_modal = str(via_modal).lower() in ("1", "true", "yes", "on")
    try:
        # Write all tracked project files (compose, override, .env, Dockerfile, …)
        written, werr = docker_svc.write_project_files(server, project_path, files)
        if not written:
            msg = "Failed to write project files to host (check SSH user can write the file / permissions / ownership)."
            if werr:
                msg = f"{msg} Detail: {werr}"
            if is_via_modal:
                return JSONResponse({"ok": False, "message": msg})
            return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/edit?write_failed=1", status_code=303)

        try:
            _invalidate_inventory(session, server)
        except Exception:
            pass

        # record as deployed (non-draft) version; update if we were editing a draft
        dv = docker_svc.save_draft_version(server.id, project, files, session, update_existing_draft_id=editing_version_id)
        dv.is_draft = False
        dv.deployed_at = datetime.utcnow()
        session.add(dv)
        session.commit()

        try:
            names = ", ".join(docker_svc.sort_project_filenames(list(docker_svc.files_for_sftp(files).keys())))
            audit = AuditLog(
                user_id=user.id if user else None,
                server_id=server_id,
                action="docker_compose_save",
                status="success",
                details=f"Project {project} saved & deployed v{dv.version} ({names})",
                started_at=datetime.utcnow(),
                finished_at=datetime.utcnow(),
            )
            session.add(audit)
            session.commit()
        except Exception:
            pass

        try:
            docker_svc.redeploy_project(server, project_path, pull=True)
        except Exception:
            pass

        if is_via_modal:
            return JSONResponse({"ok": True, "message": f"Saved & deployed v{dv.version}"})
        return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/edit?saved=1&version={dv.version}", status_code=303)
    except Exception as e:
        if is_via_modal:
            return JSONResponse({"ok": False, "message": str(e)[:120]})
        return RedirectResponse(f"/servers/{server_id}/docker/compose/{project}/edit", status_code=303)


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
        },
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
    status = "updates" if result.get("has_updates") else ("ok" if result.get("success") else "fail")
    # Merge single-project result into fleet summary for UI highlights
    try:
        import os
        proj_name = os.path.basename((project_path or "").rstrip("/")) or project_path
        summary = {}
        if server.container_updates_summary:
            try:
                summary = json.loads(server.container_updates_summary) or {}
            except Exception:
                summary = {}
        projects = list(summary.get("projects") or [])
        details = dict(summary.get("project_details") or {})
        if result.get("has_updates"):
            if proj_name not in projects:
                projects.append(proj_name)
            details[proj_name] = {"images": list(result.get("updated_images") or [])}
        else:
            projects = [p for p in projects if p != proj_name]
            details.pop(proj_name, None)
        server.container_updates_summary = json.dumps({
            "projects": projects,
            "project_details": details,
            "failed": summary.get("failed") or [],
            "checked": summary.get("checked") or [],
        })
        server.container_updates_count = len(projects)
        server.last_container_check_at = datetime.utcnow()
        session.add(server)
        audit = AuditLog(
            user_id=user.id if user else None,
            server_id=server_id,
            action="docker_check-updates",
            status="success" if result.get("success") or result.get("has_updates") else "failed",
            details=f"Project {project_path}",
            output_snippet=str({
                "has_updates": result.get("has_updates"),
                "updated_images": result.get("updated_images"),
            })[:300],
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        session.add(audit)
        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
    from urllib.parse import quote
    return RedirectResponse(
        f"/servers/{server_id}/docker?update_check={quote(project_path, safe='')}&status={status}",
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
        audit = AuditLog(
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

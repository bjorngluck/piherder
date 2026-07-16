"""Docker Compose multi-file editor, drafts, versions (from server_docker.py)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlmodel import Session

from .. import templates as templates_mod
from ..database import get_session
from ..models import Server, User
from ..security.auth import get_current_user, secrets_unlock_active
from ..services import docker_management as docker_svc
from ..services import docker_inventory as inventory_svc
from ..services import env_file_ui
from ..services.audit_write import make_audit_log

router = APIRouter()
logger = logging.getLogger("piherder.docker")


def _invalidate_inventory(
    session: Session, server: Server, background_tasks: Optional[BackgroundTasks] = None
):
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
    """Build a multi-file snapshot: live + optional draft base, then apply updates.

    Client may have redacted .env (********); restore those keys from live so save
    does not wipe host secrets.
    """
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
    # Restore masks against *live* host secrets (not draft), then merge
    if client_map:
        client_map = env_file_ui.restore_project_files_on_save(client_map, live)
    if single_updates:
        single_updates = env_file_ui.restore_project_files_on_save(single_updates, live)
    merged = docker_svc.merge_project_files(base, client_map or {})
    if single_updates:
        merged = docker_svc.merge_project_files(merged, single_updates)
    # Final pass: any remaining masks vs live
    return env_file_ui.restore_project_files_on_save(merged, live)


def _ui_redact_files(
    request: Request,
    user: User,
    session: Session,
    server_id: int,
    project: str,
    files: dict,
) -> tuple:
    """Return (files_for_browser, secrets_revealed, extra_keys)."""
    unlocked = secrets_unlock_active(request, user)
    extra = env_file_ui.extra_secret_keys_for_project(session, server_id, project)
    safe = env_file_ui.redact_project_files_for_ui(
        files, reveal=unlocked, extra_secret_keys=extra
    )
    return safe, unlocked, extra


@router.get("/{server_id}/docker/compose/{project}/file-content", response_class=JSONResponse)
async def get_file_content(
    request: Request,
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
        live_files = docker_svc.get_project_live_files(server, proj["path"]) or {}
        # Optional: ?file=.env or file=env
        want = (file or "compose").strip()
        if want in ("env", ".env") or want.startswith(".env"):
            env_key = ".env" if ".env" in live_files else next(
                (k for k in live_files if env_file_ui.is_env_filename(k)), None
            )
            raw = live_files.get(env_key or ".env", "")
            safe_map, unlocked, _ = _ui_redact_files(
                request, user, session, server_id, project, {env_key or ".env": raw}
            )
            return {
                "ok": True,
                "file": env_key or ".env",
                "content": safe_map.get(env_key or ".env", ""),
                "secrets_revealed": unlocked,
                "secrets_masked": not unlocked,
            }
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

    # Never ship cleartext .env / secrets/* without step-up unlock
    project_files, secrets_revealed, _extra = _ui_redact_files(
        request, user, session, server_id, project, project_files
    )

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

    template_dep = None
    try:
        from ..services.service_templates.deploy import get_deployment_for_project

        template_dep = get_deployment_for_project(session, server_id, project)
    except Exception:
        template_dep = None

    from ..security.auth import SECRETS_UNLOCK_MINUTES

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
            "secrets_revealed": secrets_revealed,
            "user_has_2fa": bool(getattr(user, "totp_enabled", False)),
            "secrets_unlock_minutes": SECRETS_UNLOCK_MINUTES,
            "unlock_error": request.query_params.get("unlock_error"),
            "template_deployment": (
                {
                    "id": template_dep.id,
                    "template_slug": template_dep.template_slug,
                    "config_version": template_dep.config_version,
                    "drift_status": template_dep.drift_status,
                }
                if template_dep
                else None
            ),
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
                audit = make_audit_log(
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
            audit = make_audit_log(
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



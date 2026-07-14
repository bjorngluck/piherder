"""Settings UI: general · fleet · PiHerder backup · API tokens.

HTTP layer only. Persistence: app_settings; archives: herder_backup; fleet apply: update_check_config.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from .. import templates as templates_mod
from ..database import engine, get_session
from ..models import User
from ..security.auth import ROLE_ADMIN, get_admin_user, get_current_user, user_role
from ..services import api_tokens as tok_svc
from ..services import app_settings as app_cfg
from ..services import herder_backup as hb
from ..services import stack_health as stack_svc
from ..services import update_check_config as ucc
from ..services.audit_write import make_audit_log
from ..services.markdown_lite import load_repo_markdown, markdown_to_html
from ..services.scheduler import (
    HERDER_SCHEDULE_JOB_ID,
    STACK_HEALTH_INTERVAL_MIN,
    sync_all_server_cron_jobs,
    sync_herder_backup_schedule,
)

router = APIRouter(tags=["settings"])

_TABS = frozenset({"general", "fleet", "backup", "status", "api"})


def _form_on(value: Optional[str]) -> bool:
    return value in ("1", "on", "true")


def _scopes_from_form(
    scope_read: Optional[str],
    scope_jobs: Optional[str],
    scope_edit: Optional[str],
    scope_feature_backup: Optional[str],
    scope_feature_os: Optional[str],
    scope_feature_docker: Optional[str],
) -> list[str]:
    scopes: list[str] = []
    for flag, scope in (
        (scope_read, "read"),
        (scope_jobs, "jobs"),
        (scope_edit, "edit"),
        (scope_feature_backup, "feature:backup"),
        (scope_feature_os, "feature:os"),
        (scope_feature_docker, "feature:docker"),
    ):
        if _form_on(flag):
            scopes.append(scope)
    return scopes or ["read", "jobs"]


def _settings_url(tab: str = "general", **params) -> str:
    t = (tab or "general").strip().lower()
    if t not in _TABS:
        t = "general"
    q = {"tab": t}
    for k, v in params.items():
        if v is not None:
            q[k] = str(v)
    return f"/herder-backups?{urlencode(q)}"


def _scheduler():
    """Lazy import avoids circular deps with main lifespan."""
    from ..main import HAS_SCHEDULER, scheduler

    return scheduler, HAS_SCHEDULER


@router.get("/herder-backups", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    scheduler, has_sched = _scheduler()
    backups = hb.list_backups()
    cfg = app_cfg.load_settings()
    schedule_status = "disabled"
    next_run = None
    if has_sched and scheduler and cfg.get("schedule_enabled"):
        job = scheduler.get_job(HERDER_SCHEDULE_JOB_ID)
        if job:
            schedule_status = "enabled"
            nr = getattr(job, "next_run_time", None)
            if nr:
                next_run = app_cfg.format_datetime_in_app_tz(nr)

    is_admin = user_role(user) == ROLE_ADMIN
    api_token_rows = []
    api_token_counts = {"active": 0, "revoked": 0, "all": 0}
    api_token_status = tok_svc.normalize_token_list_status(
        request.query_params.get("token_status")
    )
    api_docs_html = ""
    api_meta = None
    if is_admin:
        try:
            api_token_counts = tok_svc.count_api_tokens_by_status(session)
            api_token_rows = []
            for t in tok_svc.list_api_tokens(session, status=api_token_status):
                d = tok_svc.token_public_dict(t)
                d["last_used_display"] = (
                    app_cfg.format_datetime_in_app_tz(t.last_used_at)
                    if t.last_used_at
                    else None
                )
                d["created_display"] = (
                    app_cfg.format_datetime_in_app_tz(t.created_at)
                    if t.created_at
                    else None
                )
                d["revoked_display"] = (
                    app_cfg.format_datetime_in_app_tz(t.revoked_at)
                    if t.revoked_at
                    else None
                )
                api_token_rows.append(d)
        except Exception:
            api_token_rows = []
            api_token_counts = {"active": 0, "revoked": 0, "all": 0}
        try:
            api_docs_html = markdown_to_html(load_repo_markdown("docs/API.md"))
        except Exception as e:
            api_docs_html = f'<p class="text-sm text-muted">Could not load API docs: {e}</p>'
        try:
            api_meta = tok_svc.api_meta_dict()
        except Exception:
            api_meta = None

    tab = (request.query_params.get("tab") or "general").strip().lower()
    if tab not in _TABS:
        tab = "general"
    if tab == "api" and not is_admin:
        tab = "general"
    if tab == "status" and not is_admin:
        tab = "general"
    qp = request.query_params
    if (
        qp.get("token_created")
        or qp.get("token_revoked")
        or qp.get("token_updated")
        or qp.get("token_rotated")
    ):
        tab = "api" if is_admin else tab
    if qp.get("backup_ok") or qp.get("restored") or qp.get("deleted"):
        tab = "backup"
    if qp.get("update_checks_saved"):
        tab = "fleet"
    if qp.get("security_saved"):
        tab = "general"
    if qp.get("stack_checked"):
        tab = "status" if is_admin else tab

    stack_report = None
    if is_admin:
        stack_report = stack_svc.load_last_report()

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="herder_backups.html",
        context={
            "title": "Settings",
            "user": user,
            "backups": backups,
            "herder_backup_dir": str(hb.HERDER_BACKUP_DIR),
            "herder_config": cfg,
            "tz_choices": app_cfg.get_available_timezones(),
            "schedule_status": schedule_status,
            "schedule_next_run": next_run,
            "api_tokens": api_token_rows,
            "api_token_status": api_token_status,
            "api_token_counts": api_token_counts,
            "is_admin": is_admin,
            "settings_tab": tab,
            "api_docs_html": api_docs_html,
            "api_meta": api_meta,
            "new_api_token_secret": qp.get("token_secret"),
            "new_api_token_name": qp.get("token_name"),
            "stack_report": stack_report,
            "stack_health_interval_min": STACK_HEALTH_INTERVAL_MIN,
        },
    )


@router.post("/herder-backups/status/check")
async def stack_status_check_now(
    user: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Admin: run stack health probes now and refresh Status tab.

    Fast path only (no full backup-tree ``du``). Folder breakdown is lazy via
    ``GET /herder-backups/status/backup-usage``.
    """
    scheduler, has_sched = _scheduler()
    try:
        report = await run_in_threadpool(
            lambda: stack_svc.run_stack_health_check(
                session,
                scheduler=scheduler,
                has_scheduler=has_sched,
                notify=True,
            )
        )
        overall = (report or {}).get("overall") or "unknown"
        return RedirectResponse(
            _settings_url("status", stack_checked="1", overall=overall),
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            _settings_url("status", stack_error=str(e)[:120]),
            status_code=303,
        )


@router.get("/herder-backups/status/backup-usage")
async def stack_status_backup_usage(
    user: User = Depends(get_admin_user),
):
    """Admin: expensive backup-tree size + top-level host folders (lazy Status UI)."""
    try:
        data = await run_in_threadpool(stack_svc.collect_backup_tree_usage)
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(e)[:200],
                "children": [],
                "tree_bytes": None,
            },
        )


class ApiTokenTestBody(BaseModel):
    """Plaintext secret for one-shot admin test (shown only at create/rotate)."""

    token: str = Field(..., min_length=8, max_length=200)


@router.post("/herder-backups/api-tokens/test")
async def test_api_token(
    request: Request,
    body: ApiTokenTestBody,
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    """Validate a freshly shown API secret (admin session). Does not require `read` scope.

    Checks hash / revoked / expiry and whether *this browser’s* client IP would
    pass the token allowlist. Updates last_used_at on success.
    """
    peer = request.client.host if request.client else None
    client_ip = tok_svc.extract_client_ip(dict(request.headers), peer)
    result = tok_svc.diagnose_plaintext_token(
        session,
        body.token,
        client_ip=client_ip,
        touch_last_used=True,
    )
    status = 200 if result.get("ok") else 400
    if result.get("error") == "invalid_or_revoked":
        status = 401
    return JSONResponse(status_code=status, content=result)


@router.post("/herder-backups/api-tokens")
async def create_api_token_form(
    name: str = Form(...),
    scope_read: Optional[str] = Form(None),
    scope_jobs: Optional[str] = Form(None),
    scope_edit: Optional[str] = Form(None),
    scope_feature_backup: Optional[str] = Form(None),
    scope_feature_os: Optional[str] = Form(None),
    scope_feature_docker: Optional[str] = Form(None),
    allowed_cidrs: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    scopes = _scopes_from_form(
        scope_read,
        scope_jobs,
        scope_edit,
        scope_feature_backup,
        scope_feature_os,
        scope_feature_docker,
    )
    row, plain = tok_svc.create_api_token(
        session,
        name=name,
        created_by=user,
        scopes=scopes,
        allowed_cidrs=allowed_cidrs or None,
    )
    try:
        session.add(
            make_audit_log(
                user_id=user.id,
                action="api_token_created",
                status="success",
                details=f"Token #{row.id} {row.name!r}",
                finished_at=datetime.utcnow(),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
    return RedirectResponse(
        _settings_url(
            "api",
            token_created="1",
            token_name=row.name,
            token_secret=plain,
            api_panel="tokens",
            token_status="active",
        ),
        status_code=303,
    )


@router.post("/herder-backups/api-tokens/{token_id}/update")
async def update_api_token_form(
    token_id: int,
    name: str = Form(...),
    scope_read: Optional[str] = Form(None),
    scope_jobs: Optional[str] = Form(None),
    scope_edit: Optional[str] = Form(None),
    scope_feature_backup: Optional[str] = Form(None),
    scope_feature_os: Optional[str] = Form(None),
    scope_feature_docker: Optional[str] = Form(None),
    allowed_cidrs: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    row = tok_svc.get_api_token(session, token_id)
    if not row:
        return RedirectResponse(
            _settings_url("api", error="Token not found", api_panel="tokens"),
            status_code=303,
        )
    try:
        tok_svc.update_api_token(
            session,
            row,
            name=name,
            scopes=_scopes_from_form(
                scope_read,
                scope_jobs,
                scope_edit,
                scope_feature_backup,
                scope_feature_os,
                scope_feature_docker,
            ),
            allowed_cidrs=allowed_cidrs or "",
            update_cidrs=True,
        )
    except ValueError as e:
        return RedirectResponse(
            _settings_url("api", error=str(e)[:120], api_panel="tokens"),
            status_code=303,
        )
    try:
        session.add(
            make_audit_log(
                user_id=user.id,
                action="api_token_updated",
                status="success",
                details=f"Token #{row.id} {row.name!r}",
                finished_at=datetime.utcnow(),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
    return RedirectResponse(
        _settings_url(
            "api",
            token_updated="1",
            token_name=row.name,
            api_panel="tokens",
            token_status="active",
        ),
        status_code=303,
    )


@router.post("/herder-backups/api-tokens/{token_id}/rotate")
async def rotate_api_token_form(
    token_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    row = tok_svc.get_api_token(session, token_id)
    if not row:
        return RedirectResponse(
            _settings_url("api", error="Token not found", api_panel="tokens"),
            status_code=303,
        )
    try:
        row, plain = tok_svc.rotate_api_token(session, row)
    except ValueError as e:
        return RedirectResponse(
            _settings_url("api", error=str(e)[:120], api_panel="tokens"),
            status_code=303,
        )
    try:
        session.add(
            make_audit_log(
                user_id=user.id,
                action="api_token_rotated",
                status="success",
                details=f"Token #{row.id} {row.name!r}",
                finished_at=datetime.utcnow(),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
    return RedirectResponse(
        _settings_url(
            "api",
            token_rotated="1",
            token_name=row.name,
            token_secret=plain,
            api_panel="tokens",
            token_status="active",
        ),
        status_code=303,
    )


@router.post("/herder-backups/api-tokens/{token_id}/revoke")
async def revoke_api_token_form(
    token_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    row = tok_svc.get_api_token(session, token_id)
    if row:
        tok_svc.revoke_api_token(session, row)
        try:
            session.add(
                make_audit_log(
                    user_id=user.id,
                    action="api_token_revoked",
                    status="success",
                    details=f"Token #{row.id} {row.name!r}",
                    finished_at=datetime.utcnow(),
                )
            )
            session.commit()
        except Exception:
            session.rollback()
    # Soft-revoke keeps the row — land on Revoked filter for traceability
    return RedirectResponse(
        _settings_url(
            "api",
            token_revoked="1",
            api_panel="tokens",
            token_status="revoked",
        ),
        status_code=303,
    )


@router.post("/herder-backups/run")
async def trigger_herder_backup(
    backup_mode: str = Form("config_only"),
    user: User = Depends(get_current_user),
):
    mode = backup_mode if backup_mode in ("config_only", "full") else "config_only"
    include_audit = mode == "full"
    config_only = mode != "full"
    with next(get_session()) as s:
        audit = make_audit_log(
            user_id=user.id,
            server_id=None,
            action="herder_backup",
            status="running",
            details=f"Manual self-backup triggered ({mode})",
            started_at=datetime.utcnow(),
        )
        s.add(audit)
        s.commit()
        s.refresh(audit)
        try:
            path = hb.create_herder_backup(
                include_audit=include_audit, config_only=config_only
            )
            audit.status = "success"
            audit.output_snippet = json.dumps({"path": str(path), "mode": mode})
            audit.finished_at = datetime.utcnow()
            s.add(audit)
            s.commit()
            return RedirectResponse(
                _settings_url("backup", backup_ok="1", file=path.name),
                status_code=303,
            )
        except Exception as e:
            audit.status = "failed"
            audit.output_snippet = str(e)[:2000]
            audit.finished_at = datetime.utcnow()
            s.add(audit)
            s.commit()
            return RedirectResponse(
                _settings_url("backup", error=str(e)[:120]), status_code=303
            )


@router.post("/herder-backups/restore")
async def restore_herder_backup(
    archive: str = Form(""),
    restore_file: UploadFile = File(None),
    restore_audit: Optional[str] = Form(None),
    dry_run: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
):
    tmp_path = None
    try:
        archive_to_use = archive
        if restore_file and restore_file.filename:
            orig = Path(restore_file.filename).name
            ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            if not orig.endswith((".tar.gz", ".tgz", ".tar")):
                safe_name = f".upload-piherder-{ts}-restore.tar.gz"
            else:
                safe_name = f".upload-{orig}"
            tmp_path = Path("/tmp") / safe_name
            with tmp_path.open("wb") as f:
                shutil.copyfileobj(restore_file.file, f)
            archive_to_use = str(tmp_path)

        if not archive_to_use:
            raise ValueError("Provide a server path or upload a file")

        preview = _form_on(dry_run)
        res = hb.restore_herder_backup(
            archive_to_use,
            restore_audit=_form_on(restore_audit),
            dry_run=preview,
        )

        with next(get_session()) as s:
            s.add(
                make_audit_log(
                    user_id=user.id,
                    server_id=None,
                    action="herder_restore",
                    status="success",
                    details=json.dumps(res),
                    output_snippet=json.dumps(res),
                    started_at=datetime.utcnow(),
                    finished_at=datetime.utcnow(),
                )
            )
            s.commit()

        servers_n = (
            res.get("would_restore_servers") if preview else res.get("restored_servers", 0)
        )
        audit_n = (
            res.get("would_restore_audit") if preview else res.get("restored_audit", 0)
        )
        return RedirectResponse(
            _settings_url(
                "backup",
                restored="1",
                dry=str(int(preview)),
                servers=str(servers_n),
                audit=str(audit_n),
            ),
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            _settings_url("backup", error=str(e)[:120]), status_code=303
        )
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


@router.get("/herder-backups/download")
async def download_herder_backup(
    path: str = "",
    name: str = "",
    user: User = Depends(get_current_user),
):
    roots = list(hb.archive_dir_candidates())
    if name:
        p = next((r / name for r in roots if (r / name).exists()), None)
        if p is None:
            raise HTTPException(404)
    else:
        p = Path(path)
    if not p.exists() or not any(str(p).startswith(str(r)) for r in roots):
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)


@router.post("/herder-backups/config")
async def save_backup_schedule(
    keep: int = Form(10),
    schedule_mode: str = Form("config_only"),
    schedule_enabled: Optional[str] = Form(None),
    schedule_cron: str = Form("0 3 * * *"),
    user: User = Depends(get_current_user),
):
    enabled = _form_on(schedule_enabled)
    cron = (schedule_cron or "").strip() or "0 3 * * *"
    if enabled:
        try:
            app_cfg.validate_cron_expression(cron)
        except ValueError as e:
            return RedirectResponse(
                _settings_url("backup", error=str(e)[:120]), status_code=303
            )
    try:
        app_cfg.save_settings(
            {
                "keep": max(1, min(100, keep)),
                "schedule_mode": schedule_mode
                if schedule_mode in ("config_only", "full")
                else "config_only",
                "schedule_enabled": enabled,
                "schedule_cron": cron,
            }
        )
        sched, has = _scheduler()
        sync_herder_backup_schedule(sched, has)
    except Exception as e:
        return RedirectResponse(
            _settings_url("backup", error=str(e)[:120]), status_code=303
        )
    return RedirectResponse(_settings_url("backup", config_saved="1"), status_code=303)


@router.post("/herder-backups/security")
async def save_security_policy(
    force_2fa: Optional[str] = Form(None),
    template_require_2fa: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
):
    if user_role(user) != ROLE_ADMIN:
        raise HTTPException(403, "Admin role required")
    try:
        app_cfg.save_settings(
            {
                "force_2fa": _form_on(force_2fa),
                "template_require_2fa": _form_on(template_require_2fa),
            }
        )
    except Exception as e:
        return RedirectResponse(
            _settings_url("general", error=str(e)[:120]), status_code=303
        )
    return RedirectResponse(
        _settings_url("general", security_saved="1"), status_code=303
    )


@router.post("/herder-backups/update-checks")
async def save_update_check_defaults(
    os_check_global_enabled: Optional[str] = Form(None),
    os_check_cron: str = Form("0 0 * * *"),
    container_check_global_enabled: Optional[str] = Form(None),
    container_check_cron: str = Form("0 0 * * *"),
    update_check_jitter: Optional[str] = Form(None),
    apply_to_all: Optional[str] = Form(None),
    enable_feature_flags: Optional[str] = Form(None),
    enable_backups: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
):
    os_on = _form_on(os_check_global_enabled)
    cont_on = _form_on(container_check_global_enabled)
    jitter = _form_on(update_check_jitter)
    do_apply = _form_on(apply_to_all)
    os_cron = (os_check_cron or "").strip() or "0 0 * * *"
    cont_cron = (container_check_cron or "").strip() or "0 0 * * *"
    try:
        if os_on:
            app_cfg.validate_cron_expression(os_cron)
        if cont_on:
            app_cfg.validate_cron_expression(cont_cron)
    except ValueError as e:
        return RedirectResponse(
            _settings_url("fleet", error=str(e)[:120]), status_code=303
        )

    try:
        app_cfg.save_settings(
            {
                "os_check_global_enabled": os_on,
                "os_check_cron": os_cron,
                "container_check_global_enabled": cont_on,
                "container_check_cron": cont_cron,
                "update_check_jitter": jitter,
            }
        )
        applied = {
            "os_applied": 0,
            "container_applied": 0,
            "servers_total": 0,
            "flags_os": 0,
            "flags_container": 0,
            "flags_backup": 0,
        }
        if do_apply:
            with Session(engine) as db:
                applied = ucc.apply_global_update_checks_to_all(
                    db,
                    os_enabled=os_on,
                    os_cron=os_cron,
                    container_enabled=cont_on,
                    container_cron=cont_cron,
                    jitter=jitter,
                    only_patch_enabled=False,
                    enable_feature_flags=_form_on(enable_feature_flags),
                    enable_backups=_form_on(enable_backups),
                )
            sched, has = _scheduler()
            sync_all_server_cron_jobs(sched, has)
    except Exception as e:
        return RedirectResponse(
            _settings_url("fleet", error=str(e)[:120]), status_code=303
        )

    return RedirectResponse(
        _settings_url(
            "fleet",
            update_checks_saved="1",
            os=str(applied.get("os_applied", 0)),
            cont=str(applied.get("container_applied", 0)),
            total=str(applied.get("servers_total", 0)),
            flags_os=str(applied.get("flags_os", 0)),
            flags_cont=str(applied.get("flags_container", 0)),
            flags_bak=str(applied.get("flags_backup", 0)),
            applied="1" if do_apply else "0",
        ),
        status_code=303,
    )


@router.post("/herder-backups/timezone")
async def save_timezone(
    timezone: str = Form("UTC"),
    user: User = Depends(get_current_user),
):
    try:
        app_cfg.set_app_timezone(timezone)
        sched, has = _scheduler()
        sync_herder_backup_schedule(sched, has)
    except Exception as e:
        return RedirectResponse(
            _settings_url("general", error=str(e)[:120]), status_code=303
        )
    return RedirectResponse(_settings_url("general", config_saved="1"), status_code=303)


@router.post("/herder-backups/delete")
async def delete_herder_backup(
    name: str = Form(...),
    user: User = Depends(get_current_user),
):
    deleted = False
    for root in hb.archive_dir_candidates():
        p = root / name
        if p.exists() and str(p).startswith(str(root)) and p.suffix == ".gz":
            try:
                p.unlink()
                deleted = True
            except Exception:
                pass
            break
    return RedirectResponse(
        _settings_url("backup", deleted="1")
        if deleted
        else _settings_url("backup", error="delete_failed"),
        status_code=303,
    )

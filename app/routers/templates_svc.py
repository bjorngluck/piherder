"""Service template catalog + deploy wizard (v0.4 Phase 1)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from .. import templates as templates_mod
from ..database import get_session
from ..models import AuditLog, User
from ..security.auth import (
    SECRETS_UNLOCK_COOKIE,
    SECRETS_UNLOCK_MINUTES,
    consume_backup_code,
    create_secrets_unlock_token,
    decrypt_totp_secret,
    get_current_user,
    get_operator_user,
    rate_limit_auth,
    role_at_least,
    secrets_unlock_active,
    verify_totp_code,
    ROLE_OPERATOR,
    ROLE_VIEWER,
)
from ..services import app_settings as app_cfg
from ..services.service_templates import (
    TemplateError,
    apply_last_known_config,
    apply_template_to_host,
    blank_editor_form,
    build_definition_from_editor,
    check_deployment_drift,
    definition_to_editor_form,
    delete_template,
    get_deployment,
    get_template_definition,
    get_template_row,
    host_picker_rows,
    import_template_from_zip_bytes,
    list_catalog,
    list_deployments_for_server,
    matching_backup_sources_for_deployment,
    migrate_host_env_into_deployment,
    preview_template,
    public_vars_excluding_volume_meta,
    redeploy_desired_state,
    save_template_definition,
    volume_fields_for_ui,
)
from ..services.service_templates.editor import (
    apply_harden_env_to_form,
    apply_scan_vars_to_form,
    preserve_secret_defaults_on_save,
    redact_form_secrets,
    redact_secret_variable_dicts,
)
from ..services.service_templates.schema import definition_from_storage_json
from ..services.service_templates.from_host import (
    list_host_projects_for_picker,
    pull_project_as_editor_form,
)
from ..models import Server  # used by from-host picker
from ..services.service_templates.deploy import decrypt_deployment_secrets
from ..services.service_templates.schema import redact_files_for_ui

logger = logging.getLogger(__name__)
router = APIRouter(tags=["templates"])


def _audit(
    session: Session,
    user: User,
    action: str,
    *,
    server_id: Optional[int] = None,
    details: str = "",
    status: str = "success",
) -> None:
    try:
        from ..services.audit_write import make_audit_log

        session.add(
            make_audit_log(
                user_id=user.id,
                server_id=server_id,
                action=action,
                status=status,
                details=(details or "")[:2000],
                started_at=datetime.utcnow(),
                finished_at=datetime.utcnow(),
            )
        )
        session.commit()
    except Exception as e:
        logger.debug("audit skip: %s", e)
        session.rollback()


def _redirect(path: str, **params) -> RedirectResponse:
    if params:
        path = f"{path}?{urlencode({k: v for k, v in params.items() if v is not None})}"
    return RedirectResponse(path, status_code=303)


def _template_require_2fa() -> bool:
    return bool(app_cfg.load_settings().get("template_require_2fa"))


def _user_has_2fa(user: User) -> bool:
    return bool(getattr(user, "totp_enabled", False))


def _secrets_revealed(request: Request, user: User) -> bool:
    """Cleartext secrets only after step-up TOTP (not merely because 2FA is enabled)."""
    return secrets_unlock_active(request, user)


def _check_secrets_2fa(user: User) -> None:
    """Account must have TOTP enabled before any secret cleartext / secret edit."""
    if not _user_has_2fa(user):
        raise HTTPException(
            403,
            "Viewing secrets requires 2FA. Enable TOTP under Account, then use View secrets.",
        )


def _check_secrets_unlocked(request: Request, user: User) -> None:
    """Require recent step-up unlock cookie (after TOTP re-entry)."""
    _check_secrets_2fa(user)
    if not _secrets_revealed(request, user):
        raise HTTPException(
            403,
            "Enter your 2FA code under View secrets to unlock cleartext (expires after "
            f"{SECRETS_UNLOCK_MINUTES} minutes).",
        )


def _check_template_2fa(user: User) -> None:
    """Optional policy: require 2FA for template deploy / redeploy."""
    if _template_require_2fa() and not _user_has_2fa(user):
        raise HTTPException(
            403,
            "Template deploy requires 2FA (Settings → Security). Enable TOTP under Account, "
            "or turn off that policy.",
        )


def _safe_return_to(path: Optional[str], default: str = "/templates") -> str:
    """Only allow same-app relative redirects (open-redirect safe)."""
    if not path:
        return default
    path = str(path).strip()
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        return default
    # Templates + compose editor unlock return paths
    if path.startswith("/templates") or path.startswith("/servers/"):
        # Only docker compose edit under /servers/
        if path.startswith("/servers/") and "/docker/compose/" not in path:
            return default
        return path
    return default


def _client_ip(request: Request) -> Optional[str]:
    from ..services.request_ip import client_ip_from_request

    return client_ip_from_request(request)


def _set_secrets_unlock_cookie(response: RedirectResponse, user: User) -> None:
    from ..security.auth import cookie_secure

    token = create_secrets_unlock_token(user.id)
    response.set_cookie(
        SECRETS_UNLOCK_COOKIE,
        token,
        httponly=True,
        max_age=SECRETS_UNLOCK_MINUTES * 60,
        samesite="lax",
        path="/",
        secure=cookie_secure(),
    )


def _clear_secrets_unlock_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(SECRETS_UNLOCK_COOKIE, path="/")


def _secrets_ui_context(request: Request, user: User) -> dict:
    return {
        "user_has_2fa": _user_has_2fa(user),
        "secrets_revealed": _secrets_revealed(request, user),
        "secrets_unlock_minutes": SECRETS_UNLOCK_MINUTES,
    }


@router.get("/templates", response_class=HTMLResponse)
async def templates_list(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    msg: Optional[str] = None,
    error: Optional[str] = None,
):
    items = list_catalog(session)
    by_cat: dict = {}
    by_src: dict = {}
    vars_n = 0
    for t in items:
        c = str(t.get("category") or "other")
        s = str(t.get("source") or "user")
        by_cat[c] = by_cat.get(c, 0) + 1
        by_src[s] = by_src.get(s, 0) + 1
        try:
            vars_n += int(t.get("var_count") or 0)
        except Exception:
            pass
    ranked_cat = sorted(by_cat.items(), key=lambda kv: (-kv[1], kv[0]))
    catalog_pulse = {
        "health": "ok",
        "primary": len(items),
        "primary_label": "templates",
        "bar": [
            {
                "n": n or 0.001,
                "cls": "ops-bar--ok" if i == 0 else ("ops-bar--run" if i == 1 else "ops-bar--mute"),
                "title": f"{k}: {n}",
            }
            for i, (k, n) in enumerate(ranked_cat[:4])
        ]
        or [{"n": 1, "cls": "ops-bar--mute"}],
        "line1": [
            {"n": len(items), "l": "total", "cls": "text-accent"},
            {
                "n": by_src.get("user", 0) + by_src.get("custom", 0),
                "l": "custom",
                "cls": "",
            },
            {
                "n": by_src.get("starter", 0) + by_src.get("builtin", 0),
                "l": "starter",
                "cls": "",
            },
            {"n": vars_n, "l": "settings", "cls": ""},
        ],
        "line2": [
            {
                "n": n,
                # Short labels that still read as full category names
                "l": {
                    "observability": "observe",
                    "monitoring": "monitor",
                    "Proxy TLS": "proxy tls",
                    "dns": "dns",
                    "proxy": "proxy",
                    "other": "other",
                }.get(k, (k[:10] if len(k) > 10 else k)),
                "full": k,
                "cls": "",
            }
            for k, n in ranked_cat[:4]
        ]
        or [{"n": 0, "l": "none", "cls": ""}],
        "caption": "Catalog size · categories",
    }
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="templates_list.html",
        context={
            "user": user,
            "title": "Service templates",
            "items": items,
            "msg": msg,
            "error": error,
            "catalog_pulse": catalog_pulse,
            "can_mutate": role_at_least(user, ROLE_OPERATOR),
        },
    )


@router.post("/templates/import")
async def templates_import(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    file: UploadFile = File(...),
):
    try:
        data = await file.read()
        row = import_template_from_zip_bytes(session, data)
        _audit(
            session,
            user,
            "template.import",
            details=f"slug={row.slug} version={row.version}",
        )
        return _redirect("/templates", msg=f"Imported template {row.slug}")
    except TemplateError as e:
        return _redirect("/templates", error=str(e)[:200])
    except Exception as e:
        logger.exception("template import")
        return _redirect("/templates", error=str(e)[:200])


@router.post("/templates/secrets/unlock")
async def secrets_unlock(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    code: str = Form(""),
    return_to: str = Form("/templates"),
):
    """
    Step-up 2FA: re-enter TOTP (even if already used at login), then grant a short-lived
    cookie that allows decrypting secrets from the DB for the UI.
    """
    dest = _safe_return_to(return_to)
    ip = _client_ip(request) or "unknown"
    if not rate_limit_auth(f"secrets_unlock:{user.id}:{ip}", max_attempts=20, window_seconds=300):
        return _redirect(dest, unlock_error="Too many unlock attempts. Wait a few minutes.")

    if not _user_has_2fa(user) or not getattr(user, "totp_secret_encrypted", None):
        return _redirect(
            dest,
            unlock_error="Enable 2FA under Account before viewing secrets.",
        )

    code = (code or "").strip()
    ok = False
    if code:
        try:
            secret = decrypt_totp_secret(user.totp_secret_encrypted)
            if verify_totp_code(secret, code):
                ok = True
            elif consume_backup_code(session, user.id, code):
                ok = True
        except Exception:
            ok = False

    if not ok:
        return _redirect(dest, unlock_error="Invalid 2FA code. Try again.")

    _audit(session, user, "template.secrets_unlock", details=f"return_to={dest[:200]}")
    response = RedirectResponse(dest, status_code=303)
    _set_secrets_unlock_cookie(response, user)
    return response


@router.post("/templates/secrets/lock")
async def secrets_lock(
    request: Request,
    user: User = Depends(get_operator_user),
    return_to: str = Form("/templates"),
):
    """Hide cleartext secrets again (clear step-up cookie)."""
    dest = _safe_return_to(return_to)
    response = RedirectResponse(dest, status_code=303)
    _clear_secrets_unlock_cookie(response)
    return response


def _editor_from_form(form) -> dict:
    use_ds = str(form.get("use_docker_secrets") or "").lower() in ("1", "true", "on", "yes")
    return {
        "slug": str(form.get("slug") or "").strip(),
        "name": str(form.get("name") or "").strip(),
        "description": str(form.get("description") or "").strip(),
        "category": str(form.get("category") or "other").strip() or "other",
        "version": str(form.get("version") or "1.0.0").strip() or "1.0.0",
        "compose_content": str(form.get("compose_content") or ""),
        "env_content": str(form.get("env_content") or ""),
        "variables_json": str(form.get("variables_json") or "[]"),
        "checklist_json": str(form.get("checklist_json") or "[]"),
        "use_docker_secrets": use_ds,
    }


def _editor_response(
    request: Request,
    user: User,
    *,
    form: dict,
    is_new: bool,
    slug: str = "",
    template_id=None,
    error: Optional[str] = None,
    msg: Optional[str] = None,
    tool_messages: Optional[list] = None,
    status_code: int = 200,
    skip_redact: bool = False,
):
    title = "New template" if is_new else f"Edit · {form.get('name') or slug}"
    reveal = _secrets_revealed(request, user)
    safe_form = form if skip_redact else redact_form_secrets(form, reveal=reveal)
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="template_edit.html",
        context={
            "user": user,
            "title": title,
            "form": safe_form,
            "is_new": is_new,
            "error": error,
            "msg": msg,
            "tool_messages": tool_messages or [],
            "template_id": template_id,
            "slug": slug,
            **_secrets_ui_context(request, user),
        },
        status_code=status_code,
    )


def _deploy_form_values(definition, request: Request, user: User) -> dict:
    """Prefill deploy form; never prefill secret defaults without step-up unlock."""
    reveal = _secrets_revealed(request, user)
    out = {}
    for v in definition.variables:
        if v.secret and not reveal:
            out[v.name] = ""
        else:
            out[v.name] = v.default or ""
        if getattr(v, "type", None) == "volume":
            out[f"{v.name}__mode"] = getattr(v, "volume_default_mode", None) or "named"
            out[f"{v.name}__source"] = v.default or ""
        if getattr(v, "type", None) == "boolean":
            # Normalize for select/checkbox checked state
            from ..services.service_templates.schema import coerce_boolean_value

            out[v.name] = coerce_boolean_value(v, v.default or "")
    return out


def _collect_deploy_values(definition, form) -> dict:
    """Read var_* fields from the deploy wizard form (incl. volume mode)."""
    values = {}
    for var in definition.variables:
        key = f"var_{var.name}"
        if var.type == "boolean":
            # select or checkbox: missing → false
            if key in form:
                values[var.name] = str(form.get(key) or "")
            elif var.name in form:
                values[var.name] = str(form.get(var.name) or "")
            else:
                values[var.name] = ""
            continue
        if var.type == "volume":
            mode_key = f"var_{var.name}_mode"
            if mode_key in form:
                values[f"{var.name}__mode"] = str(form.get(mode_key) or "named")
            elif f"{var.name}__mode" in form:
                values[f"{var.name}__mode"] = str(form.get(f"{var.name}__mode") or "named")
            else:
                values[f"{var.name}__mode"] = var.volume_default_mode or "named"
            if key in form:
                values[var.name] = str(form.get(key) or "")
                values[f"{var.name}__source"] = str(form.get(key) or "")
            elif var.name in form:
                values[var.name] = str(form.get(var.name) or "")
                values[f"{var.name}__source"] = str(form.get(var.name) or "")
            continue
        if key in form:
            values[var.name] = str(form.get(key) or "")
        elif var.name in form:
            values[var.name] = str(form.get(var.name) or "")
    if form.get("var_PROJECT_NAME"):
        values["PROJECT_NAME"] = str(form.get("var_PROJECT_NAME"))
    return values


@router.get("/templates/new", response_class=HTMLResponse)
async def template_new_form(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    error: Optional[str] = None,
):
    return _editor_response(
        request, user, form=blank_editor_form(), is_new=True, error=error
    )


@router.get("/templates/from-host", response_class=HTMLResponse)
async def template_from_host_form(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    error: Optional[str] = None,
):
    hosts = host_picker_rows(session)
    # String keys so Alpine/JSON never mismatch int vs str
    host_projects: dict = {}
    for h in hosts:
        sid = str(h["id"])
        srv = session.get(Server, h["id"])
        host_projects[sid] = list_host_projects_for_picker(srv) if srv else []
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="template_from_host.html",
        context={
            "user": user,
            "title": "Template from existing service",
            "hosts": hosts,
            "host_projects_json": json.dumps(host_projects, ensure_ascii=False),
            "error": error,
        },
    )


@router.post("/templates/from-host")
async def template_from_host_pull(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Pull without strict Form(...) so a missing field becomes a friendly error, not 422."""
    form = await request.form()
    try:
        server_id = int(str(form.get("server_id") or "0"))
    except ValueError:
        server_id = 0
    project_name = str(
        form.get("project_name") or form.get("project") or ""
    ).strip()
    auto_harden = str(form.get("auto_harden") or "").lower() in (
        "1",
        "true",
        "on",
        "yes",
    )

    if not server_id:
        return _redirect("/templates/from-host", error="Select a host")
    if not project_name:
        return _redirect(
            "/templates/from-host",
            error="Select or type a project / stack name",
        )

    server = session.get(Server, server_id)
    if not server:
        return _redirect("/templates/from-host", error="Server not found")
    try:
        result = pull_project_as_editor_form(
            server,
            project_name,
            auto_harden_env=auto_harden,
        )
        _audit(
            session,
            user,
            "template.from_host",
            server_id=server.id,
            details=f"project={project_name}",
        )
        return _editor_response(
            request,
            user,
            form=result["form"],
            is_new=True,
            msg="Loaded from host — review, edit, then Save.",
            tool_messages=result.get("messages") or [],
        )
    except TemplateError as e:
        return _redirect("/templates/from-host", error=str(e)[:200])
    except Exception as e:
        logger.exception("from-host pull")
        return _redirect("/templates/from-host", error=str(e)[:200])


@router.post("/templates/new")
async def template_create(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    form = await request.form()
    data = _editor_from_form(form)
    action = str(form.get("editor_action") or "save").strip()

    reveal = _secrets_revealed(request, user)
    if action in ("scan_vars", "harden_env"):
        try:
            if action == "scan_vars":
                data, msgs = apply_scan_vars_to_form(data, reveal_secrets=reveal)
            else:
                data, msgs = apply_harden_env_to_form(data, reveal_secrets=reveal)
            data["use_docker_secrets"] = False
            return _editor_response(
                request, user, form=data, is_new=True, tool_messages=msgs, msg="Tool applied — review and Save."
            )
        except TemplateError as e:
            return _editor_response(
                request, user, form=data, is_new=True, error=str(e), status_code=400
            )

    try:
        # Drop UI-only keys
        data.pop("_extracted_secret_keys", None)
        definition = build_definition_from_editor(
            **{k: v for k, v in data.items() if k != "use_docker_secrets"},
            use_docker_secrets=False,
            source="user",
        )
        existing = get_template_row(session, slug=definition.slug)
        if existing:
            raise TemplateError(f"Slug {definition.slug!r} already exists — edit that template instead")
        row = save_template_definition(session, definition, mark_user=True)
        _audit(session, user, "template.create", details=f"slug={row.slug}")
        return _redirect("/templates", msg=f"Saved template {row.slug}")
    except TemplateError as e:
        return _editor_response(
            request,
            user,
            form={**blank_editor_form(), **data},
            is_new=True,
            error=str(e),
            status_code=400,
        )


@router.get("/templates/{slug}/edit", response_class=HTMLResponse)
async def template_edit_form(
    request: Request,
    slug: str,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    error: Optional[str] = None,
    msg: Optional[str] = None,
    unlock_error: Optional[str] = None,
):
    try:
        definition = get_template_definition(session, slug=slug, allow_disabled=True)
    except TemplateError as e:
        return _redirect("/templates", error=str(e))
    row = get_template_row(session, slug=slug)
    form = definition_to_editor_form(
        definition, reveal_secrets=_secrets_revealed(request, user)
    )
    return _editor_response(
        request,
        user,
        form=form,
        is_new=False,
        slug=slug,
        template_id=row.id if row else None,
        error=error or unlock_error,
        msg=msg,
    )


@router.post("/templates/{slug}/edit")
async def template_save_edit(
    request: Request,
    slug: str,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    template_id: Optional[int] = Form(None),
):
    form = await request.form()
    data = _editor_from_form(form)
    action = str(form.get("editor_action") or "save").strip()
    row = get_template_row(session, slug=slug)
    if not row and template_id:
        row = get_template_row(session, template_id=template_id)
    if not row and action == "save":
        return _redirect("/templates", error="Template not found")

    reveal = _secrets_revealed(request, user)
    if action in ("scan_vars", "harden_env"):
        try:
            if action == "scan_vars":
                data, msgs = apply_scan_vars_to_form(data, reveal_secrets=reveal)
            else:
                data, msgs = apply_harden_env_to_form(data, reveal_secrets=reveal)
            data["use_docker_secrets"] = False
            return _editor_response(
                request,
                user,
                form=data,
                is_new=False,
                slug=slug,
                template_id=row.id if row else None,
                tool_messages=msgs,
                msg="Tool applied — review and Save.",
            )
        except TemplateError as e:
            return _editor_response(
                request,
                user,
                form=data,
                is_new=False,
                slug=slug,
                template_id=row.id if row else None,
                error=str(e),
                status_code=400,
            )

    if not row:
        return _redirect("/templates", error="Template not found")
    try:
        # Preserve secret defaults if UI redacted them (no 2FA empty fields)
        try:
            prev_def = (
                definition_from_storage_json(row.definition_json, source=row.source or "user")
                if row.definition_json
                else None
            )
            submitted = json.loads(data.get("variables_json") or "[]")
            if isinstance(submitted, list) and prev_def:
                merged = preserve_secret_defaults_on_save(
                    submitted, [v.to_dict() for v in prev_def.variables]
                )
                data["variables_json"] = json.dumps(merged)
        except Exception:
            pass
        definition = build_definition_from_editor(
            **{k: v for k, v in data.items() if k != "use_docker_secrets"},
            use_docker_secrets=False,
            source="user",
        )
        saved = save_template_definition(
            session, definition, template_id=row.id, mark_user=True
        )
        _audit(
            session,
            user,
            "template.edit",
            details=f"slug={saved.slug} was={slug} version={saved.version}",
        )
        return _redirect(f"/templates/{saved.slug}/edit", msg="Template saved")
    except TemplateError as e:
        return _editor_response(
            request,
            user,
            form=data,
            is_new=False,
            slug=slug,
            template_id=row.id,
            error=str(e),
            status_code=400,
        )


@router.post("/templates/{slug}/delete")
async def template_delete(
    request: Request,
    slug: str,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    try:
        delete_template(session, slug=slug)
        _audit(session, user, "template.delete", details=f"slug={slug}")
        return _redirect("/templates", msg=f"Deleted template {slug}")
    except TemplateError as e:
        return _redirect("/templates", error=str(e)[:200])


@router.get("/templates/{slug}", response_class=HTMLResponse)
async def template_detail(
    request: Request,
    slug: str,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """View settings / variables before deploy or edit."""
    try:
        definition = get_template_definition(session, slug=slug, allow_disabled=True)
    except TemplateError as e:
        return _redirect("/templates", error=str(e))
    row = get_template_row(session, slug=slug)
    reveal = _secrets_revealed(request, user)
    variables = redact_secret_variable_dicts(
        [v.to_dict() for v in definition.variables], reveal=reveal
    )
    # For detail page without reveal: show placeholder not empty for secrets
    has_secret_vars = False
    for v in variables:
        if v.get("secret"):
            has_secret_vars = True
            if not reveal:
                v["default_display"] = "••••••••"
            else:
                v["default_display"] = v.get("default") or "—"
        else:
            v["default_display"] = v.get("default") or "—"
    public_def = definition.to_public_dict()
    # Never embed cleartext secret defaults in template context (page source / JSON)
    public_def["variables"] = variables
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="template_detail.html",
        context={
            "user": user,
            "title": definition.name,
            "definition": public_def,
            "variables": variables,
            "checklist": [{"title": c.title, "body": c.body} for c in definition.checklist],
            "files": list(definition.file_contents.keys()),
            "source": row.source if row else definition.source,
            "can_mutate": role_at_least(user, ROLE_OPERATOR),
            "slug": slug,
            "has_secret_vars": has_secret_vars,
            "unlock_error": request.query_params.get("unlock_error"),
            **_secrets_ui_context(request, user),
        },
    )


# Static path segments before /templates/{slug}/… so "deployments" is not a slug.
@router.get("/templates/deployments/{deployment_id}", response_class=HTMLResponse)
async def deployment_detail(
    request: Request,
    deployment_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    msg: Optional[str] = None,
    error: Optional[str] = None,
):
    dep = get_deployment(session, deployment_id)
    if not dep:
        raise HTTPException(404, "Deployment not found")
    server = session.get(Server, dep.server_id)
    public = {}
    try:
        public = json.loads(dep.variables_json or "{}")
    except Exception:
        pass
    files = {}
    try:
        files = json.loads(dep.files_json or "{}")
    except Exception:
        pass

    secrets_map: dict = {}
    secrets_visible = False
    secret_key_names: list = []
    full_secrets: dict = {}
    unlocked = _secrets_revealed(request, user)
    can_op = role_at_least(user, ROLE_OPERATOR)

    # Decrypt server-side for masking / (only if unlocked) UI reveal.
    # Never send cleartext to the browser without step-up unlock cookie.
    if dep.secrets_encrypted and can_op:
        try:
            full_secrets = decrypt_deployment_secrets(dep)
            secret_key_names = list(full_secrets.keys())
            if unlocked:
                secrets_map = full_secrets
                secrets_visible = True
        except Exception:
            secret_key_names = []
            full_secrets = {}

    # Strict: even legacy files_json with secrets/* or cleartext .env is scrubbed
    # unless step-up unlock is active.
    masked_files = redact_files_for_ui(
        files,
        secret_values=full_secrets,
        secret_keys=secret_key_names,
        reveal=secrets_visible,
    )
    if secrets_visible and full_secrets:
        # When unlocked, rebuild host-ready preview from stored structure + secrets
        from ..services.service_templates.deploy import merge_secrets_into_env_files

        masked_files = merge_secrets_into_env_files(masked_files, full_secrets)

    checklist = []
    definition = None
    try:
        if dep.template_slug:
            definition = get_template_definition(session, slug=dep.template_slug)
            # Never interpolate secrets into checklist without unlock
            values = {**public, "PROJECT_NAME": dep.project_name}
            if secrets_visible:
                values = {**values, **secrets_map}
            from ..services.service_templates.schema import render_checklist

            checklist = render_checklist(definition, values)
    except Exception:
        definition = None

    volume_rows = volume_fields_for_ui(public, definition)
    public_simple = public_vars_excluding_volume_meta(public, definition)
    backup_matches = []
    if server:
        try:
            backup_matches = matching_backup_sources_for_deployment(server, dep)
        except Exception:
            backup_matches = []

    unlock_error = request.query_params.get("unlock_error")
    drift_detail = request.query_params.get("drift_detail")

    from ..services import dns_fabric as fabric
    from ..services.app_settings import load_settings

    dns_record = fabric.find_service_for_deployment(session, deployment_id)
    dns_target_name = None
    if dns_record:
        t = session.get(Server, dns_record.target_server_id)
        dns_target_name = fabric.normalize_fqdn(t.dns_name) if t else None
    base = (load_settings().get("dns_base_domain") or "").strip()
    dns_plan = None
    try:
        dns_plan = fabric.resolve_service_dns_plan(
            session,
            backend_server_id=dep.server_id,
            docker_project=dep.project_name,
            stack_deployment_id=dep.id,
            base_domain=base,
        )
    except Exception:
        dns_plan = None
    dns_suggest = (dns_plan or {}).get("fqdn") or (
        f"{(dep.project_name or 'app').lower().replace('_', '-')}.{base}" if base else ""
    )

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="template_deployment.html",
        context={
            "user": user,
            "title": f"{dep.project_name} · V{dep.config_version}",
            "dep": dep,
            "server": server,
            "public": public_simple,
            "volume_rows": volume_rows,
            "secret_keys": secret_key_names,
            "secrets_map": secrets_map if secrets_visible else {},
            "secrets_visible": secrets_visible,
            "files_masked": masked_files,
            "checklist": checklist,
            "backup_matches": backup_matches,
            "msg": msg,
            "error": error,
            "unlock_error": unlock_error,
            "drift_detail": drift_detail,
            "can_mutate": can_op,
            "template_require_2fa": _template_require_2fa(),
            "dns_record": dns_record,
            "dns_plan": dns_plan,
            "dns_target_name": dns_target_name,
            "dns_suggest": dns_suggest,
            **_secrets_ui_context(request, user),
        },
    )


@router.post("/templates/deployments/{deployment_id}/redeploy")
async def deployment_redeploy(
    request: Request,
    deployment_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    form = await request.form()
    # Changing secrets requires step-up unlock; deploy policy also applies
    has_secret_fields = any(str(k).startswith("sec_") for k in form.keys())
    try:
        if has_secret_fields:
            _check_secrets_unlocked(request, user)
        _check_template_2fa(user)
    except HTTPException as e:
        return _redirect(f"/templates/deployments/{deployment_id}", error=e.detail)

    dep = get_deployment(session, deployment_id)
    if not dep:
        raise HTTPException(404)
    server = session.get(Server, dep.server_id)
    if not server:
        raise HTTPException(404, "Server missing")

    updated_public = {}
    updated_secrets = {}
    for k, v in form.items():
        sk = str(k)
        if sk.startswith("pub_"):
            updated_public[sk[4:]] = str(v)
        elif sk.startswith("vol_") and sk.endswith("__mode"):
            # vol_NAME__mode
            name = sk[4 : -len("__mode")]
            updated_public[f"{name}__mode"] = str(v)
        elif sk.startswith("vol_") and sk.endswith("__source"):
            name = sk[4 : -len("__source")]
            updated_public[f"{name}__source"] = str(v)
            updated_public[name] = str(v)  # merge_variable_values also accepts raw source
        elif sk.startswith("sec_"):
            val = str(v)
            if val and val != "********":
                updated_secrets[sk[4:]] = val

    try:
        result = redeploy_desired_state(
            session,
            server=server,
            deployment=dep,
            updated_public=updated_public or None,
            updated_secrets=updated_secrets or None,
            deploy_now=True,
        )
    except TemplateError as e:
        _audit(
            session,
            user,
            "template.redeploy",
            server_id=server.id,
            details=f"deployment={deployment_id} error={e}",
            status="failed",
        )
        return _redirect(f"/templates/deployments/{deployment_id}", error=str(e)[:200])
    except Exception as e:
        _audit(
            session,
            user,
            "template.redeploy",
            server_id=server.id,
            details=f"deployment={deployment_id} error={e}",
            status="failed",
        )
        return _redirect(
            f"/templates/deployments/{deployment_id}",
            error=f"Redeploy failed: {str(e)[:180]}",
        )

    _audit(
        session,
        user,
        "template.redeploy",
        server_id=server.id,
        details=f"deployment={deployment_id} config_v={result.get('config_version')}",
    )
    rd = result.get("redeploy") or {}
    ok_host = rd.get("success") if isinstance(rd, dict) else True
    msg = f"Redeployed as V{result.get('config_version')}"
    if ok_host is False:
        msg += " — compose up reported failure (see Audit / Docker)"
    if server:
        msg += f". Open Docker · {dep.project_name}."
    return _redirect(
        f"/templates/deployments/{result['deployment_id']}",
        msg=msg[:240],
    )


@router.post("/templates/deployments/{deployment_id}/check-drift")
async def deployment_check_drift(
    deployment_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    dep = get_deployment(session, deployment_id)
    if not dep:
        raise HTTPException(404)
    from ..models import Server

    server = session.get(Server, dep.server_id)
    if not server:
        raise HTTPException(404, "Server missing")
    try:
        result = check_deployment_drift(session, server=server, deployment=dep)
    except Exception as e:
        return _redirect(
            f"/templates/deployments/{deployment_id}",
            error=f"Drift check failed: {str(e)[:180]}",
        )
    _audit(
        session,
        user,
        "template.drift_check",
        server_id=server.id,
        details=(
            f"deployment={deployment_id} status={result.get('status')} "
            f"diffs={len(result.get('diffs') or [])}"
        ),
        status="success" if result.get("status") != "unknown" else "failed",
    )
    st = result.get("status") or "unknown"
    detail = ""
    diffs = result.get("diffs") or []
    if diffs:
        detail = "; ".join(
            f"{d.get('file')}: {d.get('detail')}" for d in diffs[:6]
        )[:180]
    if st == "in_sync":
        msg = "Host matches desired state (in sync)"
    elif st == "drifted":
        msg = f"Drift detected ({len(diffs)} file(s))"
    else:
        msg = f"Drift unknown: {result.get('error') or result.get('reason') or 'check failed'}"
    return _redirect(
        f"/templates/deployments/{deployment_id}",
        msg=msg,
        drift_detail=detail or None,
    )


@router.post("/templates/deployments/{deployment_id}/migrate-env")
async def deployment_migrate_env(
    request: Request,
    deployment_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Pull host .env secrets into PiHerder encrypted store (B — .env migrate UX)."""
    try:
        _check_secrets_2fa(user)
        # Storing secrets requires account 2FA; step-up unlock optional but preferred
        if _user_has_2fa(user) and not _secrets_revealed(request, user):
            # Allow migrate without unlock (import only into DB encrypted store)
            pass
    except HTTPException as e:
        return _redirect(f"/templates/deployments/{deployment_id}", error=str(e.detail)[:200])
    dep = get_deployment(session, deployment_id)
    if not dep:
        raise HTTPException(404)
    server = session.get(Server, dep.server_id)
    if not server:
        raise HTTPException(404, "Server missing")
    try:
        result = migrate_host_env_into_deployment(
            session, server=server, deployment=dep
        )
    except TemplateError as e:
        return _redirect(f"/templates/deployments/{deployment_id}", error=str(e)[:200])
    except Exception as e:
        return _redirect(
            f"/templates/deployments/{deployment_id}",
            error=f"Env migrate failed: {str(e)[:180]}",
        )
    _audit(
        session,
        user,
        "template.env_migrate",
        server_id=server.id,
        details=(
            f"deployment={deployment_id} secrets={result.get('imported_secrets')} "
            f"public={result.get('imported_public')}"
        ),
    )
    n_s = len(result.get("imported_secrets") or [])
    n_p = len(result.get("imported_public") or [])
    return _redirect(
        f"/templates/deployments/{deployment_id}",
        msg=f"Imported from host .env: {n_s} secret(s), {n_p} public key(s). Redeploy to write host if needed.",
    )


@router.post("/templates/deployments/{deployment_id}/apply-config")
async def deployment_apply_last_known(
    deployment_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Write last known desired state to host (C — after wipe / DR)."""
    dep = get_deployment(session, deployment_id)
    if not dep:
        raise HTTPException(404)
    from ..models import Server

    server = session.get(Server, dep.server_id)
    if not server:
        raise HTTPException(404, "Server missing")
    try:
        _check_template_2fa(user)
        result = apply_last_known_config(
            session, server=server, deployment=dep, deploy_now=True
        )
    except TemplateError as e:
        _audit(
            session,
            user,
            "template.apply_config",
            server_id=server.id,
            details=f"deployment={deployment_id} error={e}",
            status="failed",
        )
        return _redirect(f"/templates/deployments/{deployment_id}", error=str(e)[:200])
    except HTTPException as e:
        return _redirect(f"/templates/deployments/{deployment_id}", error=str(e.detail)[:200])
    except Exception as e:
        return _redirect(
            f"/templates/deployments/{deployment_id}",
            error=f"Apply failed: {str(e)[:180]}",
        )
    _audit(
        session,
        user,
        "template.apply_config",
        server_id=server.id,
        details=f"deployment={deployment_id} config_v={result.get('config_version')}",
    )
    return _redirect(
        f"/templates/deployments/{result.get('deployment_id', deployment_id)}",
        msg=(
            f"Applied last known config V{result.get('config_version')} to host. "
            f"Restore volume data from Backups if needed."
        )[:240],
    )


@router.get("/templates/{slug}/deploy", response_class=HTMLResponse)
async def template_deploy_wizard(
    request: Request,
    slug: str,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    step: str = "configure",
    error: Optional[str] = None,
):
    if not role_at_least(user, ROLE_VIEWER):
        raise HTTPException(403)
    try:
        definition = get_template_definition(session, slug=slug)
    except TemplateError as e:
        return _redirect("/templates", error=str(e))

    hosts = host_picker_rows(session)
    reveal = _secrets_revealed(request, user)
    safe_vars = redact_secret_variable_dicts(
        [v.to_dict() for v in definition.variables], reveal=reveal
    )
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="template_deploy.html",
        context={
            "user": user,
            "title": f"Deploy · {definition.name}",
            "definition": definition.to_public_dict(),
            "variables": safe_vars,
            "hosts": hosts,
            "step": step,
            "error": error,
            "can_mutate": role_at_least(user, ROLE_OPERATOR),
            "template_require_2fa": _template_require_2fa(),
            "preview": None,
            "selected_server_id": None,
            "form_values": _deploy_form_values(definition, request, user),
            **_secrets_ui_context(request, user),
        },
    )


@router.post("/templates/{slug}/preview", response_class=HTMLResponse)
async def template_preview(
    request: Request,
    slug: str,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    server_id: int = Form(...),
    deploy_now: Optional[str] = Form("1"),
):
    form = await request.form()
    try:
        definition = get_template_definition(session, slug=slug)
    except TemplateError as e:
        return _redirect("/templates", error=str(e))

    values = _collect_deploy_values(definition, form)

    try:
        prev = preview_template(session, slug=slug, values=values, auto_generate=True)
    except TemplateError as e:
        hosts = host_picker_rows(session)
        reveal = _secrets_revealed(request, user)
        safe_form_values = dict(values)
        if not reveal:
            for var in definition.variables:
                if var.secret:
                    safe_form_values[var.name] = ""
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="template_deploy.html",
            context={
                "user": user,
                "title": f"Deploy · {definition.name}",
                "definition": definition.to_public_dict(),
                "variables": redact_secret_variable_dicts(
                    [v.to_dict() for v in definition.variables], reveal=reveal
                ),
                "hosts": hosts,
                "step": "configure",
                "error": str(e),
                "can_mutate": True,
                "template_require_2fa": _template_require_2fa(),
                "preview": None,
                "selected_server_id": server_id,
                "form_values": safe_form_values,
                **_secrets_ui_context(request, user),
            },
            status_code=400,
        )

    hosts = host_picker_rows(session)
    # Stash values for confirm (secrets included) — only in form hidden fields on confirm page
    stash = {
        "values": {**prev["values_public"], **prev["secrets"]},
        "server_id": server_id,
        "deploy_now": bool(deploy_now),
    }
    # Browser preview must never include raw secrets or files_raw
    safe_preview = {
        "project_name": prev["project_name"],
        "secret_keys": prev["secret_keys"],
        "files_masked": prev["files_masked"],
        "checklist": prev["checklist"],
        "values_public": prev["values_public"],
    }
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="template_deploy.html",
        context={
            "user": user,
            "title": f"Preview · {definition.name}",
            "definition": definition.to_public_dict(),
            "variables": redact_secret_variable_dicts(
                [v.to_dict() for v in definition.variables], reveal=False
            ),
            "hosts": hosts,
            "step": "preview",
            "error": None,
            "can_mutate": True,
            "template_require_2fa": _template_require_2fa(),
            "preview": safe_preview,
            "selected_server_id": server_id,
            "form_values": prev["values_public"],
            "stash_json": json.dumps(stash),
            "deploy_now": bool(deploy_now),
            **_secrets_ui_context(request, user),
        },
    )


@router.post("/templates/{slug}/confirm")
async def template_confirm_deploy(
    request: Request,
    slug: str,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    stash_json: str = Form(...),
):
    try:
        _check_template_2fa(user)
    except HTTPException as e:
        return _redirect(f"/templates/{slug}/deploy", error=e.detail)

    try:
        stash = json.loads(stash_json)
    except Exception:
        return _redirect(f"/templates/{slug}/deploy", error="Invalid deploy state; start again")

    values = stash.get("values") or {}
    server_id = int(stash.get("server_id") or 0)
    deploy_now = bool(stash.get("deploy_now", True))
    server = session.get(Server, server_id)
    if not server:
        return _redirect(f"/templates/{slug}/deploy", error="Server not found")

    try:
        result = apply_template_to_host(
            session,
            server=server,
            template_slug=slug,
            values=values,
            deploy_now=deploy_now,
            auto_generate=False,
        )
    except TemplateError as e:
        _audit(
            session,
            user,
            "template.deploy",
            server_id=server_id,
            details=f"slug={slug} error={e}",
            status="failed",
        )
        return _redirect(f"/templates/{slug}/deploy", error=str(e)[:200])
    except Exception as e:
        logger.exception("template deploy")
        _audit(
            session,
            user,
            "template.deploy",
            server_id=server_id,
            details=f"slug={slug} error={e}",
            status="failed",
        )
        return _redirect(f"/templates/{slug}/deploy", error=str(e)[:200])

    secret_keys = ",".join(result.get("secret_keys") or [])
    _audit(
        session,
        user,
        "template.deploy",
        server_id=server_id,
        details=(
            f"slug={slug} project={result.get('project_name')} "
            f"config_v={result.get('config_version')} secrets=[{secret_keys}]"
        ),
        status="success",
    )

    dep_id = result.get("deployment_id")
    return _redirect(
        f"/templates/deployments/{dep_id}",
        msg=f"Deployed {result.get('project_name')} as V{result.get('config_version')}",
    )


@router.get("/servers/{server_id}/deployments", response_class=HTMLResponse)
async def server_deployments(
    request: Request,
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    deps = list_deployments_for_server(session, server_id)
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_deployments.html",
        context={
            "user": user,
            "title": f"Deployments · {server.name}",
            "server": server,
            "deployments": deps,
        },
    )

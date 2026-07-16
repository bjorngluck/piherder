"""Service template catalog routes — router lives in templates_common."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from .. import templates as templates_mod
from ..database import get_session
from ..models import AuditLog, Server, User
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
    blank_editor_form,
    build_definition_from_editor,
    definition_to_editor_form,
    delete_template,
    get_template_definition,
    get_template_row,
    host_picker_rows,
    import_template_from_zip_bytes,
    list_catalog,
    preview_template,
    public_vars_excluding_volume_meta,
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
from .templates_common import (
    router,
    _audit,
    _redirect,
    _template_require_2fa,
    _user_has_2fa,
    _secrets_revealed,
    _check_secrets_2fa,
    _check_secrets_unlocked,
    _check_template_2fa,
    _safe_return_to,
    _client_ip,
    _set_secrets_unlock_cookie,
    _clear_secrets_unlock_cookie,
    _secrets_ui_context,
    _editor_from_form,
    _editor_response,
    _deploy_form_values,
    _collect_deploy_values,
)

logger = logging.getLogger(__name__)

from . import templates_deploy as _templates_deploy  # noqa: F401

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

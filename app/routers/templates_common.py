"""Shared template router + helpers (secrets unlock, editor forms)."""
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
    secrets_unlock_minutes,
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


def _user_has_2fa(session: Session, user: User) -> bool:
    """True when TOTP and/or at least one passkey is enrolled."""
    from ..services import webauthn_svc as wa_svc

    return wa_svc.user_has_2fa(session, user)


def _secrets_revealed(request: Request, user: User) -> bool:
    """Cleartext secrets only after step-up 2FA (not merely because 2FA is enabled)."""
    return secrets_unlock_active(request, user)


def _check_secrets_2fa(session: Session, user: User) -> None:
    """Account must have a second factor before any secret cleartext / secret edit."""
    if not _user_has_2fa(session, user):
        raise HTTPException(
            403,
            "Viewing secrets requires 2FA. Enable a passkey or TOTP under Account, then use View secrets.",
        )


def _check_secrets_unlocked(request: Request, session: Session, user: User) -> None:
    """Require recent step-up unlock cookie (after TOTP or passkey re-entry)."""
    _check_secrets_2fa(session, user)
    if not _secrets_revealed(request, user):
        raise HTTPException(
            403,
            "Complete step-up 2FA under View secrets (passkey or authenticator code) to unlock "
            f"cleartext (expires after {secrets_unlock_minutes()} minutes).",
        )


def _check_template_2fa(session: Session, user: User) -> None:
    """Optional policy: require 2FA for template deploy / redeploy."""
    if _template_require_2fa() and not _user_has_2fa(session, user):
        raise HTTPException(
            403,
            "Template deploy requires 2FA (Settings → Security). Enable a passkey or TOTP under Account, "
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


def _set_secrets_unlock_cookie(response, user: User) -> None:
    """Set secrets unlock cookie on any Response (redirect or JSON)."""
    from ..security.auth import cookie_auth_kwargs

    token = create_secrets_unlock_token(user.id)
    response.set_cookie(
        SECRETS_UNLOCK_COOKIE,
        token,
        **cookie_auth_kwargs(max_age=secrets_unlock_minutes() * 60),
    )


def _clear_secrets_unlock_cookie(response) -> None:
    response.delete_cookie(SECRETS_UNLOCK_COOKIE, path="/")


def _secrets_ui_context(request: Request, session: Session, user: User) -> dict:
    from ..services import webauthn_svc as wa_svc

    has_totp = wa_svc.totp_active(user)
    has_pk = False
    try:
        if getattr(user, "id", None) is not None:
            has_pk = wa_svc.has_passkeys(session, int(user.id))
    except Exception:
        has_pk = False
    return {
        "user_has_2fa": has_totp or has_pk,
        "has_totp": has_totp,
        "has_passkeys": has_pk,
        "secrets_revealed": _secrets_revealed(request, user),
        "secrets_unlock_minutes": secrets_unlock_minutes(),
    }


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
        "extra_files_json": str(form.get("extra_files_json") or "[]"),
        "variables_json": str(form.get("variables_json") or "[]"),
        "checklist_json": str(form.get("checklist_json") or "[]"),
        "use_docker_secrets": use_ds,
    }


def _editor_response(
    request: Request,
    session: Session,
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
            **_secrets_ui_context(request, session, user),
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



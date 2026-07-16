"""Deployment detail, redeploy, drift, apply, deploy wizard (shared router)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from .. import templates as templates_mod
from ..database import get_session
from ..models import AuditLog, Server, User
from ..security.auth import (
    get_current_user,
    get_operator_user,
    role_at_least,
    ROLE_OPERATOR,
    ROLE_VIEWER,
)
from ..services import app_settings as app_cfg
from ..services.service_templates import (
    TemplateError,
    apply_last_known_config,
    apply_template_to_host,
    check_deployment_drift,
    get_deployment,
    get_template_definition,
    get_template_row,
    host_picker_rows,
    list_catalog,
    list_deployments_for_server,
    matching_backup_sources_for_deployment,
    migrate_host_env_into_deployment,
    preview_template,
    public_vars_excluding_volume_meta,
    redeploy_desired_state,
    volume_fields_for_ui,
)
from ..services.service_templates.deploy import decrypt_deployment_secrets
from ..services.service_templates.schema import redact_files_for_ui
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
    _secrets_ui_context,
    _deploy_form_values,
    _collect_deploy_values,
)

logger = logging.getLogger(__name__)

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

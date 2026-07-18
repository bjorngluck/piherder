"""Nginx Proxy Manager detail helpers + bind / cert routes."""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from .. import templates as templates_mod
from ..database import get_session
from ..models import Integration, Server, User
from ..security.auth import get_current_user, get_operator_user
from ..services.integrations import npm as npm_mod
from ..services.integrations import registry as reg
from .integrations_common import router, _audit, _redirect, _can_mutate

logger = logging.getLogger(__name__)

async def render_npm_detail(request, session, user, integration: Integration):
    status = reg.parse_last_status(integration)
    tab = (request.query_params.get("tab") or "hosts").strip().lower()
    servers = list(session.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
    server_names = {s.id: s.name for s in servers}
    bindings = reg.list_bindings(
        session, integration_id=integration.id, role=reg.ROLE_PROXY_HOST
    )
    bind_by_ext: dict[str, dict] = {}
    for b in bindings:
        bind_by_ext[str(b.external_id)] = {
            "id": b.id,
            "server_id": b.server_id,
            "server_name": server_names.get(b.server_id, f"#{b.server_id}"),
            "docker_project": b.docker_project or "",
            "docker_container": b.docker_container or "",
            "external_label": b.external_label or "",
        }
    proxy_hosts = status.get("proxy_hosts") or []
    certificates = status.get("certificates") or []
    docker_options: dict[int, list] = {}
    for s in servers:
        docker_options[s.id] = reg.docker_inventory_options(session, s.id)
    from ..services import certificates as cert_svc

    managed = [
        cert_svc.public_cert_dict(c)
        for c in cert_svc.list_certificates(session)
        if c.source_integration_id == integration.id
    ]
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_npm_detail.html",
        context={
            "title": integration.name,
            "user": user,
            "integration": integration,
            "status": status,
            "tab": tab,
            "proxy_hosts": proxy_hosts,
            "certificates": certificates,
            "servers": servers,
            "bindings": bindings,
            "bind_by_ext": bind_by_ext,
            # String keys so JS dockerOpts[serverId] works (JSON object keys)
            "docker_options_json": json.dumps(
                {str(k): v for k, v in docker_options.items()}
            ),
            "managed_certs": managed,
            "open_url": npm_mod.open_npm_url(integration.base_url),
            "can_mutate": _can_mutate(user),
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


@router.post("/integrations/{integration_id}/npm/bind")
async def npm_bind_proxy(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    external_id: str = Form(...),
    server_id: int = Form(...),
    docker_project: str = Form(""),
    docker_container: str = Form(""),
):
    integration = reg.get_integration(session, integration_id)
    if not integration or integration.type != reg.TYPE_NPM:
        raise HTTPException(404)
    st = reg.parse_last_status(integration)
    host = None
    for h in st.get("proxy_hosts") or []:
        if str(h.get("id")) == str(external_id).strip():
            host = h
            break
    label = (host or {}).get("label") or str(external_id)
    try:
        reg.set_binding(
            session,
            integration_id=integration_id,
            server_id=server_id,
            external_id=str(external_id).strip(),
            role=reg.ROLE_PROXY_HOST,
            docker_project=docker_project or None,
            docker_container=docker_container or None,
            external_label=label,
            external_meta=host or {"id": external_id},
            last_state="up",
        )
        _audit(
            session,
            user,
            "npm_proxy_bound",
            server_id=server_id,
            details=f"proxy_host={external_id}",
        )
        return _redirect(
            f"/integrations/{integration_id}", tab="hosts", msg="bound"
        )
    except ValueError as e:
        return _redirect(
            f"/integrations/{integration_id}",
            tab="hosts",
            error="bind_failed",
            detail=str(e)[:200],
        )


@router.post("/integrations/{integration_id}/npm/unbind")
async def npm_unbind_proxy(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    binding_id: int = Form(...),
):
    ok = reg.clear_binding(
        session, integration_id=integration_id, server_id=0, binding_id=binding_id
    )
    if ok:
        _audit(session, user, "npm_proxy_unbound", details=f"binding={binding_id}")
    return _redirect(f"/integrations/{integration_id}", tab="hosts", msg="unbound")


@router.post("/integrations/{integration_id}/npm/pull-cert")
async def npm_pull_cert(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    cert_id: str = Form(...),
    name: str = Form(""),
    auto_renew: Optional[str] = Form("on"),
):
    from ..services import certificates as cert_svc

    integration = reg.get_integration(session, integration_id)
    if not integration or integration.type != reg.TYPE_NPM:
        raise HTTPException(404)
    try:
        row = cert_svc.pull_from_npm(
            session,
            integration,
            cert_id,
            name=name,
            auto_renew=auto_renew in ("on", "1", "true"),
        )
        _audit(
            session,
            user,
            "cert_pulled_npm",
            details=f"npm_id={cert_id} cert={row.id} name={row.name}",
        )
        return _redirect(f"/certificates/{row.id}", msg="pulled", setup="map")
    except Exception as e:
        logger.exception("npm pull cert")
        return _redirect(
            f"/integrations/{integration_id}",
            tab="certs",
            error="pull_failed",
            detail=str(e)[:200],
        )


@router.post("/integrations/{integration_id}/npm/renew-cert")
async def npm_renew_cert(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    cert_id: str = Form(...),
):
    from ..services import certificates as cert_svc

    integration = reg.get_integration(session, integration_id)
    if not integration or integration.type != reg.TYPE_NPM:
        raise HTTPException(404)
    # Ensure we have a managed row
    try:
        row = cert_svc.pull_from_npm(
            session, integration, cert_id, auto_renew=True
        )
    except Exception as e:
        return _redirect(
            f"/integrations/{integration_id}",
            tab="certs",
            error="pull_failed",
            detail=str(e)[:200],
        )
    result = cert_svc.renew_npm_certificate(
        session, row, poll_interval_sec=5, poll_attempts=2
    )
    _audit(
        session,
        user,
        "cert_renew_requested",
        details=f"cert={row.id} ok={result.get('ok')}",
        status="success" if result.get("ok") else "failed",
    )
    if result.get("ok"):
        return _redirect(f"/certificates/{row.id}", msg="renewed")
    return _redirect(
        f"/certificates/{row.id}",
        error="renew_failed",
        detail=(result.get("error") or "")[:200],
    )

# --- create forms (from integrations.py) ---
@router.get("/integrations/new/npm", response_class=HTMLResponse)
async def npm_new_form(
    request: Request,
    user: User = Depends(get_operator_user),
):
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_npm_form.html",
        context={
            "title": "Add Nginx Proxy Manager",
            "user": user,
            "mode": "create",
            "integration": None,
            "form": {
                "name": "Nginx Proxy Manager",
                "base_url": "https://nginx.example.com",
                "identity": "",
                "poll_interval_sec": reg.DEFAULT_NPM_POLL_SEC,
                "tls_verify": True,
                "enabled": True,
            },
            "has_password": False,
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


@router.post("/integrations/new/npm")
async def npm_create(
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    name: str = Form("Nginx Proxy Manager"),
    base_url: str = Form(...),
    identity: str = Form(...),
    password: str = Form(...),
    poll_interval_sec: int = Form(reg.DEFAULT_NPM_POLL_SEC),
    tls_verify: Optional[str] = Form(None),
    enabled: Optional[str] = Form("on"),
):
    try:
        base = npm_mod.normalize_base_url(base_url)
        tls = tls_verify in ("on", "1", "true")
        result = npm_mod.poll(base, identity, password, tls_verify=tls)
        if not result.ok:
            return _redirect(
                "/integrations/new/npm",
                error="test_failed",
                detail=(result.error or "failed")[:200],
            )
        row = reg.create_npm(
            session,
            name=name,
            base_url=base,
            identity=identity,
            password=password,
            poll_interval_sec=poll_interval_sec,
            tls_verify_flag=tls,
            enabled=enabled in ("on", "1", "true") if enabled is not None else True,
        )
        poll_svc.poll_integration(row.id, notify=False)
        _audit(
            session, user, "integration_created", details=f"npm id={row.id} name={row.name}"
        )
        return _redirect(f"/integrations/{row.id}", msg="created")
    except ValueError as e:
        return _redirect("/integrations/new/npm", error="invalid", detail=str(e)[:200])
    except Exception as e:
        logger.exception("create npm failed")
        return _redirect(
            "/integrations/new/npm", error="save_failed", detail=str(e)[:200]
        )




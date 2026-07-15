"""Nginx Proxy Manager detail helpers + bind / cert routes."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, Form, HTTPException, Request
from sqlmodel import Session, select

from .. import templates as templates_mod
from ..database import get_session
from ..models import Integration, Server, User
from ..security.auth import get_operator_user
from ..services.integrations import npm as npm_mod
from ..services.integrations import registry as reg
from .integrations_common import router, _audit, _redirect

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
        return _redirect(f"/certificates/{row.id}", msg="pulled")
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

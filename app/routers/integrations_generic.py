"""Generic URL integration — create / detail / bind (HA · Frigate · n8n · custom)."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from .. import templates as templates_mod
from ..database import get_session
from ..models import Integration, Server, User
from ..security.auth import get_current_user, get_operator_user
from ..services.integrations import generic_url as gen
from ..services.integrations import poll as poll_svc
from ..services.integrations import registry as reg
from .integrations_common import (
    router,
    _audit,
    _redirect,
    _can_mutate,
    _pin_context_for_integration,
)

logger = logging.getLogger(__name__)


def _product_choices() -> list[dict[str, str]]:
    return [
        {"value": k, "label": v[0], "default_name": v[1], "health": v[2]}
        for k, v in gen.PRODUCTS.items()
    ]


@router.get("/integrations/new/generic", response_class=HTMLResponse)
async def generic_new_form(
    request: Request,
    user: User = Depends(get_operator_user),
    product: str = "custom",
):
    prod = gen.normalize_product(product or request.query_params.get("product") or "custom")
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_generic_form.html",
        context={
            "title": "Add link",
            "user": user,
            "mode": "create",
            "integration": None,
            "products": _product_choices(),
            "form": {
                "name": gen.default_name_for_product(prod),
                "base_url": "https://",
                "product": prod,
                "health_path": gen.default_health_path(prod),
                "notes": "",
                "poll_interval_sec": reg.DEFAULT_GENERIC_POLL_SEC,
                "tls_verify": True,
                "enabled": True,
            },
            "has_token": False,
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


@router.post("/integrations/new/generic")
async def generic_create(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    name: str = Form(""),
    base_url: str = Form(...),
    product: str = Form("custom"),
    health_path: str = Form(""),
    notes: str = Form(""),
    api_key: str = Form(""),
    poll_interval_sec: int = Form(reg.DEFAULT_GENERIC_POLL_SEC),
    tls_verify: Optional[str] = Form(None),
    enabled: Optional[str] = Form("on"),
    test_only: Optional[str] = Form(None),
    skip_test: Optional[str] = Form(None),
):
    tls = tls_verify in ("1", "on", "true")
    en = enabled in ("1", "on", "true")
    prod = gen.normalize_product(product)
    try:
        base = gen.normalize_base_url(base_url)
        path = (
            gen.normalize_path(health_path)
            if (health_path or "").strip()
            else gen.default_health_path(prod)
        )
        result = gen.probe(
            base,
            health_path=path,
            tls_verify=tls,
            bearer_token=(api_key or "").strip(),
            product=prod,
        )
        if test_only:
            if result.ok:
                return _redirect(
                    "/integrations/new/generic",
                    msg="test_ok",
                    detail=f"HTTP {result.status_code or 'ok'}",
                    product=prod,
                )
            return _redirect(
                "/integrations/new/generic",
                error="test_failed",
                detail=(result.error or "failed")[:200],
                product=prod,
            )
        if not result.ok and not skip_test:
            return _redirect(
                "/integrations/new/generic",
                error="test_failed",
                detail=(result.error or "failed")[:200],
                product=prod,
            )
        row = reg.create_generic_url(
            session,
            name=name,
            base_url=base,
            product=prod,
            health_path=path,
            notes=notes,
            api_key=api_key,
            poll_interval_sec=poll_interval_sec,
            tls_verify_flag=tls,
            enabled=en,
        )
        # Cache probe
        if result.ok or skip_test:
            from datetime import datetime
            import json

            now = datetime.utcnow()
            payload = result.to_status_json()
            payload["polled_at"] = now.isoformat() + "Z"
            row.last_status_json = json.dumps(payload)
            row.last_polled_at = now
            row.last_error = None if result.ok else (result.error or "")[:500]
            session.add(row)
            session.commit()
        _audit(session, user, "integration_created", details=f"generic_url id={row.id} product={prod}")
        return _redirect(f"/integrations/{row.id}", msg="created")
    except ValueError as e:
        return _redirect(
            "/integrations/new/generic",
            error="invalid",
            detail=str(e)[:200],
            product=prod,
        )


async def render_generic_detail(
    request: Request,
    session: Session,
    user: User,
    integration: Integration,
):
    st = reg.parse_last_status(integration)
    servers = list(session.exec(select(Server).order_by(Server.name)).all())
    binds = reg.list_bindings(
        session, integration_id=integration.id, role=reg.ROLE_SERVICE
    )
    server_names = {s.id: s.name for s in servers}
    bind_rows = []
    for b in binds:
        chip = reg.binding_to_chip(session, b)
        chip["server_name"] = server_names.get(b.server_id, f"#{b.server_id}")
        bind_rows.append(chip)
    pin = _pin_context_for_integration(session, user, integration)
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_generic_detail.html",
        context={
            "title": integration.name,
            "user": user,
            "integration": integration,
            "product": reg.generic_product(integration),
            "product_label": gen.product_label(reg.generic_product(integration)),
            "health_path": reg.generic_health_path(integration),
            "notes": reg.generic_notes(integration),
            "open_url": gen.open_url(integration.base_url, ""),
            "status": st,
            "ok": st.get("ok"),
            "status_code": st.get("status_code"),
            "servers": servers,
            "bindings": bind_rows,
            "can_mutate": _can_mutate(user),
            "has_token": reg.has_credentials(integration),
            "poll_interval_sec": reg.poll_interval_sec(integration),
            "tls_verify": reg.tls_verify(integration),
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
            **pin,
        },
    )


@router.post("/integrations/{integration_id}/generic/bind")
async def generic_bind(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    server_id: int = Form(...),
    label: str = Form(""),
    path: str = Form(""),
    docker_project: str = Form(""),
    docker_container: str = Form(""),
):
    integration = reg.get_integration(session, integration_id)
    if not integration or integration.type != reg.TYPE_GENERIC_URL:
        raise HTTPException(404)
    try:
        lab = (label or "").strip() or integration.name
        p = (path or "").strip()
        open_u = gen.open_url(integration.base_url, p)
        # external_id must be unique per scope; use stable key
        if p.startswith("http"):
            ext = "url"
            meta_url = p
            meta_path = ""
        elif p and p not in ("/", ""):
            ext = p if len(p) <= 120 else p[:120]
            meta_url = open_u
            meta_path = gen.normalize_path(p)
        else:
            ext = "url"
            meta_url = open_u
            meta_path = "/"
        reg.set_binding(
            session,
            integration_id=integration_id,
            server_id=int(server_id),
            external_id=ext,
            role=reg.ROLE_SERVICE,
            docker_project=(docker_project or "").strip() or None,
            docker_container=(docker_container or "").strip() or None,
            external_label=lab,
            external_meta={
                "url": meta_url,
                "path": meta_path,
                "product": reg.generic_product(integration),
            },
            last_state="linked",
            last_message="deep link",
        )
        _audit(
            session,
            user,
            "integration_binding_set",
            server_id=int(server_id),
            details=f"generic_url id={integration_id} label={lab}",
        )
        return _redirect(f"/integrations/{integration_id}", msg="binding_saved")
    except ValueError as e:
        return _redirect(
            f"/integrations/{integration_id}",
            error="invalid",
            detail=str(e)[:200],
        )


@router.post("/integrations/{integration_id}/generic/bind/{binding_id}/remove")
async def generic_unbind(
    integration_id: int,
    binding_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    integration = reg.get_integration(session, integration_id)
    if not integration or integration.type != reg.TYPE_GENERIC_URL:
        raise HTTPException(404)
    from ..models import IntegrationBinding

    b = session.get(IntegrationBinding, binding_id)
    if not b or b.integration_id != integration_id:
        raise HTTPException(404)
    session.delete(b)
    session.commit()
    _audit(
        session,
        user,
        "integration_binding_removed",
        server_id=b.server_id,
        details=f"generic_url binding={binding_id}",
    )
    return _redirect(f"/integrations/{integration_id}", msg="binding_cleared")

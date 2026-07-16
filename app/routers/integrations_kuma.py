"""Uptime Kuma create forms + detail render (shared integrations router)."""
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
from ..services.integrations import poll as poll_svc
from ..services.integrations import registry as reg
from ..services.integrations import uptime_kuma as kuma
from .integrations_common import router, _audit, _redirect, _can_mutate

logger = logging.getLogger(__name__)

@router.get("/integrations/new/uptime-kuma", response_class=HTMLResponse)
async def kuma_new_form(
    request: Request,
    user: User = Depends(get_operator_user),
):
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_kuma_form.html",
        context={
            "title": "Add Uptime Kuma",
            "user": user,
            "mode": "create",
            "integration": None,
            "form": {
                "name": "Uptime Kuma",
                "base_url": "https://uptime.hacknow.info",
                "poll_interval_sec": reg.DEFAULT_POLL_INTERVAL_SEC,
                "tls_verify": True,
                "enabled": True,
                "username": "",
            },
            "has_kuma_login": False,
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


@router.post("/integrations/new/uptime-kuma")
async def kuma_create(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    name: str = Form("Uptime Kuma"),
    base_url: str = Form(...),
    api_key: str = Form(...),
    poll_interval_sec: int = Form(reg.DEFAULT_POLL_INTERVAL_SEC),
    tls_verify: Optional[str] = Form(None),
    enabled: Optional[str] = Form("on"),
    username: str = Form(""),
    password: str = Form(""),
    test_only: Optional[str] = Form(None),
):
    tls = tls_verify in ("1", "on", "true")
    en = enabled in ("1", "on", "true")
    try:
        base = kuma.normalize_base_url(base_url)
        key = (api_key or "").strip()
        if not key:
            raise ValueError("API key is required")
        # Test first
        result = kuma.fetch_metrics(base, key, tls_verify=tls)
        if not result.ok:
            return _redirect(
                "/integrations/new/uptime-kuma",
                error="test_failed",
                detail=result.error[:200],
            )
        if test_only in ("1", "on", "true"):
            return _redirect(
                "/integrations/new/uptime-kuma",
                msg="test_ok",
                detail=f"{len(result.monitors)} monitors",
            )
        row = reg.create_kuma(
            session,
            name=name,
            base_url=base,
            api_key=key,
            poll_interval_sec=poll_interval_sec,
            tls_verify_flag=tls,
            enabled=en,
            username=username,
            password=password,
        )
        # Persist first successful poll
        poll_svc.poll_integration(row.id, notify=False)
        _audit(session, user, "integration_created", details=f"uptime_kuma id={row.id} name={row.name}")
        return _redirect(f"/integrations/{row.id}", msg="created")
    except ValueError as e:
        return _redirect("/integrations/new/uptime-kuma", error="invalid", detail=str(e)[:200])
    except Exception as e:
        logger.exception("create kuma failed")
        return _redirect("/integrations/new/uptime-kuma", error="save_failed", detail=str(e)[:200])




async def render_kuma_detail(request, session, user, integration: Integration):
    integration_id = integration.id
    servers = list(session.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
    all_bindings = reg.list_bindings(session, integration_id=integration_id)
    ssh_by_server = {
        b.server_id: b for b in all_bindings if b.role == reg.ROLE_SSH
    }
    service_bindings = [b for b in all_bindings if b.role == reg.ROLE_SERVICE]
    monitors = reg.monitors_from_cache(integration)

    def _msort(m):
        t = (m.get("type") or "").lower()
        pri = 0 if t in ("port", "tcp") else 1
        return (pri, (m.get("name") or "").lower())

    monitors_sorted = sorted(monitors, key=_msort)
    ssh_monitors = [m for m in monitors_sorted if m.get("is_ssh_like")]
    service_monitors = [m for m in monitors_sorted if m.get("is_service_like") or not m.get("is_ssh_like")]
    status = reg.parse_last_status(integration)

    def _mon_obj(m: dict) -> kuma.KumaMonitor:
        return kuma.KumaMonitor(
            id=str(m.get("id")),
            name=m.get("name") or "",
            type=m.get("type") or "",
            hostname=m.get("hostname") or "",
            port=str(m.get("port") or ""),
            url=m.get("url") or "",
            status=m.get("status") or "unknown",
            response_time_ms=m.get("response_time_ms"),
            dashboard_id=str(m["dashboard_id"]) if m.get("dashboard_id") else None,
            cert_days_remaining=m.get("cert_days_remaining"),
            cert_is_valid=m.get("cert_is_valid"),
        )

    mon_objs = [_mon_obj(m) for m in monitors if m.get("id") is not None]
    mon_by_id = {m.id: m for m in mon_objs}

    binding_rows = []
    for s in servers:
        b = ssh_by_server.get(s.id)
        meta = reg.parse_binding_meta(b) if b else {}
        did = kuma.resolve_dashboard_id(
            mon_by_id.get(b.external_id) if b else None,
            external_id=(b.external_id if b else ""),
            meta=meta,
        )
        binding_rows.append(
            {
                "server_id": s.id,
                "server_name": s.name,
                "hostname": s.hostname,
                "ip_address": s.ip_address,
                "ssh_port": s.ssh_port,
                "binding": b,
                "state": (b.last_state if b else None),
                "message": (b.last_message if b else None),
                "external_id": (b.external_id if b else ""),
                "external_label": (b.external_label if b else ""),
                "dashboard_id": did or meta.get("dashboard_id") or "",
                "open_url": (
                    kuma.open_kuma_url(integration.base_url, dashboard_id=did)
                    if b
                    else ""
                ),
            }
        )

    suggestions = {}
    suggestion_dashboard = {}
    for s in servers:
        sug = kuma.suggest_monitor_for_server(
            mon_objs,
            hostname=s.hostname or "",
            ip_address=s.ip_address or "",
            ssh_port=s.ssh_port or 22,
        )
        if sug:
            suggestions[s.id] = sug.id
            if sug.dashboard_id:
                suggestion_dashboard[s.id] = sug.dashboard_id

    server_name = {s.id: s.name for s in servers}
    service_rows = []
    for b in service_bindings:
        meta = reg.parse_binding_meta(b)
        mon = kuma.find_monitor(mon_objs, b.external_id or "", meta=meta)
        did = kuma.resolve_dashboard_id(mon, external_id=b.external_id or "", meta=meta)
        service_rows.append(
            {
                "binding_id": b.id,
                "server_id": b.server_id,
                "server_name": server_name.get(b.server_id, f"#{b.server_id}"),
                "docker_project": b.docker_project or meta.get("docker_project") or "",
                "docker_container": b.docker_container or meta.get("docker_container") or "",
                "external_id": b.external_id,
                "external_label": b.external_label or b.external_id,
                "state": b.last_state,
                "message": b.last_message,
                "dashboard_id": did or "",
                "open_url": kuma.open_kuma_url(integration.base_url, dashboard_id=did),
                "cert_days": meta.get("cert_days_remaining"),
                "cert_valid": meta.get("cert_is_valid"),
                "url": meta.get("url") or meta.get("target") or "",
            }
        )

    # Docker inventory options per server for service binding form
    docker_options = {
        s.id: reg.docker_inventory_options(session, s.id) for s in servers
    }

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_kuma_detail.html",
        context={
            "title": integration.name,
            "user": user,
            "integration": integration,
            "status": status,
            "monitors": monitors_sorted,
            "ssh_monitors": ssh_monitors or monitors_sorted,
            "service_monitors": service_monitors or monitors_sorted,
            "binding_rows": binding_rows,
            "service_rows": service_rows,
            "servers": servers,
            "docker_options": docker_options,
            "docker_options_json": json.dumps(
                {str(k): v for k, v in docker_options.items()}
            ),
            "suggestions": suggestions,
            "suggestion_dashboard": suggestion_dashboard,
            "can_mutate": _can_mutate(user),
            "has_key": reg.has_credentials(integration),
            "has_kuma_login": reg.has_kuma_login(integration),
            "poll_interval_sec": reg.poll_interval_sec(integration),
            "tls_verify": reg.tls_verify(integration),
            "open_url": kuma.open_kuma_url(integration.base_url),
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


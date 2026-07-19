"""Integrations hub — list, CRUD, Kuma/Grafana bindings (Pi-hole/NPM in sibling modules)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session, select

from .. import templates as templates_mod
from ..database import get_session
from ..models import AuditLog, Integration, Server, User
from ..security.auth import get_current_user, get_operator_user, role_at_least, ROLE_OPERATOR
from ..services import jobs as job_service
from ..services.integrations import grafana as gf
from ..services.integrations import npm as npm_mod
from ..services.integrations import pihole as ph
from ..services.integrations import poll as poll_svc
from ..services.integrations import registry as reg
from ..services.integrations import uptime_kuma as kuma
from .integrations_common import router, _audit, _redirect, _can_mutate

logger = logging.getLogger(__name__)

@router.get("/catalog", response_class=HTMLResponse)
async def catalog_entry(
    user: User = Depends(get_current_user),
):
    """Top-nav Catalog always lands on Integrations (Templates is the other tab)."""
    del user
    return RedirectResponse("/integrations", status_code=303)


@router.get("/integrations", response_class=HTMLResponse)
async def integrations_list(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    rows = reg.list_integrations(session)
    items = []
    for r in rows:
        st = reg.parse_last_status(r)
        items.append(
            {
                "id": r.id,
                "type": r.type,
                "name": r.name,
                "base_url": r.base_url,
                "enabled": r.enabled,
                "last_polled_at": r.last_polled_at,
                "last_error": r.last_error,
                "has_key": reg.has_credentials(r),
                "ok": st.get("ok"),
                "monitor_count": st.get("monitor_count"),
                "dashboard_count": st.get("dashboard_count"),
                "version": st.get("version") or "",
                "poll_interval_sec": reg.poll_interval_sec(r),
                "queries": st.get("queries"),
                "blocked": st.get("blocked"),
                "percent_blocked": st.get("percent_blocked"),
                "is_primary": st.get("is_primary") or reg.is_pihole_primary(r)
                if r.type == reg.TYPE_PIHOLE
                else False,
                "proxy_host_count": st.get("proxy_host_count"),
                "certificate_count": st.get("certificate_count"),
                "device_count": st.get("device_count"),
                "worker_online": st.get("worker_online"),
            }
        )
    # Multi Pi-hole fleet summary
    pihole_items = [i for i in items if i["type"] == reg.TYPE_PIHOLE]
    pihole_summary = None
    if len(pihole_items) >= 1:
        pihole_summary = ph.summarize_instances(
            [
                {
                    "ok": i.get("ok"),
                    "queries": i.get("queries") or 0,
                    "blocked": i.get("blocked") or 0,
                    "active_clients": (reg.parse_last_status(
                        reg.get_integration(session, i["id"])
                    ) or {}).get("active_clients")
                    if i.get("id")
                    else 0,
                    "domains_on_lists": (reg.parse_last_status(
                        reg.get_integration(session, i["id"])
                    ) or {}).get("domains_on_lists")
                    if i.get("id")
                    else 0,
                    "is_primary": i.get("is_primary"),
                }
                for i in pihole_items
            ]
        )
    # Compact hero pulse
    by_type: dict = {}
    ok_n = err_n = off_n = never_n = 0
    for i in items:
        t = i.get("type") or "other"
        by_type[t] = by_type.get(t, 0) + 1
        if not i.get("enabled"):
            off_n += 1
        elif i.get("ok") is True:
            ok_n += 1
        elif i.get("ok") is False or i.get("last_error"):
            err_n += 1
        else:
            never_n += 1
    type_line = [
        {"n": by_type.get(t, 0), "l": lab, "cls": ""}
        for t, lab in (
            ("uptime_kuma", "kuma"),
            ("grafana", "grafana"),
            ("pihole", "pihole"),
            ("npm", "npm"),
        )
        if by_type.get(t)
    ]
    from ..services.ops_pulse import catalog_health, dual_line_pulse, stat, bar_seg

    catalog_pulse = dual_line_pulse(
        health=catalog_health(err_n=err_n, items=len(items)),
        primary=len(items),
        primary_label="linked",
        bar=[
            bar_seg(ok_n, "ops-bar--ok", f"{ok_n} ok"),
            bar_seg(err_n, "ops-bar--fail", f"{err_n} error"),
            bar_seg(never_n, "ops-bar--run", f"{never_n} never"),
            bar_seg(off_n, "ops-bar--mute", f"{off_n} off"),
        ]
        if items
        else [{"n": 1, "cls": "ops-bar--mute", "title": "empty"}],
        line1=[
            stat(ok_n, "ok", "text-accent"),
            stat(err_n, "error", "text-danger" if err_n else ""),
            stat(never_n, "never"),
            stat(off_n, "off"),
        ],
        line2=type_line or [stat(0, "none")],
        caption="Connection health · by product",
    )
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_list.html",
        context={
            "title": "Integrations",
            "user": user,
            "integrations": items,
            "pihole_summary": pihole_summary,
            "catalog_pulse": catalog_pulse,
            "can_mutate": _can_mutate(user),
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )



# Product modules register create/detail helpers on the shared router
from . import integrations_kuma as _kuma  # noqa: F401
from . import integrations_grafana as _grafana  # noqa: F401
from . import integrations_pihole as _pihole  # noqa: F401
from . import integrations_npm as _npm  # noqa: F401
from . import integrations_nmap as _nmap  # noqa: F401


@router.get("/integrations/{integration_id}", response_class=HTMLResponse)
async def integration_detail(
    integration_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    integration = reg.get_integration(session, integration_id)
    if not integration:
        raise HTTPException(404, "Integration not found")
    if integration.type == reg.TYPE_GRAFANA:
        from .integrations_grafana import render_grafana_detail
        return await render_grafana_detail(request, session, user, integration)
    if integration.type == reg.TYPE_PIHOLE:
        from .integrations_pihole import render_pihole_detail
        return await render_pihole_detail(request, session, user, integration)
    if integration.type == reg.TYPE_NPM:
        from .integrations_npm import render_npm_detail
        return await render_npm_detail(request, session, user, integration)
    if integration.type == reg.TYPE_NMAP:
        from .integrations_nmap import render_nmap_detail
        return await render_nmap_detail(request, session, user, integration)
    if integration.type == reg.TYPE_UPTIME_KUMA:
        from .integrations_kuma import render_kuma_detail
        return await render_kuma_detail(request, session, user, integration)
    raise HTTPException(400, "Unsupported integration type in UI yet")

@router.get("/integrations/{integration_id}/edit", response_class=HTMLResponse)
async def integration_edit_form(
    integration_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    integration = reg.get_integration(session, integration_id)
    if not integration:
        raise HTTPException(404)
    if integration.type == reg.TYPE_GRAFANA:
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="integrations_grafana_form.html",
            context={
                "title": f"Edit {integration.name}",
                "user": user,
                "mode": "edit",
                "integration": integration,
                "form": {
                    "name": integration.name,
                    "base_url": integration.base_url,
                    "poll_interval_sec": reg.poll_interval_sec(integration),
                    "tls_verify": reg.tls_verify(integration),
                    "enabled": integration.enabled,
                    "query_template": reg.query_template(integration),
                    "query_template_container_host": reg.query_template_container_host(
                        integration
                    ),
                    "query_template_container": reg.query_template_container(integration),
                    "query_template_logs": reg.query_template_logs(integration),
                },
                "has_key": reg.has_credentials(integration),
                "error": request.query_params.get("error") or "",
                "detail": request.query_params.get("detail") or "",
            },
        )
    if integration.type == reg.TYPE_PIHOLE:
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="integrations_pihole_form.html",
            context={
                "title": f"Edit {integration.name}",
                "user": user,
                "mode": "edit",
                "integration": integration,
                "form": {
                    "name": integration.name,
                    "base_url": integration.base_url,
                    "poll_interval_sec": reg.poll_interval_sec(integration),
                    "tls_verify": reg.tls_verify(integration),
                    "enabled": integration.enabled,
                    "is_primary": reg.is_pihole_primary(integration),
                },
                "has_password": reg.has_credentials(integration),
                "error": request.query_params.get("error") or "",
                "detail": request.query_params.get("detail") or "",
            },
        )
    if integration.type == reg.TYPE_NPM:
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="integrations_npm_form.html",
            context={
                "title": f"Edit {integration.name}",
                "user": user,
                "mode": "edit",
                "integration": integration,
                "form": {
                    "name": integration.name,
                    "base_url": integration.base_url,
                    "identity": reg.decrypt_credentials(integration).get("username")
                    or "",
                    "poll_interval_sec": reg.poll_interval_sec(integration),
                    "tls_verify": reg.tls_verify(integration),
                    "enabled": integration.enabled,
                },
                "has_password": reg.has_credentials(integration),
                "error": request.query_params.get("error") or "",
                "detail": request.query_params.get("detail") or "",
            },
        )
    if integration.type == reg.TYPE_NMAP:
        from ..services.nmap import config as nmap_cfg

        cfg = nmap_cfg.parse_nmap_config(integration)
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="integrations_nmap_form.html",
            context={
                "title": f"Edit {integration.name}",
                "user": user,
                "mode": "edit",
                "integration": integration,
                "form": {
                    "name": integration.name,
                    "cidrs": "\n".join(cfg.get("cidrs") or []),
                    "excludes": "\n".join(cfg.get("excludes") or []),
                    "excludes_port_scans": "\n".join(cfg.get("excludes_port_scans") or []),
                    "excludes_deep": "\n".join(cfg.get("excludes_deep") or []),
                    "skip_dns": cfg.get("skip_dns", False),
                    "use_syn": cfg.get("use_syn", False),
                    "vuln_enabled": cfg.get("vuln_enabled", False),
                    "notes": cfg.get("notes") or "",
                    "enabled": integration.enabled,
                },
                "error": request.query_params.get("error") or "",
                "detail": request.query_params.get("detail") or "",
            },
        )
    if integration.type != reg.TYPE_UPTIME_KUMA:
        raise HTTPException(404)
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_kuma_form.html",
        context={
            "title": f"Edit {integration.name}",
            "user": user,
            "mode": "edit",
            "integration": integration,
            "form": {
                "name": integration.name,
                "base_url": integration.base_url,
                "poll_interval_sec": reg.poll_interval_sec(integration),
                "tls_verify": reg.tls_verify(integration),
                "enabled": integration.enabled,
                "username": reg.decrypt_credentials(integration).get("username") or "",
            },
            "has_key": reg.has_credentials(integration),
            "has_kuma_login": reg.has_kuma_login(integration),
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


@router.post("/integrations/{integration_id}/edit")
async def integration_edit(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    name: str = Form(...),
    base_url: str = Form(""),
    api_key: str = Form(""),
    poll_interval_sec: int = Form(reg.DEFAULT_POLL_INTERVAL_SEC),
    tls_verify: Optional[str] = Form(None),
    enabled: Optional[str] = Form(None),
    username: str = Form(""),
    password: str = Form(""),
    clear_login: Optional[str] = Form(None),
    query_template: str = Form(""),
    query_template_container_host: str = Form(""),
    query_template_container: str = Form(""),
    query_template_logs: str = Form(""),
    clear_token: Optional[str] = Form(None),
    is_primary: Optional[str] = Form(None),
    identity: str = Form(""),
    cidrs: str = Form(""),
    excludes: str = Form(""),
    excludes_port_scans: str = Form(""),
    excludes_deep: str = Form(""),
    skip_dns: Optional[str] = Form(None),
    use_syn: Optional[str] = Form(None),
    vuln_enabled: Optional[str] = Form(None),
    notes: str = Form(""),
):
    integration = reg.get_integration(session, integration_id)
    if not integration:
        raise HTTPException(404)
    if integration.type == reg.TYPE_NMAP:
        from ..services.nmap import config as nmap_cfg

        try:
            nmap_cfg.update_nmap(
                session,
                integration,
                name=name,
                cidrs=nmap_cfg.parse_cidrs_textarea(cidrs),
                excludes=nmap_cfg.parse_cidrs_textarea(excludes),
                excludes_port_scans=nmap_cfg.parse_cidrs_textarea(excludes_port_scans),
                excludes_deep=nmap_cfg.parse_cidrs_textarea(excludes_deep),
                skip_dns=skip_dns in ("on", "1", "true"),
                use_syn=use_syn in ("on", "1", "true"),
                vuln_enabled=vuln_enabled in ("on", "1", "true"),
                notes=notes,
                enabled=enabled in ("on", "1", "true") if enabled is not None else True,
            )
            nmap_cfg.refresh_status(session, integration)
            _audit(
                session, user, "integration_updated", details=f"id={integration_id} nmap"
            )
            return _redirect(f"/integrations/{integration_id}", msg="saved")
        except ValueError as e:
            return _redirect(
                f"/integrations/{integration_id}/edit",
                error="invalid",
                detail=str(e)[:200],
            )
    if integration.type == reg.TYPE_PIHOLE:
        try:
            reg.update_pihole(
                session,
                integration,
                name=name,
                base_url=base_url,
                password=password if password.strip() else None,
                poll_interval_sec=poll_interval_sec,
                tls_verify_flag=tls_verify in ("1", "on", "true"),
                enabled=enabled in ("1", "on", "true"),
                is_primary=is_primary in ("1", "on", "true"),
            )
            _audit(
                session, user, "integration_updated", details=f"id={integration_id} pihole"
            )
            return _redirect(f"/integrations/{integration_id}", msg="saved")
        except ValueError as e:
            return _redirect(
                f"/integrations/{integration_id}/edit",
                error="invalid",
                detail=str(e)[:200],
            )
    if integration.type == reg.TYPE_NPM:
        try:
            reg.update_npm(
                session,
                integration,
                name=name,
                base_url=base_url,
                identity=identity if identity.strip() else None,
                password=password if password.strip() else None,
                poll_interval_sec=poll_interval_sec,
                tls_verify_flag=tls_verify in ("1", "on", "true"),
                enabled=enabled in ("1", "on", "true"),
            )
            _audit(
                session, user, "integration_updated", details=f"id={integration_id} npm"
            )
            return _redirect(f"/integrations/{integration_id}", msg="saved")
        except ValueError as e:
            return _redirect(
                f"/integrations/{integration_id}/edit",
                error="invalid",
                detail=str(e)[:200],
            )
    if integration.type == reg.TYPE_GRAFANA:
        try:
            reg.update_grafana(
                session,
                integration,
                name=name,
                base_url=base_url,
                api_key=api_key if api_key.strip() else None,
                poll_interval_sec=poll_interval_sec,
                tls_verify_flag=tls_verify in ("1", "on", "true"),
                enabled=enabled in ("1", "on", "true"),
                query_template=query_template,
                query_template_container_host=query_template_container_host,
                query_template_container=query_template_container,
                query_template_logs=query_template_logs,
                clear_token=clear_token in ("1", "on", "true"),
            )
            _audit(
                session,
                user,
                "integration_updated",
                details=f"id={integration_id} grafana",
            )
            return _redirect(f"/integrations/{integration_id}", msg="saved")
        except ValueError as e:
            return _redirect(
                f"/integrations/{integration_id}/edit",
                error="invalid",
                detail=str(e)[:200],
            )
        except Exception as e:
            logger.exception("grafana edit")
            return _redirect(
                f"/integrations/{integration_id}/edit",
                error="save_failed",
                detail=str(e)[:200],
            )
    if integration.type != reg.TYPE_UPTIME_KUMA:
        raise HTTPException(404)
    try:
        if clear_login in ("1", "on", "true"):
            uname, pw = "", ""
        else:
            uname = username
            pw = password if password.strip() else None
        reg.update_kuma(
            session,
            integration,
            name=name,
            base_url=base_url,
            api_key=api_key if api_key.strip() else None,
            poll_interval_sec=poll_interval_sec,
            tls_verify_flag=tls_verify in ("1", "on", "true"),
            enabled=enabled in ("1", "on", "true"),
            username=uname if clear_login in ("1", "on", "true") or username != "" or pw else None,
            password="" if clear_login in ("1", "on", "true") else pw,
        )
        _audit(session, user, "integration_updated", details=f"id={integration_id}")
        return _redirect(f"/integrations/{integration_id}", msg="saved")
    except ValueError as e:
        return _redirect(
            f"/integrations/{integration_id}/edit",
            error="invalid",
            detail=str(e)[:200],
        )


@router.post("/integrations/{integration_id}/test")
async def integration_test(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    integration = reg.get_integration(session, integration_id)
    if not integration:
        raise HTTPException(404)
    result = poll_svc.test_connection(integration)
    if result.ok:
        poll_svc.poll_integration(integration_id, notify=False)
        if integration.type == reg.TYPE_GRAFANA:
            n = len(getattr(result, "dashboards", None) or [])
            ver = getattr(result, "version", "") or ""
            detail = f"v{ver}" if ver else "healthy"
            if n:
                detail = f"{detail} · {n} dashboards"
            return _redirect(
                f"/integrations/{integration_id}", msg="test_ok", detail=detail
            )
        if integration.type == reg.TYPE_PIHOLE:
            q = getattr(result, "queries", 0)
            b = getattr(result, "blocked", 0)
            return _redirect(
                f"/integrations/{integration_id}",
                msg="test_ok",
                detail=f"{q} queries · {b} blocked",
            )
        if integration.type == reg.TYPE_NPM:
            n = len(getattr(result, "proxy_hosts", None) or [])
            c = len(getattr(result, "certificates", None) or [])
            return _redirect(
                f"/integrations/{integration_id}",
                msg="test_ok",
                detail=f"{n} proxy hosts · {c} certs",
            )
        if integration.type == reg.TYPE_NMAP:
            n_cidrs = len(getattr(result, "cidrs", None) or [])
            online = "worker online" if getattr(result, "worker_online", False) else "worker offline"
            return _redirect(
                f"/integrations/{integration_id}",
                msg="test_ok",
                detail=f"{online} · {n_cidrs} CIDR(s)",
            )
        return _redirect(
            f"/integrations/{integration_id}",
            msg="test_ok",
            detail=f"{len(result.monitors)} monitors",
        )
    return _redirect(
        f"/integrations/{integration_id}",
        error="test_failed",
        detail=(result.error or "failed")[:200],
    )


@router.post("/integrations/{integration_id}/poll")
async def integration_poll(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    integration = reg.get_integration(session, integration_id)
    if not integration:
        raise HTTPException(404)
    summary = poll_svc.poll_integration(integration_id, notify=True)
    if summary.get("ok"):
        if integration.type == reg.TYPE_GRAFANA:
            n = summary.get("dashboard_count") or summary.get("monitor_count") or 0
            ver = summary.get("version") or ""
            detail = f"{n} dashboards"
            if ver:
                detail = f"v{ver} · {detail}"
            return _redirect(
                f"/integrations/{integration_id}", msg="polled", detail=detail
            )
        if integration.type == reg.TYPE_PIHOLE:
            return _redirect(
                f"/integrations/{integration_id}",
                msg="polled",
                detail=f"{summary.get('queries', 0)} queries",
            )
        if integration.type == reg.TYPE_NPM:
            return _redirect(
                f"/integrations/{integration_id}",
                msg="polled",
                detail=f"{summary.get('proxy_host_count', 0)} hosts",
            )
        if integration.type == reg.TYPE_NMAP:
            return _redirect(
                f"/integrations/{integration_id}",
                msg="polled",
                detail=f"{summary.get('device_count', 0)} devices",
            )
        return _redirect(
            f"/integrations/{integration_id}",
            msg="polled",
            detail=f"{summary.get('monitor_count', 0)} monitors",
        )
    if summary.get("skipped"):
        return _redirect(f"/integrations/{integration_id}", error="poll_busy")
    return _redirect(
        f"/integrations/{integration_id}",
        error="poll_failed",
        detail=(summary.get("error") or "")[:200],
    )


@router.post("/integrations/{integration_id}/delete")
async def kuma_delete(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    integration = reg.get_integration(session, integration_id)
    if not integration:
        raise HTTPException(404)
    name = integration.name
    reg.delete_integration(session, integration)
    _audit(session, user, "integration_deleted", details=f"name={name}")
    return _redirect("/integrations", msg="deleted")


def _monitor_meta_from_cache(
    integration: Integration,
    external_id: str,
    dashboard_id: str = "",
) -> tuple[Optional[dict], Optional[str], Optional[str], Optional[str]]:
    """Return (meta, label, state, message) for a monitor external_id."""
    mon_meta = None
    mon_label = None
    mon_state = None
    mon_msg = None
    for m in reg.monitors_from_cache(integration):
        if str(m.get("id")) == str(external_id).strip() or str(m.get("name")) == str(
            external_id
        ).strip():
            mon_meta = dict(m)
            mon_label = m.get("name")
            mon_state = m.get("status")
            mon_msg = reg.binding_message_from_monitor(
                kuma.KumaMonitor(
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
            )
            break
    if mon_meta is None:
        mon_meta = {}
    did = (dashboard_id or "").strip() or str(mon_meta.get("dashboard_id") or "").strip()
    if did.isdigit():
        mon_meta["dashboard_id"] = did
    return mon_meta, mon_label, mon_state, mon_msg


def _optional_int_form(raw: Optional[str]) -> Optional[int]:
    """Form fields often send '' for empty optional ints — treat as None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


@router.post("/integrations/{integration_id}/bindings")
async def set_binding(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    server_id: int = Form(...),
    external_id: str = Form(""),
    dashboard_id: str = Form(""),
    role: str = Form(reg.ROLE_SSH),
    docker_project: str = Form(""),
    docker_container: str = Form(""),
    kind: str = Form(reg.GRAFANA_KIND_METRICS),
    display_name: str = Form(""),
    clear: Optional[str] = Form(None),
    binding_id: Optional[str] = Form(None),
    next: str = Form(""),
):
    integration = reg.get_integration(session, integration_id)
    if not integration:
        raise HTTPException(404)
    role = (role or reg.ROLE_SSH).strip()
    if role not in (reg.ROLE_SSH, reg.ROLE_SERVICE, reg.ROLE_DASHBOARD):
        role = reg.ROLE_SSH
    binding_id_int = _optional_int_form(binding_id)

    if role == reg.ROLE_DASHBOARD:
        gkind = reg.normalize_grafana_kind(kind)
        tab = {
            reg.GRAFANA_KIND_METRICS: "metrics",
            reg.GRAFANA_KIND_CONTAINERS: "containers",
            reg.GRAFANA_KIND_LOGS: "logs",
        }.get(gkind, "metrics")
        scope = "dashboard"
        if clear in ("1", "on", "true") or not (external_id or "").strip():
            reg.clear_binding(
                session,
                integration_id=integration_id,
                server_id=server_id,
                role=role,
                external_id=external_id,
                binding_id=binding_id_int,
            )
            _audit(
                session,
                user,
                "integration_binding_cleared",
                server_id=server_id,
                details=f"integration={integration_id} role={role}",
            )
            return _redirect(
                f"/integrations/{integration_id}",
                msg="binding_cleared",
                scope=scope,
                tab=tab,
            )
        # Resolve meta from cached dashboards or manual uid
        mon_meta: dict = {}
        mon_label = None
        for d in reg.dashboards_from_cache(integration):
            if str(d.get("uid")) == str(external_id).strip():
                mon_meta = dict(d)
                mon_label = d.get("title") or d.get("name")
                break
        if not mon_meta:
            mon_meta = {"uid": external_id.strip()}
            mon_label = external_id.strip()
        mon_meta["kind"] = gkind
        grafana_title = mon_label
        if mon_label:
            mon_meta["grafana_title"] = mon_label
        uid = (external_id or "").strip()
        # Form name (if set) becomes the preferred name for this dashboard UID.
        # Otherwise inherit any existing preferred name so new binds match.
        custom_label = (display_name or "").strip()
        if custom_label:
            reg.set_preferred_display_name(session, integration, uid, custom_label)
            session.refresh(integration)
            mon_meta["label_override"] = custom_label
            mon_label = custom_label
        else:
            preferred = reg.preferred_display_name(integration, uid)
            if preferred:
                mon_meta["label_override"] = preferred
                mon_label = preferred
            else:
                mon_meta.pop("label_override", None)
                mon_label = grafana_title or mon_label
        proj = (docker_project or "").strip() if gkind == reg.GRAFANA_KIND_CONTAINERS else ""
        cont = (docker_container or "").strip() if gkind == reg.GRAFANA_KIND_CONTAINERS else ""
        msg_bits = [gkind]
        if cont:
            msg_bits.append(cont)
        elif proj:
            msg_bits.append(proj)
        try:
            reg.set_binding(
                session,
                integration_id=integration_id,
                server_id=server_id,
                external_id=external_id,
                role=role,
                docker_project=proj or None,
                docker_container=cont or None,
                external_label=mon_label,
                external_meta=mon_meta,
                last_state="linked",
                last_message=" · ".join(msg_bits),
                binding_id=binding_id_int,
            )
            _audit(
                session,
                user,
                "integration_binding_set",
                server_id=server_id,
                details=(
                    f"integration={integration_id} role={role} kind={gkind}"
                    f" uid={external_id} project={proj} container={cont}"
                ),
            )
            return _redirect(
                f"/integrations/{integration_id}",
                msg="binding_saved",
                scope=scope,
                tab=tab,
            )
        except ValueError as e:
            return _redirect(
                f"/integrations/{integration_id}",
                error="binding_failed",
                detail=str(e)[:200],
                scope=scope,
                tab=tab,
            )
        except Exception as e:
            logger.exception("grafana binding failed")
            return _redirect(
                f"/integrations/{integration_id}",
                error="binding_failed",
                detail=str(e)[:200],
                scope=scope,
                tab=tab,
            )

    if clear in ("1", "on", "true") or not (external_id or "").strip():
        reg.clear_binding(
            session,
            integration_id=integration_id,
            server_id=server_id,
            role=role,
            external_id=external_id if role == reg.ROLE_SERVICE else None,
            binding_id=binding_id_int,
        )
        _audit(
            session,
            user,
            "integration_binding_cleared",
            server_id=server_id,
            details=f"integration={integration_id} role={role}",
        )
        scope = "service" if role == reg.ROLE_SERVICE else "ssh"
        section = "kuma-services" if role == reg.ROLE_SERVICE else "kuma-ssh"
        return _redirect(
            f"/integrations/{integration_id}",
            fragment=section,
            msg="binding_cleared",
            scope=scope,
        )

    mon_meta, mon_label, mon_state, mon_msg = _monitor_meta_from_cache(
        integration, external_id, dashboard_id=dashboard_id
    )
    scope = "service" if role == reg.ROLE_SERVICE else "ssh"
    section = "kuma-services" if role == reg.ROLE_SERVICE else "kuma-ssh"

    def _after_bind_redirect(*, msg: str = "", error: str = "", detail: str = ""):
        """Honor safe relative next= (e.g. Network coverage) or fall back to integration."""
        nxt = (next or "").strip()
        if (
            nxt.startswith("/")
            and not nxt.startswith("//")
            and "://" not in nxt
            and "\n" not in nxt
        ):
            from urllib.parse import quote

            sep = "&" if "?" in nxt else "?"
            if msg:
                nxt = f"{nxt}{sep}msg={quote(msg)}"
                sep = "&"
            if error:
                nxt = f"{nxt}{sep}error={quote(error)}"
                if detail:
                    nxt = f"{nxt}&detail={quote(detail[:200])}"
            return RedirectResponse(nxt, status_code=303)
        if error:
            return _redirect(
                f"/integrations/{integration_id}",
                fragment=section,
                error=error,
                detail=detail,
                scope=scope,
            )
        return _redirect(
            f"/integrations/{integration_id}",
            fragment=section,
            msg=msg or "binding_saved",
            scope=scope,
        )

    try:
        reg.set_binding(
            session,
            integration_id=integration_id,
            server_id=server_id,
            external_id=external_id,
            role=role,
            docker_project=docker_project if role == reg.ROLE_SERVICE else None,
            docker_container=docker_container if role == reg.ROLE_SERVICE else None,
            external_label=mon_label,
            external_meta=mon_meta,
            last_state=mon_state,
            last_message=mon_msg,
            binding_id=binding_id_int,
        )
        _audit(
            session,
            user,
            "integration_binding_set",
            server_id=server_id,
            details=(
                f"integration={integration_id} role={role} monitor={external_id}"
                f" project={docker_project} container={docker_container}"
            ),
        )
        return _after_bind_redirect(msg="binding_saved")
    except ValueError as e:
        return _after_bind_redirect(error="binding_failed", detail=str(e)[:200])
    except Exception as e:
        logger.exception("integration binding failed")
        return _after_bind_redirect(error="binding_failed", detail=str(e)[:200])


@router.post("/integrations/{integration_id}/suggest-bindings")
async def suggest_bindings(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    """Apply auto-suggestions for unbound servers only."""
    integration = reg.get_integration(session, integration_id)
    if not integration:
        raise HTTPException(404)
    monitors = reg.monitors_from_cache(integration)
    mon_objs = [
        kuma.KumaMonitor(
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
        for m in monitors
        if m.get("id") is not None
    ]
    by_id = {m.id: m for m in mon_objs}
    ssh_bound = {
        b.server_id
        for b in reg.list_bindings(
            session, integration_id=integration_id, role=reg.ROLE_SSH
        )
    }
    servers = list(session.exec(select(Server)).all())
    applied = 0
    for s in servers:
        if s.id in ssh_bound:
            continue
        sug = kuma.suggest_monitor_for_server(
            mon_objs,
            hostname=s.hostname or "",
            ip_address=s.ip_address or "",
            ssh_port=s.ssh_port or 22,
        )
        if not sug:
            continue
        mon = by_id.get(sug.id) or sug
        meta = mon.to_dict()
        reg.set_binding(
            session,
            integration_id=integration_id,
            server_id=s.id,
            external_id=sug.id,
            role=reg.ROLE_SSH,
            external_label=sug.name,
            external_meta=meta,
            last_state=sug.status,
            last_message=reg.binding_message_from_monitor(sug),
        )
        applied += 1
    _audit(
        session,
        user,
        "integration_bindings_suggested",
        details=f"integration={integration_id} applied={applied}",
    )
    return _redirect(
        f"/integrations/{integration_id}",
        fragment="kuma-ssh",
        msg="suggested",
        detail=str(applied),
        scope="ssh",
    )

# Product-specific routes attach to the same router
from . import integrations_pihole as _integrations_pihole  # noqa: E402,F401
from . import integrations_npm as _integrations_npm  # noqa: E402,F401

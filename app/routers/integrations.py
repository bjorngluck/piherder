"""Integrations hub — Uptime Kuma, Grafana, Pi-hole, Nginx Proxy Manager."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

# json used for docker_options_json in detail view

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from .. import templates as templates_mod
from ..database import get_session
from ..models import AuditLog, Integration, Server, User
from ..security.auth import get_current_user, get_operator_user, role_at_least, ROLE_OPERATOR
from ..services.integrations import grafana as gf
from ..services.integrations import npm as npm_mod
from ..services.integrations import pihole as ph
from ..services.integrations import poll as poll_svc
from ..services.integrations import registry as reg
from ..services.integrations import uptime_kuma as kuma

logger = logging.getLogger(__name__)
router = APIRouter(tags=["integrations"])


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
        session.add(
            AuditLog(
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


def _redirect(path: str, *, fragment: str | None = None, **params) -> RedirectResponse:
    """303 redirect; optional URL fragment keeps scroll position on long pages."""
    if params:
        path = f"{path}?{urlencode({k: v for k, v in params.items() if v is not None})}"
    frag = (fragment or "").strip().lstrip("#")
    if frag:
        path = f"{path}#{frag}"
    return RedirectResponse(path, status_code=303)


def _can_mutate(user: User) -> bool:
    return role_at_least(user, ROLE_OPERATOR)


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
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_list.html",
        context={
            "title": "Integrations",
            "user": user,
            "integrations": items,
            "pihole_summary": pihole_summary,
            "can_mutate": _can_mutate(user),
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


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


@router.get("/integrations/new/grafana", response_class=HTMLResponse)
async def grafana_new_form(
    request: Request,
    user: User = Depends(get_operator_user),
):
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_grafana_form.html",
        context={
            "title": "Add Grafana",
            "user": user,
            "mode": "new",
            "integration": None,
            "form": {
                "name": "Grafana",
                "base_url": "",
                "poll_interval_sec": reg.DEFAULT_GRAFANA_POLL_SEC,
                "tls_verify": True,
                "enabled": True,
                "query_template": reg.DEFAULT_QT_HOST,
                "query_template_container_host": reg.DEFAULT_QT_CONTAINER_HOST,
                "query_template_container": reg.DEFAULT_QT_CONTAINER,
                "query_template_logs": reg.DEFAULT_QT_LOGS,
            },
            "has_key": False,
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


@router.post("/integrations/new/grafana")
async def grafana_create(
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    name: str = Form("Grafana"),
    base_url: str = Form(...),
    api_key: str = Form(""),
    poll_interval_sec: int = Form(reg.DEFAULT_GRAFANA_POLL_SEC),
    tls_verify: Optional[str] = Form(None),
    enabled: Optional[str] = Form("on"),
    query_template: str = Form(""),
    query_template_container_host: str = Form(""),
    query_template_container: str = Form(""),
    query_template_logs: str = Form(""),
):
    try:
        row = reg.create_grafana(
            session,
            name=name,
            base_url=base_url,
            api_key=api_key,
            poll_interval_sec=poll_interval_sec,
            tls_verify_flag=tls_verify in ("on", "1", "true"),
            enabled=enabled in ("on", "1", "true") if enabled is not None else True,
            query_template=query_template,
            query_template_container_host=query_template_container_host,
            query_template_container=query_template_container,
            query_template_logs=query_template_logs,
        )
        poll_svc.poll_integration(row.id, notify=False)
        _audit(
            session,
            user,
            "integration_created",
            details=f"grafana id={row.id} name={row.name}",
        )
        return _redirect(f"/integrations/{row.id}", msg="created")
    except ValueError as e:
        return _redirect(
            "/integrations/new/grafana", error="invalid", detail=str(e)[:200]
        )
    except Exception as e:
        logger.exception("create grafana failed")
        return _redirect(
            "/integrations/new/grafana", error="save_failed", detail=str(e)[:200]
        )


@router.get("/integrations/new/pihole", response_class=HTMLResponse)
async def pihole_new_form(
    request: Request,
    user: User = Depends(get_operator_user),
):
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_pihole_form.html",
        context={
            "title": "Add Pi-hole",
            "user": user,
            "mode": "create",
            "integration": None,
            "form": {
                "name": "Pi-hole",
                "base_url": "https://pihole.hacknow.info",
                "poll_interval_sec": reg.DEFAULT_PIHOLE_POLL_SEC,
                "tls_verify": True,
                "enabled": True,
                "is_primary": False,
            },
            "has_password": False,
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


@router.post("/integrations/new/pihole")
async def pihole_create(
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    name: str = Form("Pi-hole"),
    base_url: str = Form(...),
    password: str = Form(...),
    poll_interval_sec: int = Form(reg.DEFAULT_PIHOLE_POLL_SEC),
    tls_verify: Optional[str] = Form(None),
    enabled: Optional[str] = Form("on"),
    is_primary: Optional[str] = Form(None),
):
    try:
        base = ph.normalize_base_url(base_url)
        result = ph.fetch_stats(
            base, password, tls_verify=tls_verify in ("on", "1", "true")
        )
        if not result.ok:
            return _redirect(
                "/integrations/new/pihole",
                error="test_failed",
                detail=(result.error or "failed")[:200],
            )
        row = reg.create_pihole(
            session,
            name=name,
            base_url=base,
            password=password,
            poll_interval_sec=poll_interval_sec,
            tls_verify_flag=tls_verify in ("on", "1", "true"),
            enabled=enabled in ("on", "1", "true") if enabled is not None else True,
            is_primary=is_primary in ("on", "1", "true"),
        )
        poll_svc.poll_integration(row.id, notify=False)
        _audit(
            session,
            user,
            "integration_created",
            details=f"pihole id={row.id} name={row.name}",
        )
        return _redirect(f"/integrations/{row.id}", msg="created")
    except ValueError as e:
        return _redirect(
            "/integrations/new/pihole", error="invalid", detail=str(e)[:200]
        )
    except Exception as e:
        logger.exception("create pihole failed")
        return _redirect(
            "/integrations/new/pihole", error="save_failed", detail=str(e)[:200]
        )


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
                "base_url": "https://nginx.hacknow.info",
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
        return await _grafana_detail(request, session, user, integration)
    if integration.type == reg.TYPE_PIHOLE:
        return await _pihole_detail(request, session, user, integration)
    if integration.type == reg.TYPE_NPM:
        return await _npm_detail(request, session, user, integration)
    if integration.type != reg.TYPE_UPTIME_KUMA:
        raise HTTPException(400, "Unsupported integration type in UI yet")

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


async def _grafana_detail(
    request: Request,
    session: Session,
    user: User,
    integration: Integration,
):
    servers = list(session.exec(select(Server).order_by(Server.name)).all())
    bindings = reg.list_bindings(
        session, integration_id=integration.id, role=reg.ROLE_DASHBOARD
    )
    dashboards = reg.dashboards_from_cache(integration)
    status = reg.parse_last_status(integration)
    server_by_id = {s.id: s for s in servers}
    uid_counts: dict[str, int] = {}
    for b in bindings:
        uid = (b.external_id or "").strip()
        if uid:
            uid_counts[uid] = uid_counts.get(uid, 0) + 1
    bound_rows = []
    for b in bindings:
        srv = server_by_id.get(b.server_id)
        chip = reg._grafana_chip_dict(integration, b, server=srv)
        chip["server_name"] = srv.name if srv else f"#{b.server_id}"
        chip["uid"] = b.external_id
        uid = (b.external_id or "").strip()
        chip["same_uid_count"] = uid_counts.get(uid, 1)
        bound_rows.append(chip)

    def _sort_key(c: dict) -> tuple:
        return (
            (c.get("server_name") or "").lower(),
            (c.get("location") or "").lower(),
            (c.get("label") or "").lower(),
            c.get("id") or 0,
        )

    by_kind = {
        reg.GRAFANA_KIND_METRICS: [],
        reg.GRAFANA_KIND_CONTAINERS: [],
        reg.GRAFANA_KIND_LOGS: [],
    }
    for row in bound_rows:
        # chip dict already uses binding_grafana_kind (docker scope → containers)
        k = reg.normalize_grafana_kind(row.get("kind"))
        by_kind.setdefault(k, []).append(row)
    for k in by_kind:
        by_kind[k].sort(key=_sort_key)

    tab = (request.query_params.get("tab") or "metrics").strip().lower()
    if tab not in ("metrics", "containers", "logs", "inventory"):
        tab = "metrics"

    # Prefill bind form for clone/new only (rename is inline on each row)
    prefill = {
        "mode": (request.query_params.get("mode") or "").strip().lower(),  # clone|""
        "server_id": (request.query_params.get("server_id") or "").strip(),
        "kind": reg.normalize_grafana_kind(request.query_params.get("kind") or tab),
        "external_id": (request.query_params.get("external_id") or "").strip(),
        "docker_project": (request.query_params.get("docker_project") or "").strip(),
        "docker_container": (request.query_params.get("docker_container") or "").strip(),
        "display_name": (request.query_params.get("display_name") or "").strip(),
    }
    if prefill["mode"] != "clone":
        prefill["mode"] = ""

    docker_options: dict[int, list] = {}
    for s in servers:
        docker_options[s.id] = reg.docker_inventory_options(session, s.id)

    # Inventory rows: Grafana dashboards + preferred name + binding counts
    pref_map = reg.preferred_display_names(integration)
    uid_bind_counts: dict[str, int] = {}
    for b in bindings:
        u = (b.external_id or "").strip()
        if u:
            uid_bind_counts[u] = uid_bind_counts.get(u, 0) + 1
    inventory_rows = []
    for d in dashboards:
        u = str(d.get("uid") or "").strip()
        inventory_rows.append(
            {
                "uid": u,
                "title": d.get("title") or d.get("name") or u,
                "folder_title": d.get("folder_title") or "",
                "url": d.get("url") or "",
                "preferred_name": pref_map.get(u) or "",
                "binding_count": uid_bind_counts.get(u, 0),
            }
        )
    # Preferred names for UIDs not currently in inventory (orphan config)
    known_uids = {r["uid"] for r in inventory_rows if r["uid"]}
    for u, name in sorted(pref_map.items()):
        if u not in known_uids:
            inventory_rows.append(
                {
                    "uid": u,
                    "title": "(not in last poll inventory)",
                    "folder_title": "",
                    "url": "",
                    "preferred_name": name,
                    "binding_count": uid_bind_counts.get(u, 0),
                }
            )

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_grafana_detail.html",
        context={
            "title": integration.name,
            "user": user,
            "integration": integration,
            "can_mutate": _can_mutate(user),
            "servers": servers,
            "dashboards": dashboards,
            "inventory_rows": inventory_rows,
            "bindings": bound_rows,
            "bindings_metrics": by_kind.get(reg.GRAFANA_KIND_METRICS) or [],
            "bindings_containers": by_kind.get(reg.GRAFANA_KIND_CONTAINERS) or [],
            "bindings_logs": by_kind.get(reg.GRAFANA_KIND_LOGS) or [],
            "counts": {
                "metrics": len(by_kind.get(reg.GRAFANA_KIND_METRICS) or []),
                "containers": len(by_kind.get(reg.GRAFANA_KIND_CONTAINERS) or []),
                "logs": len(by_kind.get(reg.GRAFANA_KIND_LOGS) or []),
                "all": len(bound_rows),
            },
            "tab": tab,
            "prefill": prefill,
            "status": status,
            "has_key": reg.has_credentials(integration),
            "poll_interval_sec": reg.poll_interval_sec(integration),
            "tls_verify": reg.tls_verify(integration),
            "query_template": reg.query_template(integration),
            "query_template_container_host": reg.query_template_container_host(integration),
            "query_template_container": reg.query_template_container(integration),
            "query_template_logs": reg.query_template_logs(integration),
            "docker_options_json": json.dumps(docker_options),
            "open_url": gf.open_grafana_url(integration.base_url),
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


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
    base_url: str = Form(...),
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
):
    integration = reg.get_integration(session, integration_id)
    if not integration:
        raise HTTPException(404)
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


@router.post("/integrations/{integration_id}/preferred-name")
async def set_grafana_preferred_name(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    uid: str = Form(...),
    display_name: str = Form(""),
):
    """Set preferred PiHerder chip name for a Grafana dashboard UID (Inventory tab)."""
    integration = reg.get_integration(session, integration_id)
    if not integration:
        raise HTTPException(404)
    if integration.type != reg.TYPE_GRAFANA:
        raise HTTPException(400, "Preferred names are for Grafana integrations")
    try:
        updated = reg.apply_grafana_preferred_name(
            session,
            integration_id=integration_id,
            uid=uid,
            display_name=display_name,
        )
        name_bit = (display_name or "").strip()
        _audit(
            session,
            user,
            "integration_preferred_name",
            details=(
                f"integration={integration_id} uid={(uid or '')[:80]!r}"
                f" preferred={name_bit[:80]!r} bindings_synced={len(updated)}"
            ),
        )
        if name_bit:
            detail = f"{name_bit[:60]!r} · {len(updated)} binding(s) synced"
        else:
            detail = f"cleared for UID · {len(updated)} binding(s) follow Grafana title"
        return _redirect(
            f"/integrations/{integration_id}",
            msg="preferred_saved",
            detail=detail,
            scope="dashboard",
            tab="inventory",
        )
    except ValueError as e:
        return _redirect(
            f"/integrations/{integration_id}",
            error="binding_failed",
            detail=str(e)[:200],
            scope="dashboard",
            tab="inventory",
        )
    except Exception as e:
        logger.exception("grafana preferred name failed")
        return _redirect(
            f"/integrations/{integration_id}",
            error="binding_failed",
            detail=str(e)[:200],
            scope="dashboard",
            tab="inventory",
        )


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
        return _redirect(
            f"/integrations/{integration_id}",
            fragment=section,
            msg="binding_saved",
            scope=scope,
        )
    except ValueError as e:
        return _redirect(
            f"/integrations/{integration_id}",
            fragment=section,
            error="binding_failed",
            detail=str(e)[:200],
            scope=scope,
        )
    except Exception as e:
        logger.exception("integration binding failed")
        return _redirect(
            f"/integrations/{integration_id}",
            fragment=section,
            error="binding_failed",
            detail=str(e)[:200],
            scope=scope,
        )


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


# ---------------------------------------------------------------------------
# Pi-hole detail + DNS / actions
# ---------------------------------------------------------------------------


async def _pihole_detail(request, session, user, integration: Integration):
    status = reg.parse_last_status(integration)
    tab = (request.query_params.get("tab") or "overview").strip().lower()
    hosts: list = []
    cnames: list = []
    dns_error = ""
    if tab in ("dns", "cname") and reg.pihole_password(integration):
        try:
            sess = ph.login(
                integration.base_url,
                reg.pihole_password(integration),
                tls_verify=reg.tls_verify(integration),
            )
            try:
                if tab == "dns":
                    hosts = ph.list_dns_hosts(sess)
                else:
                    cnames = ph.list_dns_cnames(sess)
            finally:
                ph.logout(sess)
        except Exception as e:
            dns_error = str(e)[:300]
    # All pihole instances for fan-out context
    others = [
        r
        for r in reg.list_integrations(session, type_filter=reg.TYPE_PIHOLE)
        if r.id != integration.id
    ]
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_pihole_detail.html",
        context={
            "title": integration.name,
            "user": user,
            "integration": integration,
            "status": status,
            "is_primary": reg.is_pihole_primary(integration),
            "tab": tab,
            "hosts": hosts,
            "cnames": cnames,
            "dns_error": dns_error,
            "other_piholes": others,
            "admin_url": ph.admin_url(integration.base_url),
            "gravity_url": ph.admin_url(integration.base_url, "/gravity"),
            "system_url": ph.admin_url(integration.base_url, "/settings/system"),
            "can_mutate": _can_mutate(user),
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


async def _npm_detail(request, session, user, integration: Integration):
    status = reg.parse_last_status(integration)
    tab = (request.query_params.get("tab") or "hosts").strip().lower()
    servers = list(session.exec(select(Server).order_by(Server.sort_order, Server.name)).all())
    bindings = reg.list_bindings(
        session, integration_id=integration.id, role=reg.ROLE_PROXY_HOST
    )
    bind_by_ext = {b.external_id: b for b in bindings}
    proxy_hosts = status.get("proxy_hosts") or []
    certificates = status.get("certificates") or []
    docker_options = {}
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
            "docker_options_json": json.dumps(docker_options),
            "managed_certs": managed,
            "open_url": npm_mod.open_npm_url(integration.base_url),
            "can_mutate": _can_mutate(user),
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


def _fanout_pihole_dns(
    session: Session,
    *,
    op: str,
    kind: str,
    ip: str = "",
    domain: str = "",
    target: str = "",
) -> list[dict]:
    """Apply DNS mutation to primary first, then all other enabled piholes."""
    rows = [
        r
        for r in reg.list_integrations(session, type_filter=reg.TYPE_PIHOLE)
        if r.enabled
    ]
    # Primary first
    rows.sort(key=lambda r: (0 if reg.is_pihole_primary(r) else 1, r.id or 0))
    results = []
    for r in rows:
        item = {"id": r.id, "name": r.name, "ok": False, "error": ""}
        try:
            sess = ph.login(
                r.base_url,
                reg.pihole_password(r),
                tls_verify=reg.tls_verify(r),
            )
            try:
                if kind == "host":
                    if op == "add":
                        ph.add_dns_host(sess, ip, domain)
                    else:
                        ph.delete_dns_host(sess, ip, domain)
                else:
                    if op == "add":
                        ph.add_dns_cname(sess, domain, target)
                    else:
                        ph.delete_dns_cname(sess, domain, target)
                item["ok"] = True
            finally:
                ph.logout(sess)
        except Exception as e:
            item["error"] = str(e)[:200]
        results.append(item)
    return results


@router.post("/integrations/{integration_id}/pihole/dns-host")
async def pihole_dns_host(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    action: str = Form("add"),
    ip: str = Form(""),
    domain: str = Form(""),
):
    integration = reg.get_integration(session, integration_id)
    if not integration or integration.type != reg.TYPE_PIHOLE:
        raise HTTPException(404)
    ip = (ip or "").strip()
    domain = (domain or "").strip()
    if not ip or not domain:
        return _redirect(
            f"/integrations/{integration_id}",
            tab="dns",
            error="invalid",
            detail="IP and domain required",
        )
    op = "add" if action == "add" else "delete"
    results = _fanout_pihole_dns(
        session, op=op, kind="host", ip=ip, domain=domain
    )
    ok_n = sum(1 for r in results if r["ok"])
    fail = [r for r in results if not r["ok"]]
    _audit(
        session,
        user,
        f"pihole_dns_host_{op}",
        details=f"{ip} {domain} ok={ok_n}/{len(results)}",
        status="success" if not fail else "partial",
    )
    detail = f"{ok_n}/{len(results)} instances"
    if fail:
        detail += " · " + "; ".join(f"{f['name']}: {f['error']}" for f in fail)[:180]
    return _redirect(
        f"/integrations/{integration_id}",
        tab="dns",
        msg="dns_ok" if not fail else "dns_partial",
        detail=detail,
    )


@router.post("/integrations/{integration_id}/pihole/dns-cname")
async def pihole_dns_cname(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    action: str = Form("add"),
    domain: str = Form(""),
    target: str = Form(""),
):
    integration = reg.get_integration(session, integration_id)
    if not integration or integration.type != reg.TYPE_PIHOLE:
        raise HTTPException(404)
    domain = (domain or "").strip()
    target = (target or "").strip()
    if not domain or not target:
        return _redirect(
            f"/integrations/{integration_id}",
            tab="cname",
            error="invalid",
            detail="domain and target required",
        )
    op = "add" if action == "add" else "delete"
    results = _fanout_pihole_dns(
        session, op=op, kind="cname", domain=domain, target=target
    )
    ok_n = sum(1 for r in results if r["ok"])
    fail = [r for r in results if not r["ok"]]
    _audit(
        session,
        user,
        f"pihole_dns_cname_{op}",
        details=f"{domain} -> {target} ok={ok_n}/{len(results)}",
        status="success" if not fail else "partial",
    )
    detail = f"{ok_n}/{len(results)} instances"
    if fail:
        detail += " · " + "; ".join(f"{f['name']}: {f['error']}" for f in fail)[:180]
    return _redirect(
        f"/integrations/{integration_id}",
        tab="cname",
        msg="dns_ok" if not fail else "dns_partial",
        detail=detail,
    )


@router.post("/integrations/{integration_id}/pihole/action")
async def pihole_action(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    action: str = Form(...),
    all_instances: Optional[str] = Form(None),
):
    integration = reg.get_integration(session, integration_id)
    if not integration or integration.type != reg.TYPE_PIHOLE:
        raise HTTPException(404)
    act = (action or "").strip().lower()
    if act not in ("gravity", "restartdns", "flush_network"):
        return _redirect(
            f"/integrations/{integration_id}",
            tab="actions",
            error="invalid",
            detail="unknown action",
        )
    targets = [integration]
    if all_instances in ("on", "1", "true"):
        targets = [
            r
            for r in reg.list_integrations(session, type_filter=reg.TYPE_PIHOLE)
            if r.enabled
        ]
    results = []
    for r in targets:
        item = {"name": r.name, "ok": False, "error": "", "output": ""}
        try:
            sess = ph.login(
                r.base_url,
                reg.pihole_password(r),
                tls_verify=reg.tls_verify(r),
            )
            try:
                out = ph.run_action(sess, act)
                item["ok"] = True
                item["output"] = (out or "")[:500]
            finally:
                ph.logout(sess)
        except Exception as e:
            item["error"] = str(e)[:200]
        results.append(item)
    ok_n = sum(1 for r in results if r["ok"])
    _audit(
        session,
        user,
        f"pihole_action_{act}",
        details=f"ok={ok_n}/{len(results)}",
        status="success" if ok_n == len(results) else "partial",
    )
    fail = [r for r in results if not r["ok"]]
    detail = f"{ok_n}/{len(results)} ok"
    if fail:
        detail += " · " + "; ".join(f"{f['name']}: {f['error']}" for f in fail)[:160]
    return _redirect(
        f"/integrations/{integration_id}",
        tab="actions",
        msg="action_ok" if not fail else "action_partial",
        detail=detail,
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

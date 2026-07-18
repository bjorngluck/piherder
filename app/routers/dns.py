"""End-to-end DNS fabric UI and mutations."""
from __future__ import annotations

import json
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session

from ..database import get_session
from ..models import Server, User
from ..security.auth import get_admin_user, get_current_user, get_operator_user, user_role
from ..services import dns_fabric as fabric
from ..services.app_settings import load_settings, save_settings
from ..services.audit_write import make_audit_log
from ..templates import templates

router = APIRouter()


def _can_mutate(user: User) -> bool:
    return user_role(user) in ("admin", "operator")


def _redirect(path: str = "/dns", *, msg: str = "", error: str = "") -> RedirectResponse:
    """Redirect with optional msg/error. Path may already include a query string."""
    q = []
    if msg:
        q.append(f"msg={quote(msg)}")
    if error:
        q.append(f"error={quote(error[:300])}")
    if not q:
        return RedirectResponse(path, status_code=303)
    sep = "&" if "?" in path else "?"
    return RedirectResponse(path + sep + "&".join(q), status_code=303)


def _dns_page_context(
    request: Request,
    session: Session,
    user: User,
    *,
    page: str = "index",
) -> dict:
    # Build only the topology payload the page needs (GET-safe, no DB writes).
    # Pi-hole adopt candidates are loaded lazily via GET /dns/candidates (HTMX).
    view = fabric.build_fabric_view(
        session,
        include_mesh=False,
        include_physical=(page == "physical"),
        include_logical=(page == "logical"),
        persist_links=False,
    )
    settings = load_settings()
    base = (settings.get("dns_base_domain") or "").strip()
    focus = (request.query_params.get("focus") or "").strip()
    coverage_filter = (request.query_params.get("coverage_filter") or "all").strip().lower()
    if coverage_filter not in ("all", "none", "public", "strict"):
        coverage_filter = "all"
    kuma_opts = fabric.list_kuma_monitor_options(session) if page == "index" else []
    # Path-gap filters only on the dedicated coverage page
    kc = view.get("kuma_coverage") or {}
    if page == "coverage" and kc.get("gaps") is not None:
        try:
            from ..services.dns_fabric.kuma_coverage import filter_path_gaps

            kc = dict(kc)
            kc["gaps_filtered"] = filter_path_gaps(
                list(kc.get("gaps") or []), mode=coverage_filter
            )
            kc["coverage_filter"] = coverage_filter
            view = dict(view)
            view["kuma_coverage"] = kc
        except Exception:
            pass
    stats = view.get("stats") or {}
    catalog_pulse = {
        "health": "ok",
        "primary": stats.get("services") or 0,
        "primary_label": "names",
        "bar": [
            {
                "n": (stats.get("services") or 0) - (stats.get("via_proxy") or 0) or 0.001,
                "cls": "ops-bar--ok",
                "title": "direct",
            },
            {
                "n": stats.get("via_proxy") or 0.001,
                "color": "var(--color-warning, #d97706)",
                "title": "via npm",
            },
        ],
        "line1": [
            {
                "n": f"{stats.get('hosts_named', 0)}/{stats.get('hosts_total', 0)}",
                "l": "named",
                "cls": "text-accent",
            },
            {"n": stats.get("services") or 0, "l": "mapped", "cls": ""},
            {"n": stats.get("via_proxy") or 0, "l": "via npm", "cls": "text-warning"},
            {"n": stats.get("checklist") or 0, "l": "checklist", "cls": ""},
        ],
        "line2": [
            {"n": stats.get("hosts_total") or 0, "l": "hosts", "cls": ""},
            {"n": stats.get("hosts_named") or 0, "l": "dns", "cls": ""},
            {
                "n": stats.get("kuma_gaps") or 0,
                "l": "path gaps",
                "cls": "text-warning" if (stats.get("kuma_gaps") or 0) else "",
            },
            {
                "n": stats.get("kuma_dep_gaps") or 0,
                "l": "dep gaps",
                "cls": "text-warning" if (stats.get("kuma_dep_gaps") or 0) else "",
            },
        ],
        "caption": "Network maps · hosts & paths · Kuma coverage",
    }
    return {
        "request": request,
        "user": user,
        "can_mutate": _can_mutate(user),
        "view": view,
        "catalog_pulse": catalog_pulse,
        "mesh": view.get("mesh") or {},
        "physical": view.get("physical") or {},
        "logical": view.get("logical") or {},
        "dns_base_domain": base,
        "network_lan_subnet": (settings.get("network_lan_subnet") or "").strip(),
        "network_gateway_ip": (settings.get("network_gateway_ip") or "").strip(),
        "network_public_ip": (settings.get("network_public_ip") or "").strip(),
        "network_public_ip_checked_at": (
            settings.get("network_public_ip_checked_at") or ""
        ).strip(),
        "network_gateway_kuma_external_id": (
            settings.get("network_gateway_kuma_external_id") or ""
        ).strip(),
        "network_public_kuma_external_id": (
            settings.get("network_public_kuma_external_id") or ""
        ).strip(),
        "network_kuma_integration_id": (
            settings.get("network_kuma_integration_id") or ""
        ).strip(),
        "network_kuma_monitors": kuma_opts,
        "stack_inventory_down_alerts": bool(
            settings.get("stack_inventory_down_alerts")
            if settings.get("stack_inventory_down_alerts") is not None
            else True
        ),
        "msg": request.query_params.get("msg") or "",
        "error": request.query_params.get("error") or "",
        "detail": request.query_params.get("detail") or "",
        "catalog_section": "dns",
        "dns_page": page,
        "focus_path_id": focus,
        "coverage_filter": coverage_filter if page == "coverage" else "all",
    }


@router.get("/dns", response_class=HTMLResponse)
async def dns_list(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """DNS hub: maps teaser + service paths + adopt/settings. Coverage is /dns/coverage."""
    ctx = _dns_page_context(request, session, user, page="index")
    return templates.TemplateResponse(
        request=request,
        name="dns_list.html",
        context=ctx,
    )


@router.get("/dns/coverage", response_class=HTMLResponse)
async def dns_coverage(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Kuma path + dependency coverage audit (split from hub to reduce clutter)."""
    ctx = _dns_page_context(request, session, user, page="coverage")
    ctx["title"] = "Kuma coverage"
    return templates.TemplateResponse(
        request=request,
        name="dns_coverage.html",
        context=ctx,
    )


@router.get("/dns/stack-panel", response_class=HTMLResponse)
async def dns_stack_panel(
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    service_id: Optional[int] = None,
    server_id: Optional[int] = None,
    project: Optional[str] = None,
    force: Optional[str] = None,
):
    """HTMX partial: runtime stack side panel (one project / path at a time).

    ``force=1`` schedules a background Docker inventory refresh (P1b on-command)
    so compose ``depends_on`` re-enriches; panel returns last good snapshot now.
    """
    from ..services.dns_fabric.stack_panel import build_stack_panel
    from ..services import docker_inventory as inv_svc

    sid = service_id
    if sid is None:
        raw = (request.query_params.get("service_id") or "").strip()
        if raw.isdigit():
            sid = int(raw)
    srv = server_id
    if srv is None:
        raw = (request.query_params.get("server_id") or "").strip()
        if raw.isdigit():
            srv = int(raw)
    proj = (project or request.query_params.get("project") or "").strip() or None
    do_force = (force or request.query_params.get("force") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    focus_container = (
        request.query_params.get("focus_container")
        or request.query_params.get("container")
        or ""
    ).strip() or None

    panel = build_stack_panel(
        session,
        service_id=sid,
        server_id=srv,
        project=proj,
    )
    if panel.get("ok") and focus_container:
        panel["focus_container"] = focus_container

    if do_force and panel.get("ok") and panel.get("server_id"):
        try:
            server = session.get(Server, int(panel["server_id"]))
            if server:
                kicked = inv_svc.request_refresh(
                    background_tasks,
                    int(panel["server_id"]),
                    force=True,
                    server=server,
                    session=session,
                )
                panel["refresh_note"] = (
                    "Refreshing inventory from host… tap Refresh again in a few seconds."
                    if kicked
                    else "Inventory refresh already running or not needed."
                )
            else:
                panel["refresh_note"] = "Host not found for refresh."
        except Exception:
            panel["refresh_note"] = "Could not schedule inventory refresh."

    return templates.TemplateResponse(
        request=request,
        name="partials/dns_stack_panel.html",
        context={
            "request": request,
            "user": user,
            "can_mutate": _can_mutate(user),
            "panel": panel,
        },
    )


def _stack_next(next_path: str, *, service_id: Optional[str] = None) -> str:
    """Return path after edge mutation — prefer stack deep-link."""
    dest = _safe_next_path(next_path, default="")
    if dest:
        return dest
    if service_id and str(service_id).isdigit():
        return f"/dns?stack={int(service_id)}"
    return "/dns"


@router.post("/dns/stack-order")
async def stack_save_order(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    server_id: int = Form(...),
    project: str = Form(...),
    order: str = Form(...),  # JSON array of container names
    service_id: str = Form(""),
    next: str = Form(""),
):
    """Save operator container order for stack panel + map expand.

    Returns JSON when Accept includes application/json (stack panel AJAX);
    otherwise redirects for form POSTs.
    """
    from ..services import stack_order as so_svc

    want_json = "application/json" in (request.headers.get("accept") or "").lower()

    proj = (project or "").strip()
    if not proj:
        if want_json:
            return JSONResponse(
                {"ok": False, "error": "order_need_project"}, status_code=400
            )
        return _redirect(
            _stack_next(next, service_id=service_id), error="order_need_project"
        )
    try:
        names = json.loads(order) if isinstance(order, str) else list(order or [])
        if not isinstance(names, list):
            names = []
    except Exception:
        names = [x.strip() for x in (order or "").split(",") if x.strip()]
    saved = so_svc.set_order(int(server_id), proj, [str(n) for n in names])
    try:
        session.add(
            make_audit_log(
                action="fabric.stack_order",
                status="success",
                user_id=user.id,
                server_id=int(server_id),
                details=f"{proj}: {', '.join(str(n) for n in saved)[:200]}",
            )
        )
        session.commit()
    except Exception:
        pass
    if want_json:
        return JSONResponse(
            {
                "ok": True,
                "msg": "order_saved",
                "server_id": int(server_id),
                "project": proj,
                "order": saved,
            }
        )
    return _redirect(_stack_next(next, service_id=service_id), msg="order_saved")


@router.get("/dns/stack-expand.json")
async def stack_expand_json(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    service_id: Optional[int] = None,
    server_id: Optional[int] = None,
    project: Optional[str] = None,
):
    """JSON payload for path-map stack blow-up (P4) — one stack's containers + confirmed edges."""
    del user
    from ..services.dns_fabric.stack_expand import build_stack_expand_payload

    payload = build_stack_expand_payload(
        session,
        service_id=service_id,
        server_id=server_id,
        project=project,
    )
    status = 200 if payload.get("ok") else 404
    return JSONResponse(payload, status_code=status)


@router.post("/dns/stack-edges/accept")
async def stack_edge_accept(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    from_server_id: int = Form(...),
    from_project: str = Form(...),
    from_container: str = Form(""),
    to_server_id: int = Form(...),
    to_project: str = Form(...),
    to_container: str = Form(""),
    kind: str = Form("depends_on"),
    confidence: int = Form(85),
    service_id: str = Form(""),
    next: str = Form(""),
):
    """Accept a suggested runtime edge (P2)."""
    from ..services import runtime_edges as re_svc

    try:
        row = re_svc.accept_suggestion(
            session,
            from_server_id=from_server_id,
            from_project=from_project,
            from_container=from_container or None,
            to_server_id=to_server_id,
            to_project=to_project,
            to_container=to_container or None,
            kind=kind,
            confidence=confidence,
            user_id=user.id,
        )
        session.add(
            make_audit_log(
                action="fabric.edge_accept",
                status="success",
                user_id=user.id,
                server_id=from_server_id,
                details=(
                    f"{from_project}/{from_container or '*'} → "
                    f"{to_project}/{to_container or '*'} "
                    f"(edge_id={row.id})"
                ),
            )
        )
        session.commit()
        return _redirect(_stack_next(next, service_id=service_id), msg="edge_accepted")
    except Exception as e:
        return _redirect(
            _stack_next(next, service_id=service_id),
            error=f"edge_accept_failed: {str(e)[:120]}",
        )


@router.post("/dns/stack-edges/dismiss")
async def stack_edge_dismiss(
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    from_server_id: int = Form(...),
    from_project: str = Form(...),
    from_container: str = Form(""),
    to_server_id: int = Form(...),
    to_project: str = Form(...),
    to_container: str = Form(""),
    service_id: str = Form(""),
    next: str = Form(""),
):
    """Dismiss a suggestion so it does not re-appear (P2)."""
    from ..services import runtime_edges as re_svc

    try:
        re_svc.dismiss_suggestion(
            session,
            from_server_id=from_server_id,
            from_project=from_project,
            from_container=from_container or None,
            to_server_id=to_server_id,
            to_project=to_project,
            to_container=to_container or None,
            user_id=user.id,
        )
        session.add(
            make_audit_log(
                action="fabric.edge_dismiss",
                status="success",
                user_id=user.id,
                server_id=from_server_id,
                details=(
                    f"{from_project}/{from_container or '*'} → "
                    f"{to_project}/{to_container or '*'}"
                ),
            )
        )
        session.commit()
        return _redirect(_stack_next(next, service_id=service_id), msg="edge_dismissed")
    except Exception as e:
        return _redirect(
            _stack_next(next, service_id=service_id),
            error=f"edge_dismiss_failed: {str(e)[:120]}",
        )


@router.post("/dns/stack-edges/manual")
async def stack_edge_manual(
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    from_server_id: int = Form(...),
    from_project: str = Form(...),
    from_container: str = Form(""),
    to_server_id: int = Form(...),
    to_project: str = Form(...),
    to_container: str = Form(""),
    kind: str = Form("talks_to"),
    note: str = Form(""),
    service_id: str = Form(""),
    next: str = Form(""),
):
    """Create or update a manual dependency edge (P3). Cross-host allowed."""
    from ..services import runtime_edges as re_svc

    if not (from_project or "").strip() or not (to_project or "").strip():
        return _redirect(
            _stack_next(next, service_id=service_id), error="edge_need_projects"
        )
    try:
        row = re_svc.create_manual_edge(
            session,
            from_server_id=from_server_id,
            from_project=from_project,
            from_container=from_container or None,
            to_server_id=to_server_id,
            to_project=to_project,
            to_container=to_container or None,
            kind=kind or "talks_to",
            note=note or None,
            user_id=user.id,
        )
        session.add(
            make_audit_log(
                action="fabric.edge_manual",
                status="success",
                user_id=user.id,
                server_id=from_server_id,
                details=(
                    f"{from_project}/{from_container or '*'} → "
                    f"{to_project}/{to_container or '*'} "
                    f"kind={kind} edge_id={row.id}"
                ),
            )
        )
        session.commit()
        return _redirect(_stack_next(next, service_id=service_id), msg="edge_saved")
    except Exception as e:
        return _redirect(
            _stack_next(next, service_id=service_id),
            error=f"edge_manual_failed: {str(e)[:120]}",
        )


@router.post("/dns/stack-edges/{edge_id}/delete")
async def stack_edge_delete(
    edge_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    service_id: str = Form(""),
    next: str = Form(""),
):
    """Hard-delete a confirmed/manual edge (no cascade to Kuma)."""
    from ..services import runtime_edges as re_svc

    ok = re_svc.delete_edge(session, edge_id)
    if ok:
        session.add(
            make_audit_log(
                action="fabric.edge_delete",
                status="success",
                user_id=user.id,
                details=f"edge_id={edge_id}",
            )
        )
        session.commit()
        return _redirect(_stack_next(next, service_id=service_id), msg="edge_deleted")
    return _redirect(
        _stack_next(next, service_id=service_id), error="edge_not_found"
    )


def _safe_next_path(next_path: str, default: str = "/dns/coverage#kuma-deps") -> str:
    """Allow only same-origin relative paths (no open redirects)."""
    nxt = (next_path or "").strip()
    if not nxt or not nxt.startswith("/") or nxt.startswith("//"):
        return default
    if "://" in nxt or "\n" in nxt or "\r" in nxt:
        return default
    return nxt[:500]


@router.post("/dns/coverage/mute")
async def coverage_mute_dependency(
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    key: str = Form(...),
    unmute: Optional[str] = Form(None),
    next: str = Form(""),
):
    """Mute/unmute a docker dependency from coverage suggestions (server:project:container)."""
    del session  # settings are session-free
    del user
    key = (key or "").strip()
    dest = _safe_next_path(next, default="/dns/coverage#kuma-deps")
    if not key or key.count(":") < 2 or len(key) > 200:
        return _redirect(dest, error="invalid mute key")
    cfg = load_settings()
    raw = cfg.get("kuma_coverage_mute_keys") or "[]"
    try:
        keys = json.loads(raw) if isinstance(raw, str) else list(raw or [])
        if not isinstance(keys, list):
            keys = []
    except Exception:
        keys = []
    keys = [str(k) for k in keys if str(k).strip()]
    if unmute in ("1", "on", "true", "yes"):
        keys = [k for k in keys if k != key]
        msg = "dep_unmuted"
    else:
        if key not in keys:
            keys.append(key)
        msg = "dep_muted"
    save_settings({"kuma_coverage_mute_keys": json.dumps(keys)})
    return _redirect(dest, msg=msg)


@router.post("/dns/coverage/show-infra")
async def coverage_toggle_show_infra(
    user: User = Depends(get_operator_user),
    show: Optional[str] = Form(None),
):
    """Toggle whether infra roles (postgres/redis/…) appear as dependency gaps."""
    del user
    on = show in ("1", "on", "true", "yes")
    save_settings({"kuma_coverage_show_infra": on})
    return _redirect(
        "/dns/coverage#kuma-deps", msg="infra_shown" if on else "infra_hidden"
    )


@router.get("/dns/candidates", response_class=HTMLResponse)
async def dns_candidates_partial(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """HTMX partial: Pi-hole / host-identity adopt rows (live Pi-hole call off hub paint)."""
    if not _can_mutate(user):
        return HTMLResponse(
            '<p class="text-xs text-muted">Sign in as operator to adopt DNS records.</p>'
        )
    settings = load_settings()
    base = (settings.get("dns_base_domain") or "").strip()
    error = ""
    candidates: list = []
    try:
        candidates = fabric.list_service_dns_candidates(session, base_domain=base)
    except Exception as e:
        candidates = []
        error = str(e)[:200] or "Could not load Pi-hole candidates"
    return templates.TemplateResponse(
        request=request,
        name="partials/dns_candidates.html",
        context={
            "request": request,
            "user": user,
            "can_mutate": True,
            "candidates": candidates,
            "candidates_error": error,
        },
    )


@router.get("/dns/physical", response_class=HTMLResponse)
async def dns_physical(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Full physical mesh — all hosts and landing apps."""
    ctx = _dns_page_context(request, session, user, page="physical")
    return templates.TemplateResponse(
        request=request,
        name="dns_physical.html",
        context=ctx,
    )


@router.get("/dns/logical", response_class=HTMLResponse)
async def dns_logical(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Full logical mesh — URL → NPM/direct → destination."""
    ctx = _dns_page_context(request, session, user, page="logical")
    return templates.TemplateResponse(
        request=request,
        name="dns_logical.html",
        context=ctx,
    )


@router.post("/dns/base-domain")
async def save_base_domain(
    domain: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    from ..services.app_settings import save_settings

    d = fabric.normalize_fqdn(domain)
    if d and not (
        fabric.is_valid_fqdn(d)
        or (d and all(c.isalnum() or c in ".-" for c in d) and "." in d)
    ):
        return _redirect(error="Invalid base domain")
    save_settings({"dns_base_domain": d})
    return _redirect(msg="Base domain saved")


@router.post("/dns/network")
async def save_network_map(
    lan_subnet: str = Form(""),
    gateway_ip: str = Form(""),
    public_ip: str = Form(""),
    gateway_kuma_external_id: str = Form(""),
    public_kuma_external_id: str = Form(""),
    kuma_integration_id: str = Form(""),
    stack_inventory_down_alerts: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    """Save LAN / gateway / public IP / Kuma monitors for the hosts map."""
    import ipaddress
    from datetime import datetime

    from ..services.app_settings import save_settings

    del session
    del user
    subnet = (lan_subnet or "").strip()
    gw = (gateway_ip or "").strip()
    pub = (public_ip or "").strip()
    gw_kuma = (gateway_kuma_external_id or "").strip()
    pub_kuma = (public_kuma_external_id or "").strip()
    kuma_iid = (kuma_integration_id or "").strip()
    if subnet:
        try:
            ipaddress.ip_network(subnet, strict=False)
        except Exception:
            return _redirect(error="Invalid LAN subnet (use CIDR, e.g. 192.168.86.0/24)")
    if gw:
        try:
            ipaddress.ip_address(gw)
        except Exception:
            return _redirect(error="Invalid gateway IP")
    if pub:
        try:
            ipaddress.ip_address(pub)
        except Exception:
            return _redirect(error="Invalid public IP")
    if kuma_iid and not kuma_iid.isdigit():
        return _redirect(error="Invalid Kuma integration id")
    payload = {
        "network_lan_subnet": subnet,
        "network_gateway_ip": gw,
        "network_public_ip": pub,
        "network_gateway_kuma_external_id": gw_kuma,
        "network_public_kuma_external_id": pub_kuma,
        "network_kuma_integration_id": kuma_iid,
        # checkbox: only present when checked
        "stack_inventory_down_alerts": stack_inventory_down_alerts
        in ("1", "on", "true", "yes"),
    }
    # Keep prior lookup timestamp if public IP unchanged
    prev = load_settings()
    if pub and pub != (prev.get("network_public_ip") or "").strip():
        payload["network_public_ip_checked_at"] = datetime.utcnow().isoformat() + "Z"
    save_settings(payload)
    return _redirect(msg="Network map settings saved")


@router.post("/dns/network/lookup-public-ip")
async def lookup_public_ip(
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    """Look up this PiHerder host's public WAN IP (outbound)."""
    import ipaddress
    from datetime import datetime

    import httpx

    from ..services.app_settings import save_settings

    ip = ""
    last_err = ""
    for url in (
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
    ):
        try:
            with httpx.Client(timeout=6.0, follow_redirects=True) as client:
                r = client.get(url)
                if r.status_code == 200:
                    cand = (r.text or "").strip().split()[0]
                    ipaddress.ip_address(cand)
                    ip = cand
                    break
        except Exception as e:
            last_err = str(e)[:120]
            continue
    if not ip:
        return _redirect(
            error=f"Could not look up public IP{(': ' + last_err) if last_err else ''}"
        )
    save_settings(
        {
            "network_public_ip": ip,
            "network_public_ip_checked_at": datetime.utcnow().isoformat() + "Z",
        }
    )
    return _redirect(msg=f"Public IP looked up: {ip}")


@router.post("/dns/services")
async def create_service_dns(
    fqdn: str = Form(...),
    target_server_id: int = Form(...),
    backend_server_id: int = Form(...),
    label: str = Form(""),
    docker_project: str = Form(""),
    npm_hint: str = Form(""),
    via_proxy: Optional[str] = Form(None),
    managed_on_pihole: Optional[str] = Form(None),
    sync_now: Optional[str] = Form(None),
    external_dns_status: str = Form("checklist"),
    certificate_id: Optional[str] = Form(None),
    stack_deployment_id: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    cert_id = None
    if certificate_id and str(certificate_id).strip().isdigit():
        cert_id = int(certificate_id)
    dep_id = None
    if stack_deployment_id and str(stack_deployment_id).strip().isdigit():
        dep_id = int(stack_deployment_id)
    try:
        row, results = fabric.upsert_service_record(
            session,
            fqdn=fqdn,
            target_server_id=target_server_id,
            backend_server_id=backend_server_id,
            label=label or None,
            docker_project=docker_project or None,
            npm_hint=npm_hint or None,
            via_proxy=via_proxy in ("on", "1", "true") if via_proxy is not None else None,
            managed_on_pihole=managed_on_pihole in ("on", "1", "true"),
            sync_now=sync_now in ("on", "1", "true"),
            external_dns_status=external_dns_status or "checklist",
            certificate_id=cert_id,
            stack_deployment_id=dep_id,
            user_id=user.id,
        )
        ok = sum(1 for r in results if r.get("ok")) if results else 0
        n = len(results) if results else 0
        msg = f"Service DNS {row.fqdn} saved"
        if results:
            msg += f" · Pi-hole {ok}/{n}"
        return _redirect(msg=msg)
    except fabric.DnsFabricError as e:
        return _redirect(error=e.message)


@router.post("/dns/services/{record_id}/sync")
async def sync_service(
    record_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    row = fabric.get_service_record(session, record_id)
    if not row:
        raise HTTPException(404)
    try:
        results = fabric.sync_service_cname(session, row, user_id=user.id)
        ok = sum(1 for r in results if r.get("ok"))
        return _redirect(msg=f"CNAME sync {ok}/{len(results)} on Pi-hole")
    except fabric.DnsFabricError as e:
        return _redirect(error=e.message)


@router.post("/dns/services/{record_id}/delete")
async def delete_service(
    record_id: int,
    remove_from_pihole: Optional[str] = Form("1"),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    row = fabric.get_service_record(session, record_id)
    if not row:
        raise HTTPException(404)
    fqdn = row.fqdn
    fabric.delete_service_record(
        session,
        row,
        user_id=user.id,
        remove_from_pihole=remove_from_pihole not in ("0", "false", "off"),
    )
    return _redirect(msg=f"Removed {fqdn}")


@router.post("/dns/services/{record_id}/external")
async def mark_external(
    record_id: int,
    status: str = Form("done"),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    row = fabric.get_service_record(session, record_id)
    if not row:
        raise HTTPException(404)
    row.external_dns_status = (status or "done").strip()[:32]
    session.add(row)
    session.commit()
    return _redirect(msg=f"External DNS marked {row.external_dns_status} for {row.fqdn}")


@router.post("/servers/{server_id}/dns")
async def update_server_dns(
    server_id: int,
    dns_name: str = Form(""),
    dns_manage_a: Optional[str] = Form(None),
    dns_ip_override: str = Form(""),
    ip_address: str = Form(""),
    sync_now: Optional[str] = Form(None),
    return_to: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    dest = (return_to or "").strip() or f"/servers/{server_id}"
    sep = "&" if "?" in dest else "?"

    # Persist IP for A records (host form field)
    new_ip = (ip_address or "").strip() or None
    if new_ip is not None or ip_address == "":
        # Only update when field is present in form (always is on DNS card)
        server.ip_address = new_ip
        session.add(server)
        session.commit()
        session.refresh(server)

    try:
        result = fabric.update_server_dns(
            session,
            server,
            dns_name=dns_name or None,
            dns_manage_a=dns_manage_a in ("on", "1", "true"),
            dns_ip_override=dns_ip_override or None,
            user_id=user.id,
            # Manage A always syncs; sync_now is force
            sync_now=sync_now in ("on", "1", "true"),
        )
    except fabric.DnsFabricError as e:
        return RedirectResponse(
            f"{dest}{sep}error={quote(e.message[:200])}",
            status_code=303,
        )

    action = (result or {}).get("action") or "saved"
    sync = (result or {}).get("sync") or []
    if action == "synced" and sync:
        ok = sum(1 for r in sync if r.get("ok"))
        return RedirectResponse(
            f"{dest}{sep}msg=dns_synced&detail={quote(f'{ok}/{len(sync)}')}",
            status_code=303,
        )
    if action == "removed":
        ok = sum(1 for r in sync if r.get("ok")) if sync else 0
        n = len(sync) if sync else 0
        return RedirectResponse(
            f"{dest}{sep}msg=dns_removed&detail={quote(f'{ok}/{n}')}",
            status_code=303,
        )
    return RedirectResponse(f"{dest}{sep}msg=dns_saved", status_code=303)


@router.post("/servers/{server_id}/dns/sync-a")
async def sync_server_a(
    server_id: int,
    return_to: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)
    dest = (return_to or "").strip() or f"/servers/{server_id}"
    try:
        if not server.dns_manage_a:
            server.dns_manage_a = True
            session.add(server)
            session.commit()
        results = fabric.sync_host_a(session, server, user_id=user.id)
        ok = sum(1 for r in results if r.get("ok"))
        sep = "&" if "?" in dest else "?"
        return RedirectResponse(
            f"{dest}{sep}msg={quote(f'A record sync {ok}/{len(results)}')}",
            status_code=303,
        )
    except fabric.DnsFabricError as e:
        sep = "&" if "?" in dest else "?"
        return RedirectResponse(
            f"{dest}{sep}error={quote(e.message[:200])}",
            status_code=303,
        )


@router.post("/templates/deployments/{deployment_id}/dns")
async def deployment_dns(
    deployment_id: int,
    fqdn: str = Form(""),
    use_inferred: Optional[str] = Form(None),
    target_server_id: Optional[int] = Form(None),
    via_proxy: Optional[str] = Form(None),
    npm_hint: str = Form(""),
    managed_on_pihole: Optional[str] = Form("on"),
    sync_now: Optional[str] = Form("on"),
    external_dns_status: str = Form("checklist"),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    from ..services.app_settings import load_settings
    from ..services.service_templates import get_deployment

    dep = get_deployment(session, deployment_id)
    if not dep:
        raise HTTPException(404)
    try:
        base = (load_settings().get("dns_base_domain") or "").strip()
        plan = fabric.resolve_service_dns_plan(
            session,
            backend_server_id=dep.server_id,
            docker_project=dep.project_name,
            stack_deployment_id=dep.id,
            fqdn=fqdn or None,
            base_domain=base,
        )
        # Prefer inferred target/backend; allow rare manual override
        if target_server_id and use_inferred not in ("1", "on", "true"):
            plan["target_server_id"] = int(target_server_id)
            plan["via_proxy"] = via_proxy in ("on", "1", "true")
            if npm_hint:
                plan["npm_hint"] = npm_hint
        row, results = fabric.attach_service_dns_from_plan(
            session,
            plan,
            fqdn_override=fqdn or plan.get("fqdn"),
            user_id=user.id,
            sync_now=sync_now not in ("0", "false", "off"),
        )
        ok = sum(1 for r in results if r.get("ok")) if results else 0
        n = len(results) if results else 0
        msg = f"dns_synced_{ok}_{n}" if results else "dns_saved"
        return RedirectResponse(
            f"/templates/deployments/{deployment_id}?msg={msg}",
            status_code=303,
        )
    except fabric.DnsFabricError as e:
        return RedirectResponse(
            f"/templates/deployments/{deployment_id}?error={quote(e.message[:200])}",
            status_code=303,
        )


@router.post("/dns/attach-deployment")
async def attach_from_deployment(
    deployment_id: int = Form(...),
    fqdn: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """One-click attach from Catalog → DNS candidate list."""
    from ..services.app_settings import load_settings
    from ..services.service_templates import get_deployment

    dep = get_deployment(session, deployment_id)
    if not dep:
        return _redirect(error="Deployment not found")
    try:
        base = (load_settings().get("dns_base_domain") or "").strip()
        plan = fabric.resolve_service_dns_plan(
            session,
            backend_server_id=dep.server_id,
            docker_project=dep.project_name,
            stack_deployment_id=dep.id,
            fqdn=fqdn or None,
            base_domain=base,
        )
        row, results = fabric.attach_service_dns_from_plan(
            session, plan, fqdn_override=fqdn or None, user_id=user.id
        )
        ok = sum(1 for r in results if r.get("ok")) if results else 0
        n = len(results) if results else 0
        present = sum(1 for r in results if r.get("already_present")) if results else 0
        extra = f" ({present} already on Pi-hole)" if present else ""
        return _redirect(msg=f"Attached {row.fqdn} · Pi-hole {ok}/{n}{extra}")
    except fabric.DnsFabricError as e:
        return _redirect(error=e.message)


@router.post("/dns/attach-cname")
async def attach_from_pihole_cname(
    fqdn: str = Form(...),
    cname_target: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Adopt one existing Pi-hole CNAME into PiHerder (does not recreate)."""
    from ..services.app_settings import load_settings

    try:
        base = (load_settings().get("dns_base_domain") or "").strip()
        if cname_target:
            plan = fabric.plan_from_pihole_cname(
                session, fqdn, cname_target, base_domain=base
            )
        else:
            existing = fabric._match_pihole_cname(session, fqdn)
            if not existing:
                return _redirect(error=f"No Pi-hole CNAME for {fqdn}")
            plan = fabric.plan_from_pihole_cname(
                session, existing["domain"], existing["target"], base_domain=base
            )
        row, results = fabric.attach_service_dns_from_plan(
            session, plan, fqdn_override=fqdn, user_id=user.id, sync_now=True
        )
        present = sum(1 for r in (results or []) if r.get("already_present"))
        return _redirect(
            msg=f"Adopted {row.fqdn}"
            + (f" (already on Pi-hole ×{present})" if present else "")
        )
    except fabric.DnsFabricError as e:
        return _redirect(error=e.message)


@router.post("/dns/import-pihole")
async def import_all_pihole_cnames(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Bulk-adopt all Pi-hole CNAMEs that map to fleet hosts/NPM."""
    from ..services.app_settings import load_settings

    base = (load_settings().get("dns_base_domain") or "").strip()
    result = fabric.import_pihole_cnames(session, user_id=user.id, base_domain=base)
    msg = (
        f"Imported {result['imported_count']}"
        f" · skipped {result['skipped_count']}"
        f" · errors {result['error_count']}"
    )
    if result.get("errors"):
        return _redirect(msg=msg, error="; ".join(result["errors"][:3]))
    return _redirect(msg=msg)


@router.post("/dns/attach-host-identity")
async def attach_host_identity(
    server_id: int = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Map a host whose A name is also the app name (no CNAME).

    Example: 3dprint.example.com is the host A record and the service name.
    """
    server = session.get(Server, server_id)
    if not server:
        return _redirect(error="Server not found")
    fqdn = fabric.normalize_fqdn(server.dns_name)
    if not fqdn:
        return _redirect(error="Server has no host DNS name")
    try:
        plan = {
            "fqdn": fqdn,
            "target_server_id": server.id,
            "backend_server_id": server.id,
            "backend_name": server.name,
            "host_identity": True,
            "record_type": "a",
            "via_proxy": False,
            "label": server.name,
        }
        row, results = fabric.attach_service_dns_from_plan(
            session, plan, fqdn_override=fqdn, user_id=user.id, sync_now=True
        )
        present = sum(1 for r in (results or []) if r.get("already_present"))
        return _redirect(
            msg=f"Host identity {row.fqdn} → {server.name}"
            + (f" (A already on Pi-hole ×{present})" if present else "")
        )
    except fabric.DnsFabricError as e:
        return _redirect(error=e.message)

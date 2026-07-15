"""End-to-end DNS fabric UI and mutations."""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from ..database import get_session
from ..models import Server, User
from ..security.auth import get_admin_user, get_current_user, user_role
from ..services import dns_fabric as fabric
from ..services.app_settings import load_settings
from ..templates import templates

router = APIRouter()


def _can_mutate(user: User) -> bool:
    return user_role(user) in ("admin", "operator")


def _redirect(path: str = "/dns", *, msg: str = "", error: str = "") -> RedirectResponse:
    q = []
    if msg:
        q.append(f"msg={quote(msg)}")
    if error:
        q.append(f"error={quote(error[:300])}")
    url = path + (("?" + "&".join(q)) if q else "")
    return RedirectResponse(url, status_code=303)


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
    kuma_opts = fabric.list_kuma_monitor_options(session) if page == "index" else []
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
        ],
        "caption": "Network maps · hosts & paths",
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
        "msg": request.query_params.get("msg") or "",
        "error": request.query_params.get("error") or "",
        "catalog_section": "dns",
        "dns_page": page,
        "focus_path_id": focus,
    }


@router.get("/dns", response_class=HTMLResponse)
async def dns_list(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """DNS hub: polished per-service paths + adopt/manage. Full mesh on subpages."""
    ctx = _dns_page_context(request, session, user, page="index")
    return templates.TemplateResponse(
        request=request,
        name="dns_list.html",
        context=ctx,
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
    session: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    """Save LAN / gateway / public IP / Kuma monitors for the hosts map."""
    import ipaddress
    from datetime import datetime

    from ..services.app_settings import save_settings

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

    Example: 3dprint.hacknow.info is the host A record and the service name.
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

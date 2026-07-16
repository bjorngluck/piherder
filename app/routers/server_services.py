"""Per-server Services page — Uptime Kuma HTTP/TLS monitors (host + Docker)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from .. import templates as templates_mod
from ..database import get_session
from ..models import Server, User
from ..security.auth import get_current_user
from ..services.integrations import registry as integ_reg

logger = logging.getLogger(__name__)
router = APIRouter(tags=["server-services"])


@router.get("/{server_id}/services", response_class=HTMLResponse)
async def server_services_page(
    server_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """List bound services for this host: URL, monitoring status, Kuma deep link."""
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404, "Server not found")

    services = integ_reg.all_service_chips_for_server(session, server_id)
    host_n = sum(1 for s in services if s.get("location_kind") == "host")
    docker_n = sum(1 for s in services if s.get("location_kind") == "docker")

    # Optional path-map deep links when a DNS fabric record matches the monitor URL
    try:
        from ..services import dns_fabric as fabric

        for s in services:
            hit = fabric.fabric_path_for_fqdn(session, s.get("url") or s.get("label"))
            if hit:
                s["path_map_url"] = hit.get("path_map_url")
                s["hosts_map_url"] = hit.get("hosts_map_url")
                s["fabric_fqdn"] = hit.get("fqdn")
    except Exception:
        pass
    hosts_map_url = f"/dns/physical?focus=n:host-{server_id}#map"
    try:
        from ..services import dns_fabric as fabric

        hosts_map_url = fabric.hosts_map_url(server_id=server_id)
    except Exception:
        pass

    # Prefer first Kuma integration for "manage" link
    manage_id = None
    for s in services:
        if s.get("integration_id"):
            manage_id = s["integration_id"]
            break
    if manage_id is None:
        try:
            kumas = integ_reg.list_integrations(
                session, type_filter=integ_reg.TYPE_UPTIME_KUMA
            )
            if kumas:
                manage_id = kumas[0].id
        except Exception:
            pass

    ssh = None
    try:
        binds = integ_reg.list_bindings(
            session, server_id=server_id, role=integ_reg.ROLE_SSH
        )
        if binds:
            b = binds[0]
            integ = integ_reg.get_integration(session, b.integration_id)
            ssh = {
                "state": b.last_state or "unknown",
                "label": b.external_label or b.external_id,
                "message": b.last_message or "",
                "open_url": integ_reg.binding_open_url(integ, b) if integ else "",
                "integration_id": b.integration_id,
            }
    except Exception:
        pass

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="server_services.html",
        context={
            "title": f"Services — {server.name}",
            "server": server.model_dump(
                exclude={"audit_logs", "jobs", "docker_versions"}
            ),
            "user": user,
            "services": services,
            "host_count": host_n,
            "docker_count": docker_n,
            "manage_integration_id": manage_id,
            "kuma_ssh": ssh,
            "hosts_map_url": hosts_map_url,
        },
    )

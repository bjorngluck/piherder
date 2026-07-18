"""P5 — optional alerts when a *monitored* container is down in Docker inventory.

Only fires for containers that have a Kuma (or service) IntegrationBinding.
Muted infra without a bind never alerts (coverage mute / default infra).
Uses notification fingerprints; resolves when the container is running again.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlmodel import Session, select

from ..models import IntegrationBinding, Server
from . import docker_inventory as inv_svc
from . import notifications as notif_svc
from .app_settings import load_settings
from .integrations import registry as reg

logger = logging.getLogger(__name__)


def inventory_down_alerts_enabled() -> bool:
    """App setting stack_inventory_down_alerts — default on."""
    try:
        v = load_settings().get("stack_inventory_down_alerts")
        if v is None or v == "":
            return True
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return True


def _bindings_by_server(session: Session, server_id: int) -> list[IntegrationBinding]:
    return list(
        session.exec(
            select(IntegrationBinding).where(
                IntegrationBinding.server_id == int(server_id),
                IntegrationBinding.role == reg.ROLE_SERVICE,
            )
        ).all()
    )


def _container_bound(
    binds: list[IntegrationBinding],
    *,
    project: str,
    container: str,
    compose_service: str,
) -> IntegrationBinding | None:
    from .dns_fabric import kuma_coverage as cov

    return cov._container_bound(
        binds, project=project, container=container, compose_service=compose_service
    )


def fingerprint(server_id: int, project: str, container: str) -> str:
    return f"stack_container_down:{int(server_id)}:{(project or '').lower()}:{(container or '').lower()}"


def scan_server_inventory_for_down_alerts(
    session: Session, server: Server
) -> dict[str, Any]:
    """After inventory refresh: alert bound containers that are stopped; resolve if up."""
    if not inventory_down_alerts_enabled():
        return {"enabled": False, "alerted": 0, "resolved": 0}
    if server.id is None:
        return {"enabled": True, "alerted": 0, "resolved": 0}

    inv = inv_svc.parse_inventory(server) or {}
    projects = inv.get("projects") or []
    if not isinstance(projects, list):
        return {"enabled": True, "alerted": 0, "resolved": 0}

    binds = _bindings_by_server(session, int(server.id))
    if not binds:
        return {"enabled": True, "alerted": 0, "resolved": 0, "no_binds": True}

    alerted = 0
    resolved = 0
    seen_fps: set[str] = set()

    for p in projects:
        if not isinstance(p, dict):
            continue
        pname = (p.get("name") or "").strip()
        if not pname:
            continue
        for c in p.get("containers") or []:
            if not isinstance(c, dict) or c.get("placeholder"):
                continue
            cname = (c.get("name") or c.get("compose_service") or "").strip()
            if not cname:
                continue
            csvc = (c.get("compose_service") or "").strip()
            bound = _container_bound(
                binds, project=pname, container=cname, compose_service=csvc
            )
            if not bound:
                continue  # not monitored — no fleet spam
            fp = fingerprint(int(server.id), pname, cname)
            seen_fps.add(fp)
            running = bool(c.get("running"))
            if running:
                n = notif_svc.resolve_by_fingerprint(session, fp)
                resolved += n
                continue
            # Down + bound → open/refresh notification
            label = (bound.external_label or bound.external_id or cname).strip()
            title = f"Monitored container down: {cname}"
            body = (
                f"{server.name} / {pname} / {cname} is not running in Docker inventory. "
                f"Kuma bind: {label}"
                + (f" (last Kuma: {bound.last_state})" if bound.last_state else "")
            )
            link = f"/servers/{server.id}/docker"
            notif_svc.upsert_notification(
                session,
                fingerprint=fp,
                type="stack_container_down",
                title=title,
                body=body[:500],
                link_url=link,
                severity="critical",
                server_id=int(server.id),
                payload={
                    "server_id": server.id,
                    "project": pname,
                    "container": cname,
                    "binding_id": bound.id,
                    "kuma_state": bound.last_state,
                },
            )
            alerted += 1

    logger.info(
        "stack inventory down scan server=%s alerted=%s resolved=%s",
        server.id,
        alerted,
        resolved,
    )
    return {
        "enabled": True,
        "alerted": alerted,
        "resolved": resolved,
        "bound_checked": len(seen_fps),
    }


def scan_after_inventory_refresh(server_id: int) -> None:
    """Best-effort hook from inventory refresh (never raises to caller)."""
    try:
        if not inventory_down_alerts_enabled():
            return
        with Session(engine) as session:
            server = session.get(Server, int(server_id))
            if not server:
                return
            scan_server_inventory_for_down_alerts(session, server)
    except Exception:
        logger.exception("stack down alert scan failed server=%s", server_id)


# late import for engine
from ..database import engine  # noqa: E402

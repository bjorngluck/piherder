"""Hosts-map projection of nmap discovery devices (soft fabric dependency)."""
from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from ...models import NmapDevice
from .device_ops import device_display_name

def gateway_map_info(session: Session) -> dict[str, Any]:
    """Discovery device marked as map gateway (for Hosts map spine label)."""
    from .device_classify import MAP_ROLE_GATEWAY

    row = session.exec(
        select(NmapDevice)
        .where(
            NmapDevice.map_role == MAP_ROLE_GATEWAY,
            NmapDevice.state != "ignored",
        )
        .order_by(NmapDevice.id)
        .limit(1)
    ).first()
    if not row:
        return {}
    label = device_display_name(row, prefer_ip_fallback=False)
    return {
        "device_id": row.id,
        "integration_id": row.integration_id,
        "ip": (row.ip_address or "").strip(),
        "label": (label or "").strip() or "Router",
        "display_name": (getattr(row, "display_name", None) or "").strip() or "",
        # Network tab modal + return to Hosts after save/close
        "href": (
            f"/integrations/{row.integration_id}"
            f"?tab=network&device={row.id}&return=hosts"
        ),
    }


def discovery_hosts_for_fabric(
    session: Session,
    *,
    fleet_ips: set[str] | None = None,
    fleet_server_ids: set[int] | None = None,
    gateway_ip: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Unlinked nmap devices shaped as Hosts-map host nodes (no manual link required).

    Skips ignored devices, devices already linked to a fleet server, IPs that
    already appear as managed fleet hosts, map-role gateway, and the configured
    network gateway IP (spine Router node). Used for the end-to-end LAN view.
    """
    from .device_classify import profile_dict_from_device

    fleet_ips = {str(ip).strip() for ip in (fleet_ips or set()) if ip}
    fleet_server_ids = {int(s) for s in (fleet_server_ids or set()) if s is not None}
    gw = (gateway_ip or "").strip()

    rows = list(
        session.exec(
            select(NmapDevice)
            .where(NmapDevice.state != "ignored")
            .order_by(NmapDevice.ip_address)
            .limit(max(1, min(2000, limit)))
        ).all()
    )
    out: list[dict[str, Any]] = []
    seen_ip: set[str] = set()
    for d in rows:
        ip = (d.ip_address or "").strip()
        if not ip:
            continue
        if ip in fleet_ips or ip in seen_ip:
            continue
        if gw and ip == gw:
            # Represented by Hosts map Router spine, not a LAN discovery chip
            continue
        if d.linked_server_id is not None and int(d.linked_server_id) in fleet_server_ids:
            # Already represented by the managed Server card on the map
            continue
        if d.linked_server_id is not None:
            # Linked to a server not in current fleet list — still skip to avoid doubles
            continue
        # Gateway role lives on the spine (Router node), not as a LAN chip
        role = (getattr(d, "map_role", None) or "").strip().lower()
        if role == "gateway":
            continue
        seen_ip.add(ip)
        profile = profile_dict_from_device(d)
        label = device_display_name(d, prefer_ip_fallback=False)
        if not label:
            # Prefer short kind over raw IP only when no operator/nmap name
            if profile.get("kind") and profile.get("kind") != "unknown":
                label = str(profile.get("label") or "")[:64]
            else:
                label = ip
        out.append(
            {
                "server_id": None,
                "discovery_id": d.id,
                "integration_id": d.integration_id,
                "is_discovered": True,
                "name": label[:64],
                "display_name": (getattr(d, "display_name", None) or "").strip() or "",
                "dns_name": (d.hostname or "").strip() or None,
                "ip": ip,
                "mac": d.mac_address or "",
                "mac_vendor": getattr(d, "mac_vendor", None) or "",
                "state": d.state,
                "map_role": role or "",
                "device_kind": profile.get("kind") or "unknown",
                "device_kind_label": profile.get("label") or "",
                "device_kind_short": profile.get("short") or "?",
                "device_kind_overridden": bool(profile.get("overridden")),
                "href": (
                    f"/integrations/{d.integration_id}"
                    f"?tab=network&device={d.id}&return=hosts"
                ),
                "open_label": "Open discovery",
            }
        )
    return out

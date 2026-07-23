"""Device identity, lifecycle, and soft-embed helpers for LAN Discovery."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlmodel import Session, select

from ...models import NmapDevice, NmapScriptResult

# Default days without last_seen before new/known → stale (list/refresh path)
STALE_AFTER_DAYS = 14


def device_display_name(device: Any, *, prefer_ip_fallback: bool = True) -> str:
    """Label for lists/maps: operator display_name → nmap hostname → IP."""
    for key in ("display_name", "hostname"):
        val = (getattr(device, key, None) or "").strip()
        if val:
            return val[:128]
    ip = (getattr(device, "ip_address", None) or "").strip()
    if prefer_ip_fallback and ip:
        return ip
    return ""


def set_device_display_name(
    session: Session, device: NmapDevice, name: str | None
) -> NmapDevice:
    """Set or clear operator-friendly name (does not change nmap hostname)."""
    raw = (name or "").strip()
    # Collapse internal whitespace; keep letters, digits, common host tokens
    if raw:
        raw = re.sub(r"\s+", " ", raw)[:128]
    device.display_name = raw or None
    device.updated_at = datetime.utcnow()
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


def set_device_kind_override(
    session: Session, device: NmapDevice, kind: str | None
) -> NmapDevice:
    """Set or clear operator device type (overrides busted heuristics)."""
    from .device_classify import normalize_kind_override

    device.kind_override = normalize_kind_override(kind)
    device.updated_at = datetime.utcnow()
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


def set_device_map_role(
    session: Session,
    device: NmapDevice,
    role: str | None,
    *,
    sync_network_gateway: bool = True,
) -> NmapDevice:
    """Set map role (e.g. gateway). At most one device is gateway.

    When role is gateway and *sync_network_gateway*, also writes
    ``network_gateway_ip`` so the Hosts map spine uses this IP.
    """
    from .device_classify import MAP_ROLE_GATEWAY, normalize_map_role

    new_role = normalize_map_role(role)
    if new_role == MAP_ROLE_GATEWAY:
        # Clear other gateways for a single spine router
        others = list(
            session.exec(
                select(NmapDevice).where(
                    NmapDevice.map_role == MAP_ROLE_GATEWAY,
                    NmapDevice.id != device.id,  # type: ignore[arg-type]
                )
            ).all()
        )
        for o in others:
            o.map_role = None
            o.updated_at = datetime.utcnow()
            session.add(o)
        device.map_role = MAP_ROLE_GATEWAY
        if sync_network_gateway:
            ip = (device.ip_address or "").strip()
            if ip:
                try:
                    from ..app_settings import load_settings, save_settings

                    cfg = load_settings()
                    if (cfg.get("network_gateway_ip") or "").strip() != ip:
                        cfg["network_gateway_ip"] = ip
                        save_settings(cfg)
                except Exception:
                    pass
    else:
        device.map_role = None
    device.updated_at = datetime.utcnow()
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


def set_device_map_identity(
    session: Session,
    device: NmapDevice,
    *,
    display_name: str | None = None,
    kind_override: str | None = None,
    map_role: str | None = None,
    sync_network_gateway: bool = True,
    mark_known: bool = True,
) -> NmapDevice:
    """Single form: map name + kind override + map role (gateway).

    Saving map identity is treated as review: *new* / *stale* → *known*
    when *mark_known* (default), so the inbox filter shrinks as you label devices.

    Gateway role → writes ``network_gateway_ip`` when *sync_network_gateway*.
    Clearing gateway role does **not** clear that setting (sticky spine IP).
    """
    from .device_classify import MAP_ROLE_GATEWAY, normalize_kind_override, normalize_map_role

    raw = (display_name if display_name is not None else device.display_name) or ""
    raw = (raw or "").strip()
    if raw:
        raw = re.sub(r"\s+", " ", raw)[:128]
    device.display_name = raw or None
    device.kind_override = normalize_kind_override(kind_override)

    new_role = normalize_map_role(map_role)
    if new_role == MAP_ROLE_GATEWAY:
        others = list(
            session.exec(
                select(NmapDevice).where(
                    NmapDevice.map_role == MAP_ROLE_GATEWAY,
                    NmapDevice.id != device.id,  # type: ignore[arg-type]
                )
            ).all()
        )
        for o in others:
            o.map_role = None
            o.updated_at = datetime.utcnow()
            session.add(o)
        device.map_role = MAP_ROLE_GATEWAY
        if sync_network_gateway:
            ip = (device.ip_address or "").strip()
            if ip:
                try:
                    from ..app_settings import load_settings, save_settings

                    cfg = load_settings()
                    if (cfg.get("network_gateway_ip") or "").strip() != ip:
                        cfg["network_gateway_ip"] = ip
                        save_settings(cfg)
                except Exception:
                    pass
    else:
        device.map_role = None

    # Operator touched identity → reviewed (unless linked/ignored)
    if mark_known and device.state in ("new", "stale"):
        device.state = "known"

    device.updated_at = datetime.utcnow()
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


# Human labels for device lifecycle states (UI / docs)
DEVICE_STATE_LABELS: dict[str, str] = {
    "new": "New",
    "known": "Known (reviewed)",
    "linked": "Linked",
    "ignored": "Ignored",
    # UI: "Offline" — state id remains *stale* (not seen since threshold; never auto-deleted)
    "stale": "Offline",
}
DEVICE_STATES_OPERATOR = ("new", "known", "ignored")  # settable without link


def apply_stale_device_states(
    session: Session,
    *,
    days: int = STALE_AFTER_DAYS,
    integration_id: int | None = None,
) -> int:
    """Mark new/known devices stale when *last_seen_at* is older than *days*.

    Linked and ignored are never auto-staled. Called from list/refresh paths so
    the Stale filter is meaningful without a separate cron. Returns count updated.
    """
    days = max(1, min(365, int(days or STALE_AFTER_DAYS)))
    cutoff = datetime.utcnow() - timedelta(days=days)
    q = select(NmapDevice).where(
        NmapDevice.state.in_(["new", "known"]),  # type: ignore[attr-defined]
        NmapDevice.last_seen_at.is_not(None),  # type: ignore[union-attr]
        NmapDevice.last_seen_at < cutoff,  # type: ignore[operator]
    )
    if integration_id is not None:
        q = q.where(NmapDevice.integration_id == integration_id)
    rows = list(session.exec(q).all())
    n = 0
    now = datetime.utcnow()
    for d in rows:
        d.state = "stale"
        d.updated_at = now
        session.add(d)
        n += 1
    if n:
        session.commit()
    return n


def _open_ports_summary(
    ports_json: Optional[str], *, limit: int = 8
) -> list[dict[str, Any]]:
    if not ports_json:
        return []
    try:
        ports = json.loads(ports_json)
        if not isinstance(ports, list):
            return []
        open_p = [
            p
            for p in ports
            if str((p or {}).get("state") or "").lower() == "open"
        ]
        open_p.sort(key=lambda p: int((p or {}).get("port") or 0))
        out: list[dict[str, Any]] = []
        for p in open_p[: max(1, min(30, limit))]:
            out.append(
                {
                    "port": p.get("port"),
                    "protocol": p.get("protocol") or "tcp",
                    "service": p.get("service") or "",
                    "product": p.get("product") or "",
                    "version": p.get("version") or "",
                    "state": p.get("state") or "open",
                }
            )
        return out
    except Exception:
        return []


def _count_open_ports(ports_json: Optional[str]) -> int:
    if not ports_json:
        return 0
    try:
        ports = json.loads(ports_json)
        if not isinstance(ports, list):
            return 0
        return sum(1 for p in ports if str(p.get("state") or "").lower() == "open")
    except Exception:
        return 0


def device_list_item(device: NmapDevice) -> dict[str, Any]:
    """Row payload for devices table / host list (WebMap-style)."""
    from .device_classify import profile_dict_from_device

    services = _open_ports_summary(device.ports_json, limit=6)
    profile = profile_dict_from_device(device)
    return {
        "row": device,
        "open_ports": _count_open_ports(device.ports_json),
        "services": services,
        "service_labels": [
            f"{s['port']}/{s.get('service') or '?'}" for s in services[:5]
        ],
        "profile": profile,
        "kind": profile.get("kind") or "unknown",
        "kind_label": profile.get("label") or "",
        "kind_overridden": bool(profile.get("overridden")),
        "map_role": (getattr(device, "map_role", None) or "").strip() or "",
        "display_name": (getattr(device, "display_name", None) or "").strip() or "",
        "label": device_display_name(device),
    }

def set_device_state(
    session: Session,
    device: NmapDevice,
    state: str,
    *,
    linked_server_id: int | None = None,
) -> NmapDevice:
    """Lifecycle: new → known (reviewed) → linked | ignored; stale when not seen.

    *known* means the operator has acknowledged the device (clears the *new*
    inbox). Independent of map name / kind. Rescans leave *new* until marked.
    """
    allowed = {"new", "known", "linked", "ignored", "stale"}
    if state not in allowed:
        raise ValueError(f"invalid state {state}")
    device.state = state
    if state == "linked":
        if not linked_server_id:
            raise ValueError("linked_server_id required for linked state")
        device.linked_server_id = linked_server_id
    elif state in ("new", "stale", "ignored", "known"):
        # Non-linked lifecycle states do not keep a Server FK
        device.linked_server_id = None
    device.updated_at = datetime.utcnow()
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


def mark_device_known(session: Session, device: NmapDevice) -> NmapDevice:
    """Operator reviewed this discovery — leave the *new* inbox."""
    if device.state == "linked":
        return device  # keep linked; do not demote
    return set_device_state(session, device, "known")


def mark_device_new(session: Session, device: NmapDevice) -> NmapDevice:
    """Re-flag as new (revisit inbox) without deleting history."""
    if device.state == "linked":
        raise ValueError("unlink before marking new")
    return set_device_state(session, device, "new")


def link_device(
    session: Session, device: NmapDevice, server_id: int
) -> NmapDevice:
    device.state = "linked"
    device.linked_server_id = server_id
    device.updated_at = datetime.utcnow()
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


def unlink_device(session: Session, device: NmapDevice) -> NmapDevice:
    device.state = "known"
    device.linked_server_id = None
    device.updated_at = datetime.utcnow()
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


def parse_cidrs_textarea(raw: str) -> list[str]:
    lines = []
    for part in (raw or "").replace(",", "\n").splitlines():
        s = part.strip()
        if s and not s.startswith("#"):
            lines.append(s)
    return lines


# --- Soft embed helpers (N8): discovery devices linked to fleet servers ---


def devices_for_server(
    session: Session, server_id: int, *, limit: int = 5
) -> list[NmapDevice]:
    """Nmap devices linked to a managed Server."""
    q = (
        select(NmapDevice)
        .where(NmapDevice.linked_server_id == server_id)
        .order_by(NmapDevice.updated_at.desc())
        .limit(max(1, min(20, limit)))
    )
    return list(session.exec(q).all())


def discovery_embed_for_server(
    session: Session, server_id: int
) -> dict[str, Any] | None:
    """Payload for server detail soft-embed card, or None if not linked."""
    from .script_classify import classify_scripts, script_summary_counts
    from ...models import NmapScriptResult

    devices = devices_for_server(session, server_id, limit=3)
    if not devices:
        return None
    # Prefer primary device (most recently updated)
    primary = devices[0]
    scripts = list(
        session.exec(
            select(NmapScriptResult)
            .where(NmapScriptResult.device_id == primary.id)
            .order_by(NmapScriptResult.id.desc())
            .limit(40)
        ).all()
    )
    from .device_classify import profile_dict_from_device

    classified = classify_scripts(scripts)
    counts = script_summary_counts(classified)
    services = _open_ports_summary(primary.ports_json, limit=6)
    profile = profile_dict_from_device(primary)
    return {
        "device": primary,
        "devices": devices,
        "open_ports": _count_open_ports(primary.ports_json),
        "services": services,
        "script_counts": counts,
        "profile": profile,
        "href": (
            f"/integrations/{primary.integration_id}"
            f"?tab=devices&device={primary.id}"
        ),
        "network_href": f"/integrations/{primary.integration_id}?tab=network",
    }


def discovery_chips_by_server(
    session: Session, server_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Map server_id → small chip payload for server list."""
    if not server_ids:
        return {}
    # SQLModel/SQLAlchemy IN filter
    rows = list(
        session.exec(
            select(NmapDevice).where(
                NmapDevice.linked_server_id.in_(list(server_ids))  # type: ignore[attr-defined]
            )
        ).all()
    )
    by: dict[int, list[NmapDevice]] = {}
    for d in rows:
        if d.linked_server_id is None:
            continue
        by.setdefault(int(d.linked_server_id), []).append(d)
    out: dict[int, dict[str, Any]] = {}
    for sid, devs in by.items():
        primary = sorted(
            devs,
            key=lambda x: x.updated_at or x.first_seen_at or datetime.utcnow(),
            reverse=True,
        )[0]
        out[sid] = {
            "device_id": primary.id,
            "ip": primary.ip_address,
            "hostname": primary.hostname or "",
            "open_ports": _count_open_ports(primary.ports_json),
            "state": primary.state,
            "href": (
                f"/integrations/{primary.integration_id}"
                f"?tab=devices&device={primary.id}"
            ),
        }
    return out

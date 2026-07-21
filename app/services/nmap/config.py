"""LAN Discovery integration config (type=nmap) helpers."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from ...models import Integration, NmapDevice, NmapScanRun, NmapScanSchedule
from ..integrations import registry as reg
from .allowlist import validate_cidrs
from .paths import vuln_pack_status
from .runtime import worker_online

BASE_URL_LOCAL = "local://nmap"


def _cfg_cidr_list(cfg: dict[str, Any], key: str) -> list[str]:
    raw = cfg.get(key) or []
    if isinstance(raw, str):
        raw = [c.strip() for c in raw.replace("\n", ",").split(",") if c.strip()]
    return [str(c).strip() for c in raw if str(c).strip()]


def parse_nmap_config(integration: Integration) -> dict[str, Any]:
    cfg = reg.parse_config(integration.config_json)
    return {
        "cidrs": _cfg_cidr_list(cfg, "cidrs"),
        "excludes": _cfg_cidr_list(cfg, "excludes"),
        # Exclude from inventory/detailed/deep only — discovery still allowed
        "excludes_port_scans": _cfg_cidr_list(cfg, "excludes_port_scans"),
        # Exclude from deep only
        "excludes_deep": _cfg_cidr_list(cfg, "excludes_deep"),
        # Default False so reverse DNS hostnames appear in scans (was True = always -n).
        "skip_dns": bool(cfg.get("skip_dns", False)),
        "use_syn": bool(cfg.get("use_syn", False)),
        "vuln_enabled": bool(cfg.get("vuln_enabled", False)),
        "notes": str(cfg.get("notes") or ""),
    }


def dump_nmap_config(
    *,
    cidrs: list[str],
    excludes: list[str] | None = None,
    excludes_port_scans: list[str] | None = None,
    excludes_deep: list[str] | None = None,
    skip_dns: bool = False,
    use_syn: bool = False,
    vuln_enabled: bool = False,
    notes: str = "",
) -> str:
    ok, errs = validate_cidrs(cidrs)
    if errs and not ok:
        raise ValueError("; ".join(errs))
    if errs:
        # partial invalids rejected
        raise ValueError("; ".join(errs))
    ex_ok, ex_errs = validate_cidrs(excludes or [])
    if ex_errs:
        raise ValueError(f"always exclude: {'; '.join(ex_errs)}")
    ex_port, ex_port_errs = validate_cidrs(excludes_port_scans or [])
    if ex_port_errs:
        raise ValueError(f"port-scan exclude: {'; '.join(ex_port_errs)}")
    ex_deep, ex_deep_errs = validate_cidrs(excludes_deep or [])
    if ex_deep_errs:
        raise ValueError(f"deep exclude: {'; '.join(ex_deep_errs)}")
    return reg.dump_config(
        {
            "cidrs": ok,
            "excludes": ex_ok,
            "excludes_port_scans": ex_port,
            "excludes_deep": ex_deep,
            "skip_dns": bool(skip_dns),
            "use_syn": bool(use_syn),
            "vuln_enabled": bool(vuln_enabled),
            "notes": (notes or "")[:500],
        }
    )


def create_nmap(
    session: Session,
    *,
    name: str = "LAN Discovery",
    cidrs: list[str],
    excludes: list[str] | None = None,
    excludes_port_scans: list[str] | None = None,
    excludes_deep: list[str] | None = None,
    skip_dns: bool = False,
    use_syn: bool = False,
    vuln_enabled: bool = False,
    notes: str = "",
    enabled: bool = True,
) -> Integration:
    existing = reg.list_integrations(session, type_filter=reg.TYPE_NMAP)
    if existing:
        raise ValueError("A LAN Discovery integration already exists — edit it instead")
    cfg = dump_nmap_config(
        cidrs=cidrs,
        excludes=excludes,
        excludes_port_scans=excludes_port_scans,
        excludes_deep=excludes_deep,
        skip_dns=skip_dns,
        use_syn=use_syn,
        vuln_enabled=vuln_enabled,
        notes=notes,
    )
    now = datetime.utcnow()
    row = Integration(
        type=reg.TYPE_NMAP,
        name=(name or "LAN Discovery").strip() or "LAN Discovery",
        base_url=BASE_URL_LOCAL,
        enabled=bool(enabled),
        config_json=cfg,
        credentials_encrypted=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_nmap(
    session: Session,
    integration: Integration,
    *,
    name: str,
    cidrs: list[str],
    excludes: list[str] | None = None,
    excludes_port_scans: list[str] | None = None,
    excludes_deep: list[str] | None = None,
    skip_dns: bool = False,
    use_syn: bool = False,
    vuln_enabled: bool = False,
    notes: str = "",
    enabled: bool = True,
) -> Integration:
    if integration.type != reg.TYPE_NMAP:
        raise ValueError("not an nmap integration")
    integration.name = (name or "LAN Discovery").strip() or "LAN Discovery"
    integration.base_url = BASE_URL_LOCAL
    integration.enabled = bool(enabled)
    integration.config_json = dump_nmap_config(
        cidrs=cidrs,
        excludes=excludes,
        excludes_port_scans=excludes_port_scans,
        excludes_deep=excludes_deep,
        skip_dns=skip_dns,
        use_syn=use_syn,
        vuln_enabled=vuln_enabled,
        notes=notes,
    )
    integration.updated_at = datetime.utcnow()
    session.add(integration)
    session.commit()
    session.refresh(integration)
    return integration


def refresh_status(session: Session, integration: Integration) -> dict[str, Any]:
    """Update last_status_json with worker / pack / device counts (no remote poll)."""
    cfg = parse_nmap_config(integration)
    devices = session.exec(
        select(NmapDevice).where(NmapDevice.integration_id == integration.id)
    ).all()
    by_state: dict[str, int] = {}
    for d in devices:
        by_state[d.state] = by_state.get(d.state, 0) + 1
    runs = session.exec(
        select(NmapScanRun)
        .where(NmapScanRun.integration_id == integration.id)
        .order_by(NmapScanRun.id.desc())
        .limit(1)
    ).all()
    last_run = runs[0] if runs else None
    pack = vuln_pack_status()
    online = worker_online()
    now = datetime.utcnow()
    payload = {
        "ok": bool(online.get("online")) and bool(cfg.get("cidrs")),
        "worker_online": bool(online.get("online")),
        "worker": online,
        "cidrs": cfg.get("cidrs") or [],
        "device_count": len(devices),
        "devices_by_state": by_state,
        "vuln_pack": pack,
        "vuln_enabled": bool(cfg.get("vuln_enabled")),
        "last_run_id": last_run.id if last_run else None,
        "last_run_status": last_run.status if last_run else None,
        "last_run_at": last_run.finished_at.isoformat() + "Z"
        if last_run and last_run.finished_at
        else None,
        "polled_at": now.isoformat() + "Z",
    }
    integration.last_status_json = json.dumps(payload, separators=(",", ":"))
    integration.last_polled_at = now
    integration.last_error = None if payload["ok"] else (
        "nmap worker offline" if not online.get("online") else "no CIDRs configured"
    )
    integration.updated_at = now
    session.add(integration)
    session.commit()
    return payload


def list_devices(
    session: Session,
    integration_id: int,
    *,
    state: str | None = None,
    limit: int = 500,
    apply_stale: bool = True,
) -> list[NmapDevice]:
    if apply_stale:
        from .device_ops import apply_stale_device_states

        apply_stale_device_states(session, integration_id=integration_id)
    q = select(NmapDevice).where(NmapDevice.integration_id == integration_id)
    if state:
        q = q.where(NmapDevice.state == state)
    q = q.order_by(NmapDevice.ip_address)
    rows = list(session.exec(q).all())
    return rows[: max(1, min(2000, limit))]



# --- Re-exports (device lifecycle + Hosts fabric projection) ---
from .device_ops import (  # noqa: E402
    DEVICE_STATE_LABELS,
    DEVICE_STATES_OPERATOR,
    STALE_AFTER_DAYS,
    apply_stale_device_states,
    device_display_name,
    device_list_item,
    devices_for_server,
    discovery_chips_by_server,
    discovery_embed_for_server,
    link_device,
    mark_device_known,
    mark_device_new,
    set_device_display_name,
    set_device_kind_override,
    set_device_map_identity,
    set_device_map_role,
    set_device_state,
    unlink_device,
    _count_open_ports,
    _open_ports_summary,
)
from .fabric_projection import (  # noqa: E402
    discovery_hosts_for_fabric,
    gateway_map_info,
)



def _ip_sort_key(ip: str) -> tuple:
    import ipaddress

    try:
        obj = ipaddress.ip_address(ip)
        return (0, int(obj))
    except ValueError:
        return (1, ip)

def list_runs(
    session: Session, integration_id: int, *, limit: int = 30
) -> list[NmapScanRun]:
    q = (
        select(NmapScanRun)
        .where(NmapScanRun.integration_id == integration_id)
        .order_by(NmapScanRun.id.desc())
        .limit(max(1, min(100, limit)))
    )
    return list(session.exec(q).all())


def list_schedules(session: Session, integration_id: int) -> list[NmapScanSchedule]:
    return list(
        session.exec(
            select(NmapScanSchedule)
            .where(NmapScanSchedule.integration_id == integration_id)
            .order_by(NmapScanSchedule.id)
        ).all()
    )


def network_view_payload(
    session: Session, integration: Integration
) -> dict[str, Any]:
    """Build subnet-grouped nodes for network view (WebMap-style host cards)."""
    import ipaddress

    cfg = parse_nmap_config(integration)
    devices = list_devices(session, integration.id, limit=1000)
    groups: dict[str, list[dict[str, Any]]] = {}
    by_state: dict[str, int] = {}
    total_open = 0
    for d in devices:
        by_state[d.state] = by_state.get(d.state, 0) + 1
        if d.state == "ignored":
            continue
        try:
            ip = ipaddress.ip_address(d.ip_address)
            if isinstance(ip, ipaddress.IPv4Address):
                net = str(ipaddress.ip_network(f"{ip}/24", strict=False))
            else:
                net = str(ipaddress.ip_network(f"{ip}/64", strict=False))
        except ValueError:
            net = "unknown"
        open_ports = _open_ports_summary(d.ports_json, limit=8)
        total_open += len(open_ports) if open_ports else _count_open_ports(d.ports_json)
        last_intensity = None
        last_run_id = getattr(d, "last_run_id", None)
        if last_run_id:
            run = session.get(NmapScanRun, last_run_id)
            if run:
                last_intensity = run.intensity
        from .device_classify import profile_dict_from_device

        profile = profile_dict_from_device(d)
        disp = device_display_name(d)
        node = {
            "id": d.id,
            "ip": d.ip_address,
            "hostname": d.hostname or "",
            "display_name": (getattr(d, "display_name", None) or "").strip() or "",
            "label": disp,
            "mac": d.mac_address or "",
            "mac_vendor": getattr(d, "mac_vendor", None) or "",
            "state": d.state,
            "linked_server_id": d.linked_server_id,
            "os": d.os_summary or "",
            "ports_open": _count_open_ports(d.ports_json),
            "services": open_ports,
            # ports_json is always the latest snapshot that recorded ports
            # (discovery does not clear a previous inventory/deep snapshot)
            "ports_source": "latest_snapshot",
            "last_run_intensity": last_intensity,
            "last_seen_at": (
                d.last_seen_at.isoformat() + "Z"
                if getattr(d, "last_seen_at", None)
                else None
            ),
            "kind": profile.get("kind") or "unknown",
            "kind_label": profile.get("label") or "",
            "kind_short": profile.get("short") or "?",
            "kind_confidence": profile.get("confidence") or "low",
            "kind_overridden": bool(profile.get("overridden")),
            "map_role": (getattr(d, "map_role", None) or "").strip() or "",
            "profile": profile,
        }
        groups.setdefault(net, []).append(node)
    for g in groups.values():
        g.sort(key=lambda n: _ip_sort_key(n["ip"]))
    return {
        "cidrs": cfg.get("cidrs") or [],
        "groups": [
            {
                "subnet": k,
                "nodes": v,
                "count": len(v),
                "open_ports": sum(n.get("ports_open") or 0 for n in v),
            }
            for k, v in sorted(groups.items(), key=lambda x: x[0])
        ],
        "device_count": sum(len(v) for v in groups.values()),
        "by_state": by_state,
        "open_ports_total": total_open,
        "ports_note": (
            "Open ports are the latest snapshot per host (from inventory, detailed, "
            "or deep). Discovery does not clear prior port data. This is not a merge "
            "of every historical scan."
        ),
    }


def parse_cidrs_textarea(raw: str) -> list[str]:
    lines = []
    for part in (raw or "").replace(",", "\n").splitlines():
        s = part.strip()
        if s and not s.startswith("#"):
            lines.append(s)
    return lines


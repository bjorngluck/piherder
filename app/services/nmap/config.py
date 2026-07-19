"""LAN Discovery integration config (type=nmap) helpers."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from ...models import Integration, NmapDevice, NmapScanRun, NmapScanSchedule
from ..integrations import registry as reg
from .allowlist import validate_cidrs
from .paths import vuln_pack_status
from .runtime import worker_online

BASE_URL_LOCAL = "local://nmap"


def parse_nmap_config(integration: Integration) -> dict[str, Any]:
    cfg = reg.parse_config(integration.config_json)
    cidrs = cfg.get("cidrs") or []
    if isinstance(cidrs, str):
        cidrs = [c.strip() for c in cidrs.replace("\n", ",").split(",") if c.strip()]
    excludes = cfg.get("excludes") or []
    if isinstance(excludes, str):
        excludes = [c.strip() for c in excludes.replace("\n", ",").split(",") if c.strip()]
    return {
        "cidrs": [str(c).strip() for c in cidrs if str(c).strip()],
        "excludes": [str(c).strip() for c in excludes if str(c).strip()],
        "skip_dns": bool(cfg.get("skip_dns", True)),
        "use_syn": bool(cfg.get("use_syn", False)),
        "vuln_enabled": bool(cfg.get("vuln_enabled", False)),
        "notes": str(cfg.get("notes") or ""),
    }


def dump_nmap_config(
    *,
    cidrs: list[str],
    excludes: list[str] | None = None,
    skip_dns: bool = True,
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
        raise ValueError("; ".join(ex_errs))
    return reg.dump_config(
        {
            "cidrs": ok,
            "excludes": ex_ok,
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
    skip_dns: bool = True,
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
    skip_dns: bool = True,
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
) -> list[NmapDevice]:
    q = select(NmapDevice).where(NmapDevice.integration_id == integration_id)
    if state:
        q = q.where(NmapDevice.state == state)
    q = q.order_by(NmapDevice.ip_address)
    rows = list(session.exec(q).all())
    return rows[: max(1, min(2000, limit))]


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
    """Build subnet-grouped nodes for network view MVP."""
    import ipaddress

    cfg = parse_nmap_config(integration)
    devices = list_devices(session, integration.id, limit=1000)
    # Group by /24 for IPv4
    groups: dict[str, list[dict[str, Any]]] = {}
    for d in devices:
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
        node = {
            "id": d.id,
            "ip": d.ip_address,
            "hostname": d.hostname or "",
            "mac": d.mac_address or "",
            "state": d.state,
            "linked_server_id": d.linked_server_id,
            "os": d.os_summary or "",
            "ports_open": _count_open_ports(d.ports_json),
        }
        groups.setdefault(net, []).append(node)
    for g in groups.values():
        g.sort(key=lambda n: n["ip"])
    return {
        "cidrs": cfg.get("cidrs") or [],
        "groups": [
            {"subnet": k, "nodes": v, "count": len(v)}
            for k, v in sorted(groups.items(), key=lambda x: x[0])
        ],
        "device_count": sum(len(v) for v in groups.values()),
    }


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


def set_device_state(
    session: Session,
    device: NmapDevice,
    state: str,
    *,
    linked_server_id: int | None = None,
) -> NmapDevice:
    allowed = {"new", "known", "linked", "ignored", "stale"}
    if state not in allowed:
        raise ValueError(f"invalid state {state}")
    device.state = state
    if state == "linked":
        if not linked_server_id:
            raise ValueError("linked_server_id required for linked state")
        device.linked_server_id = linked_server_id
    elif state == "ignored":
        device.linked_server_id = None
    elif state in ("new", "stale"):
        device.linked_server_id = None
    # "known" keeps existing link if any
    device.updated_at = datetime.utcnow()
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


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

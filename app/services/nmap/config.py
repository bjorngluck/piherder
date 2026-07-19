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
        node = {
            "id": d.id,
            "ip": d.ip_address,
            "hostname": d.hostname or "",
            "mac": d.mac_address or "",
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


def device_list_item(device: NmapDevice) -> dict[str, Any]:
    """Row payload for devices table / host list (WebMap-style)."""
    services = _open_ports_summary(device.ports_json, limit=6)
    return {
        "row": device,
        "open_ports": _count_open_ports(device.ports_json),
        "services": services,
        "service_labels": [
            f"{s['port']}/{s.get('service') or '?'}" for s in services[:5]
        ],
    }


def _ip_sort_key(ip: str) -> tuple:
    import ipaddress

    try:
        obj = ipaddress.ip_address(ip)
        return (0, int(obj))
    except ValueError:
        return (1, ip)


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
    classified = classify_scripts(scripts)
    counts = script_summary_counts(classified)
    services = _open_ports_summary(primary.ports_json, limit=6)
    return {
        "device": primary,
        "devices": devices,
        "open_ports": _count_open_ports(primary.ports_json),
        "services": services,
        "script_counts": counts,
        "href": f"/integrations/{primary.integration_id}?tab=devices&device={primary.id}",
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

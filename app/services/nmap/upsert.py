"""Upsert discovered devices from parsed nmap hosts."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Optional, Sequence

from sqlmodel import Session, select

from ...models import NmapDevice, NmapScriptResult
from .parse import ParsedHost, open_ports


def _norm_mac(mac: Optional[str]) -> Optional[str]:
    if not mac:
        return None
    s = re.sub(r"[^0-9A-Fa-f]", "", mac).upper()
    if len(s) != 12:
        return mac.strip().upper() or None
    return ":".join(s[i : i + 2] for i in range(0, 12, 2))


def device_identity_key(*, mac: Optional[str], ip: str) -> str:
    """Stable key: prefer MAC, else IP."""
    m = _norm_mac(mac)
    if m:
        return f"mac:{m}"
    return f"ip:{(ip or '').strip()}"


def _ports_payload(host: ParsedHost, *, max_ports: int = 512) -> str:
    ports = []
    for p in host.ports[:max_ports]:
        ports.append(
            {
                "port": p.port,
                "protocol": p.protocol,
                "state": p.state,
                "service": p.service,
                "product": p.product,
                "version": p.version,
            }
        )
    return json.dumps(ports, separators=(",", ":"))


def upsert_hosts_from_parse(
    session: Session,
    *,
    integration_id: int,
    hosts: Sequence[ParsedHost],
    run_id: Optional[int] = None,
    only_up: bool = True,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Create or update NmapDevice rows; replace script results for this run.

    Returns summary counts. Does **not** create Server rows.
    """
    now = now or datetime.utcnow()
    created = 0
    updated = 0
    skipped = 0
    scripts_written = 0

    for host in hosts:
        if only_up and (host.status or "").lower() not in ("up", "unknown"):
            # still record up only by default; discovery -sn marks up
            if (host.status or "").lower() == "down":
                skipped += 1
                continue

        ip = (host.ip_address or "").strip()
        if not ip:
            skipped += 1
            continue

        mac = _norm_mac(host.mac_address)
        key = device_identity_key(mac=mac, ip=ip)

        existing = session.exec(
            select(NmapDevice).where(
                NmapDevice.integration_id == integration_id,
                NmapDevice.identity_key == key,
            )
        ).first()

        # Also try IP match if MAC key is new but IP known (DHCP churn helper)
        if existing is None and mac:
            by_ip = session.exec(
                select(NmapDevice).where(
                    NmapDevice.integration_id == integration_id,
                    NmapDevice.ip_address == ip,
                    NmapDevice.state != "ignored",
                )
            ).first()
            if by_ip is not None and not by_ip.mac_address:
                existing = by_ip
                existing.identity_key = key
                existing.mac_address = mac

        ports_json = _ports_payload(host)
        open_n = len(open_ports(host))
        # Discovery (-sn) and empty port results must not wipe a prior inventory/deep snapshot
        has_port_data = bool(host.ports)

        if existing is None:
            dev = NmapDevice(
                integration_id=integration_id,
                identity_key=key,
                ip_address=ip,
                hostname=host.hostname,
                mac_address=mac,
                state="new",
                os_summary=host.os_summary,
                ports_json=ports_json if has_port_data else None,
                last_seen_at=now,
                first_seen_at=now,
                last_run_id=run_id,
                updated_at=now,
            )
            session.add(dev)
            session.flush()
            created += 1
            device = dev
        else:
            if existing.state == "ignored":
                skipped += 1
                continue
            existing.ip_address = ip
            if host.hostname:
                existing.hostname = host.hostname
            if mac:
                existing.mac_address = mac
                existing.identity_key = key
            if host.os_summary:
                existing.os_summary = host.os_summary
            if has_port_data:
                existing.ports_json = ports_json
            existing.last_seen_at = now
            existing.last_run_id = run_id
            existing.updated_at = now
            if existing.state == "stale":
                existing.state = "known"
            elif existing.state == "new":
                pass
            elif existing.state not in ("linked", "ignored"):
                existing.state = "known"
            session.add(existing)
            updated += 1
            device = existing

        if host.scripts and device.id is not None:
            # Drop prior results for this device+run to keep latest run clean
            if run_id is not None:
                old = session.exec(
                    select(NmapScriptResult).where(
                        NmapScriptResult.device_id == device.id,
                        NmapScriptResult.run_id == run_id,
                    )
                ).all()
                for row in old:
                    session.delete(row)
            for sc in host.scripts:
                session.add(
                    NmapScriptResult(
                        device_id=device.id,
                        run_id=run_id,
                        script_id=sc.script_id[:128],
                        output=(sc.output or "")[:50000] or None,
                        cve_ids_json=json.dumps(sc.cve_ids) if sc.cve_ids else None,
                        port=int(sc.port) if getattr(sc, "port", None) is not None else None,
                        protocol=(
                            (sc.protocol or "tcp")[:16]
                            if getattr(sc, "port", None) is not None
                            else (
                                (sc.protocol or None)[:16]
                                if getattr(sc, "protocol", None)
                                else None
                            )
                        ),
                    )
                )
                scripts_written += 1

        # open_n reserved for future metrics; silence lint
        _ = open_n

    session.commit()
    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "scripts_written": scripts_written,
        "hosts_processed": created + updated,
    }

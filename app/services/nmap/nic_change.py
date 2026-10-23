"""Possible NIC change: a new MAC at a hostname and IP that already has a quiet row.

Device identity stays the MAC. Rows are not merged and are not auto-ignored.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlmodel import Session, select

from ...models import NmapDevice, NmapScanSchedule

DEFAULT_SCAN_INTERVAL = timedelta(hours=24)


def scan_interval(
    session: Session,
    integration_id: int,
    now: Optional[datetime] = None,
) -> timedelta:
    """Shortest enabled schedule. No enabled schedule means one day."""
    now = now or datetime.utcnow()
    rows = list(
        session.exec(
            select(NmapScanSchedule).where(
                NmapScanSchedule.integration_id == int(integration_id),
                NmapScanSchedule.enabled == True,  # noqa: E712
            )
        ).all()
    )
    gaps: list[timedelta] = []
    for row in rows:
        hours = int(row.interval_hours or 0)
        if hours > 0:
            gaps.append(timedelta(hours=hours))
            continue
        cron = (row.cron or "").strip()
        if not cron:
            continue
        gap = _cron_gap(cron, now)
        if gap is not None and gap.total_seconds() > 0:
            gaps.append(gap)
    if not gaps:
        return DEFAULT_SCAN_INTERVAL
    return min(gaps)


def _cron_gap(cron: str, now: datetime) -> Optional[timedelta]:
    try:
        from ._cron import cron_trigger

        trigger = cron_trigger(cron)
        first = trigger.get_next_fire_time(None, now)
        if first is None:
            return None
        second = trigger.get_next_fire_time(first, first)
        if second is None:
            return None
        return second - first
    except Exception:
        return None


def _host_ip(device: NmapDevice) -> Optional[tuple[str, str]]:
    host = (device.hostname or "").strip().casefold()
    ip = (device.ip_address or "").strip()
    if not host or not ip:
        return None
    return host, ip


def nic_pairs(
    session: Session,
    integration_id: int,
    now: Optional[datetime] = None,
) -> list[tuple[NmapDevice, NmapDevice]]:
    """(newer MAC row, stale row) when hostname and IP match and the quiet row is past one interval."""
    now = now or datetime.utcnow()
    cutoff = now - scan_interval(session, integration_id, now)
    rows = list(
        session.exec(
            select(NmapDevice).where(
                NmapDevice.integration_id == int(integration_id),
                NmapDevice.state != "ignored",
            )
        ).all()
    )
    groups: dict[tuple[str, str], list[NmapDevice]] = {}
    for row in rows:
        key = _host_ip(row)
        if key is None:
            continue
        groups.setdefault(key, []).append(row)
    pairs: list[tuple[NmapDevice, NmapDevice]] = []
    for group in groups.values():
        recent = [
            row
            for row in group
            if row.last_seen_at is not None and row.last_seen_at >= cutoff
        ]
        quiet = [
            row
            for row in group
            if row.last_seen_at is None or row.last_seen_at < cutoff
        ]
        for newer in recent:
            mac = (newer.mac_address or "").strip().upper()
            if not mac:
                continue
            for older in quiet:
                if older.id == newer.id:
                    continue
                other = (older.mac_address or "").strip().upper()
                if other == mac:
                    continue
                pairs.append((newer, older))
    return pairs


def nic_index(
    session: Session,
    integration_id: int,
    now: Optional[datetime] = None,
) -> dict[int, dict[str, Any]]:
    """device id -> badge payload. partner_id is the other row the button acts on."""
    out: dict[int, dict[str, Any]] = {}
    for newer, older in nic_pairs(session, integration_id, now):
        if newer.id is None or older.id is None:
            continue
        slot = out.setdefault(
            int(newer.id),
            {
                "role": "new",
                "stale_device_ids": [],
                "new_device_id": int(newer.id),
                "partner_id": int(older.id),
            },
        )
        if int(older.id) not in slot["stale_device_ids"]:
            slot["stale_device_ids"].append(int(older.id))
        out.setdefault(
            int(older.id),
            {
                "role": "stale",
                "stale_device_ids": [int(older.id)],
                "new_device_id": int(newer.id),
                "partner_id": int(newer.id),
            },
        )
    return out


def note_inserted_mac(
    session: Session,
    device_id: int,
    integration_id: int,
    now: Optional[datetime] = None,
) -> int:
    """Open one info notification per stale row. Returns how many were opened."""
    from .. import notifications as notif_svc

    opened = 0
    for newer, older in nic_pairs(session, integration_id, now):
        if newer.id != int(device_id) or older.id is None:
            continue
        host = (newer.hostname or older.hostname or "").strip()
        ip = (newer.ip_address or "").strip()
        notif_svc.notify_lan_nic_change(
            session,
            integration_id=int(integration_id),
            new_device_id=int(newer.id),
            stale_device_id=int(older.id),
            hostname=host,
            ip=ip,
        )
        opened += 1
    return opened


def replace_stale_mac(
    session: Session,
    *,
    integration_id: int,
    new_device_id: int,
    stale_device_id: int,
    user_id: int | None = None,
    api_token_id: int | None = None,
    api_token_name: str | None = None,
    client_ip: str | None = None,
) -> dict[str, Any]:
    """Ignore the stale row, copy its custom fields onto the new MAC, one audit row."""
    from ..audit_write import make_audit_log
    from .device_ops import set_device_state

    newer = session.get(NmapDevice, int(new_device_id))
    older = session.get(NmapDevice, int(stale_device_id))
    if (
        newer is None
        or older is None
        or int(newer.integration_id) != int(integration_id)
        or int(older.integration_id) != int(integration_id)
    ):
        raise ValueError("missing")
    matched = any(
        pair[0].id == newer.id and pair[1].id == older.id
        for pair in nic_pairs(session, integration_id)
    )
    if not matched:
        raise ValueError("not_a_nic_change")
    if (older.display_name or "").strip():
        newer.display_name = older.display_name
    if (older.kind_override or "").strip():
        newer.kind_override = older.kind_override
    if (older.notes or "").strip():
        newer.notes = older.notes
    newer.updated_at = datetime.utcnow()
    session.add(newer)
    set_device_state(session, older, "ignored")
    session.add(
        make_audit_log(
            action="nmap_replace_stale_mac",
            user_id=user_id,
            api_token_id=api_token_id,
            api_token_name=api_token_name,
            client_ip=client_ip,
            details=(
                f"new={newer.id} stale={older.id} "
                f"hostname={newer.hostname or ''} ip={newer.ip_address}"
            )[:2000],
        )
    )
    session.commit()
    try:
        from .. import notifications as notif_svc

        notif_svc.resolve_by_fingerprint(
            session,
            notif_svc.lan_nic_fingerprint(int(integration_id), int(newer.id), int(older.id)),
        )
    except Exception:
        pass
    session.refresh(newer)
    session.refresh(older)
    return {"new_device_id": newer.id, "stale_device_id": older.id, "state": older.state}

"""Secret-free LAN Discovery payloads for the token API and hosted MCP.

A start uses the ranges saved on the integration. The caller does not
supply targets, and vulnerability scripts stay off.
"""
from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from ...models import Integration, NmapDevice, NmapScanRun
from ..app_settings import utc_isoformat
from ..integrations import registry as reg
from .argv import INTENSITIES, INTENSITY_DISCOVERY
from .config import list_devices, list_runs, parse_nmap_config
from .scan import enqueue_nmap_scan

_RUN_LIMIT = 20
_DEVICE_LIMIT = 50
_TARGET_CAP = 64


def load_nmap(session: Session, integration_id: int) -> Integration | None:
    """Return the LAN Discovery row, or None when the id is missing or another type."""
    row = session.get(Integration, int(integration_id))
    if row is None or row.type != reg.TYPE_NMAP:
        return None
    return row


def _targets(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    for item in parsed:
        text = str(item).strip()
        if not text:
            continue
        out.append(text[:80])
        if len(out) >= _TARGET_CAP:
            break
    return out


def run_public(run: NmapScanRun) -> dict[str, Any]:
    """One scan. No artifact path and no script output."""
    error = (run.error or "").strip()
    return {
        "id": run.id,
        "integration_id": run.integration_id,
        "job_id": run.job_id,
        "intensity": run.intensity,
        "status": run.status,
        "hosts_up": int(run.hosts_up or 0),
        "hosts_total": int(run.hosts_total or 0),
        "ports_open": int(run.ports_open or 0),
        "targets": _targets(run.targets_json),
        "error": error[:300],
        "started_at": utc_isoformat(run.started_at),
        "finished_at": utc_isoformat(run.finished_at),
    }


def integration_public(integration: Integration, *, latest: NmapScanRun | None) -> dict[str, Any]:
    cfg = parse_nmap_config(integration)
    return {
        "id": integration.id,
        "name": integration.name,
        "enabled": bool(integration.enabled),
        "cidrs": list(cfg.get("cidrs") or [])[:_TARGET_CAP],
        "latest_run": run_public(latest) if latest is not None else None,
    }


def device_public(device: NmapDevice) -> dict[str, Any]:
    return {
        "id": device.id,
        "ip": device.ip_address,
        "hostname": (device.hostname or "")[:255],
        "display_name": (device.display_name or "")[:128],
        "state": device.state,
        "linked_server_id": device.linked_server_id,
        "last_seen_at": utc_isoformat(device.last_seen_at),
    }


def list_payload(session: Session) -> dict[str, Any]:
    rows = reg.list_integrations(session, type_filter=reg.TYPE_NMAP)
    items: list[dict[str, Any]] = []
    for row in rows:
        runs = list_runs(session, int(row.id), limit=1)
        latest = runs[0] if runs else None
        items.append(integration_public(row, latest=latest))
    return {"integrations": items}


def detail_payload(session: Session, integration: Integration) -> dict[str, Any]:
    runs = list_runs(session, int(integration.id), limit=_RUN_LIMIT)
    latest = runs[0] if runs else None
    devices = list_devices(
        session,
        int(integration.id),
        limit=_DEVICE_LIMIT,
        apply_stale=False,
    )
    return {
        "integration": integration_public(integration, latest=latest),
        "runs": [run_public(run) for run in runs],
        "devices": [device_public(device) for device in devices],
    }


def normalize_intensity(raw: str | None) -> str:
    text = (raw or "").strip().lower() or INTENSITY_DISCOVERY
    if text not in INTENSITIES:
        raise ValueError("bad_intensity")
    return text


def queue_saved_scan(
    session: Session,
    integration: Integration,
    *,
    intensity: str,
    user_id: int | None,
) -> tuple[Any, NmapScanRun, list[str]]:
    """Queue a scan of the saved ranges. Vulnerability scripts stay off."""
    if not integration.enabled:
        raise ValueError("disabled")
    cidrs = list(parse_nmap_config(integration).get("cidrs") or [])
    if not cidrs:
        raise ValueError("no_ranges")
    chosen = normalize_intensity(intensity)
    job, run = enqueue_nmap_scan(
        session,
        integration_id=int(integration.id),
        intensity=chosen,
        targets=cidrs,
        user_id=user_id,
        scan_options={"script_preset": "none", "vuln_scripts": False},
    )
    return job, run, cidrs


_PAGE_DEFAULT = 50
_PAGE_MAX = 200
_FILTER_STATES = ("new", "known", "linked", "ignored", "stale")
_SET_STATES = ("known", "new", "ignored")


def _caller(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": kwargs.get("user_id"),
        "api_token_id": kwargs.get("api_token_id"),
        "api_token_name": kwargs.get("api_token_name"),
        "client_ip": kwargs.get("client_ip"),
    }


def _device(session: Session, integration: Integration, device_id: int) -> NmapDevice:
    row = session.get(NmapDevice, int(device_id))
    if row is None or int(row.integration_id) != int(integration.id):
        raise ValueError("missing")
    return row


def _audit_action(
    session: Session,
    *,
    action: str,
    details: str,
    user_id: int | None,
    api_token_id: int | None,
    api_token_name: str | None,
    client_ip: str | None,
    server_id: int | None = None,
) -> None:
    from ..audit_write import make_audit_log

    session.add(
        make_audit_log(
            action=action,
            user_id=user_id,
            api_token_id=api_token_id,
            api_token_name=api_token_name,
            client_ip=client_ip,
            server_id=server_id,
            details=details[:2000],
        )
    )
    session.commit()


def devices_page(
    session: Session,
    integration: Integration,
    *,
    state: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Paged devices. Applies the offline pass so state=stale matches the UI."""
    from sqlalchemy import func
    from sqlmodel import select

    from .device_ops import apply_stale_device_states

    chosen = (state or "").strip().lower()
    if chosen and chosen not in _FILTER_STATES:
        raise ValueError("bad_filter")
    if limit < 1 or limit > _PAGE_MAX:
        raise ValueError("bad_limit")
    if offset < 0:
        raise ValueError("bad_offset")
    apply_stale_device_states(session, integration_id=int(integration.id))
    cond = [NmapDevice.integration_id == int(integration.id)]
    if chosen:
        cond.append(NmapDevice.state == chosen)
    total_raw = session.exec(select(func.count()).select_from(NmapDevice).where(*cond)).one()
    if isinstance(total_raw, tuple):
        total_raw = total_raw[0]
    total = int(total_raw or 0)
    rows = list(
        session.exec(
            select(NmapDevice)
            .where(*cond)
            .order_by(NmapDevice.ip_address)
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return {
        "devices": [device_public(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def rename_device(
    session: Session,
    integration: Integration,
    device_id: int,
    display_name: str,
    **caller: Any,
) -> dict[str, Any]:
    """Set the operator name. Kind and map role stay. A new or offline row becomes known."""
    from .device_ops import set_device_map_identity

    device = _device(session, integration, device_id)
    who = _caller(caller)
    set_device_map_identity(
        session,
        device,
        display_name=display_name,
        kind_override=device.kind_override,
        map_role=device.map_role,
        sync_network_gateway=False,
        mark_known=True,
        user_id=who["user_id"],
        api_token_id=who["api_token_id"],
        api_token_name=who["api_token_name"],
        client_ip=who["client_ip"],
    )
    session.refresh(device)
    return {"device": device_public(device)}


def set_device_operator_state(
    session: Session,
    integration: Integration,
    device_id: int,
    state: str,
    **caller: Any,
) -> dict[str, Any]:
    """known, new, or ignored. linked and stale are not set here."""
    from .device_ops import mark_device_known, mark_device_new, set_device_state

    chosen = (state or "").strip().lower()
    if chosen not in _SET_STATES:
        raise ValueError("bad_state")
    device = _device(session, integration, device_id)
    if chosen == "known":
        mark_device_known(session, device)
        action = "nmap_device_known"
    elif chosen == "new":
        try:
            mark_device_new(session, device)
        except ValueError as exc:
            raise ValueError("unlink_first") from exc
        action = "nmap_device_mark_new"
    else:
        set_device_state(session, device, "ignored")
        action = "nmap_device_ignored"
    session.refresh(device)
    who = _caller(caller)
    _audit_action(
        session,
        action=action,
        details=f"device={device.id}",
        **who,
    )
    return {"device": device_public(device)}


def ignore_device(session: Session, integration: Integration, device_id: int, **caller: Any) -> dict[str, Any]:
    return set_device_operator_state(session, integration, device_id, "ignored", **caller)


def unignore_device(session: Session, integration: Integration, device_id: int, **caller: Any) -> dict[str, Any]:
    """Same as mark known. A linked device stays linked."""
    from .device_ops import mark_device_known

    device = _device(session, integration, device_id)
    mark_device_known(session, device)
    session.refresh(device)
    who = _caller(caller)
    _audit_action(session, action="nmap_device_unignored", details=f"device={device.id}", **who)
    return {"device": device_public(device)}


def link_device_to_server(
    session: Session,
    integration: Integration,
    device_id: int,
    server_id: int,
    **caller: Any,
) -> dict[str, Any]:
    from ...models import Server
    from .device_ops import link_device

    device = _device(session, integration, device_id)
    server = session.get(Server, int(server_id))
    if server is None:
        raise ValueError("server")
    link_device(session, device, int(server.id))
    session.refresh(device)
    who = _caller(caller)
    _audit_action(
        session,
        action="nmap_device_linked",
        details=f"device={device.id} server={server.id}",
        server_id=int(server.id),
        **who,
    )
    return {"device": device_public(device)}


def unlink_device_from_server(
    session: Session,
    integration: Integration,
    device_id: int,
    **caller: Any,
) -> dict[str, Any]:
    from .device_ops import unlink_device

    device = _device(session, integration, device_id)
    unlink_device(session, device)
    session.refresh(device)
    who = _caller(caller)
    _audit_action(session, action="nmap_device_unlinked", details=f"device={device.id}", **who)
    return {"device": device_public(device)}


def _refuse_linked(device: NmapDevice) -> None:
    if device.state == "linked" or device.linked_server_id is not None:
        raise ValueError("linked")


def purge_one_device(
    session: Session,
    integration: Integration,
    device_id: int,
    **caller: Any,
) -> dict[str, Any]:
    from .device_ops import purge_device

    device = _device(session, integration, device_id)
    _refuse_linked(device)
    removed = int(device.id)
    ip = (device.ip_address or "")[:64]
    res = purge_device(session, device)
    who = _caller(caller)
    _audit_action(
        session,
        action="nmap_device_purged",
        details=f"device={removed} ip={ip} scripts={res.get('scripts_deleted', 0)}",
        **who,
    )
    return {"device_ids": [removed], "purged": 1}


def purge_stale_devices(
    session: Session,
    integration: Integration,
    **caller: Any,
) -> dict[str, Any]:
    """Delete offline rows only. Linked rows are never offline and are skipped."""
    from sqlmodel import select

    from .device_ops import apply_stale_device_states, purge_devices

    apply_stale_device_states(session, integration_id=int(integration.id))
    rows = list(
        session.exec(
            select(NmapDevice).where(
                NmapDevice.integration_id == int(integration.id),
                NmapDevice.state == "stale",
                NmapDevice.linked_server_id.is_(None),  # type: ignore[union-attr]
            )
        ).all()
    )
    ids = [int(row.id) for row in rows if row.id is not None]
    res = purge_devices(session, rows)
    who = _caller(caller)
    _audit_action(
        session,
        action="nmap_devices_purged_offline",
        details=f"purged={res.get('purged', 0)} scripts={res.get('scripts_deleted', 0)}",
        **who,
    )
    return {"device_ids": ids, "purged": int(res.get("purged") or 0)}


def _ip_in_cidrs(ip: str, cidrs: list[str]) -> bool:
    import ipaddress

    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return False
    for raw in cidrs:
        text = (raw or "").strip()
        if not text:
            continue
        try:
            if "/" not in text:
                text = f"{text}/32" if addr.version == 4 else f"{text}/128"
            if addr in ipaddress.ip_network(text, strict=False):
                return True
        except ValueError:
            continue
    return False


def queue_device_scan(
    session: Session,
    integration: Integration,
    device_id: int,
    *,
    intensity: str | None,
    user_id: int | None,
) -> tuple[Any, NmapScanRun, str]:
    """Scan one saved device. Vulnerability scripts stay off.

    The device address must fall inside a range saved on the integration.
    """
    if not integration.enabled:
        raise ValueError("disabled")
    cidrs = list(parse_nmap_config(integration).get("cidrs") or [])
    if not cidrs:
        raise ValueError("no_ranges")
    device = _device(session, integration, device_id)
    ip = (device.ip_address or "").strip()
    if not _ip_in_cidrs(ip, cidrs):
        raise ValueError("outside_ranges")
    chosen = normalize_intensity((intensity or "").strip().lower() or "deep")
    job, run = enqueue_nmap_scan(
        session,
        integration_id=int(integration.id),
        intensity=chosen,
        targets=[ip],
        user_id=user_id,
        scan_options={"script_preset": "none", "vuln_scripts": False},
    )
    return job, run, ip

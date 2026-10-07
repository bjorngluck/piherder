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

"""LAN Discovery (nmap) integration routes."""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from .. import templates as templates_mod
from ..database import get_session
from ..models import Integration, NmapDevice, NmapScanSchedule, Server, User
from ..security.auth import get_current_user, get_operator_user
from ..services.integrations import registry as reg
from ..services.nmap import config as nmap_cfg
from ..services.nmap import schedules as nmap_sched
from ..services.nmap.argv import INTENSITIES, INTENSITY_DEEP, INTENSITY_DISCOVERY
from ..services.nmap.paths import vuln_pack_status
from ..services.nmap.runtime import worker_online
from ..services.nmap.scan import enqueue_nmap_scan
from .integrations_common import router, _audit, _redirect, _can_mutate

logger = logging.getLogger(__name__)


def _require_nmap(session: Session, integration_id: int) -> Integration:
    row = reg.get_integration(session, integration_id)
    if not row or row.type != reg.TYPE_NMAP:
        raise HTTPException(404, "LAN Discovery integration not found")
    return row


def _resync_schedules() -> None:
    try:
        from ..main import HAS_SCHEDULER, scheduler
        from ..services.scheduler import sync_nmap_schedules

        sync_nmap_schedules(scheduler, HAS_SCHEDULER)
    except Exception as e:
        logger.debug("nmap schedule resync skipped: %s", e)


async def render_nmap_detail(request, session, user, integration: Integration):
    tab = (request.query_params.get("tab") or "overview").strip().lower()
    if tab not in ("overview", "devices", "network", "schedules", "runs"):
        tab = "overview"
    cfg = nmap_cfg.parse_nmap_config(integration)
    status = reg.parse_last_status(integration)
    online = worker_online()
    pack = vuln_pack_status()
    devices = nmap_cfg.list_devices(session, integration.id) if tab in (
        "devices",
        "network",
        "overview",
    ) else []
    state_filter = (request.query_params.get("state") or "").strip() or None
    if tab == "devices" and state_filter:
        devices = [d for d in devices if d.state == state_filter]
    device_rows = []
    for d in devices:
        device_rows.append(
            {
                "row": d,
                "open_ports": nmap_cfg._count_open_ports(d.ports_json),
            }
        )
    runs = nmap_cfg.list_runs(session, integration.id) if tab in ("runs", "overview") else []
    schedules = (
        nmap_cfg.list_schedules(session, integration.id)
        if tab in ("schedules", "overview")
        else []
    )
    network = (
        nmap_cfg.network_view_payload(session, integration) if tab == "network" else None
    )
    servers = list(
        session.exec(select(Server).order_by(Server.sort_order, Server.name)).all()
    )
    # Device detail drawer
    device_id = request.query_params.get("device")
    device = None
    device_ports = []
    if device_id:
        try:
            did = int(device_id)
            device = session.get(NmapDevice, did)
            if device and device.integration_id == integration.id and device.ports_json:
                try:
                    device_ports = json.loads(device.ports_json)
                except Exception:
                    device_ports = []
        except ValueError:
            device = None

    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_nmap_detail.html",
        context={
            "title": integration.name,
            "user": user,
            "integration": integration,
            "cfg": cfg,
            "status": status,
            "tab": tab,
            "worker_online": bool(online.get("online")),
            "worker": online,
            "vuln_pack": pack,
            "devices": devices,
            "device_rows": device_rows,
            "state_filter": state_filter or "",
            "runs": runs,
            "schedules": schedules,
            "network": network,
            "servers": servers,
            "device": device,
            "device_ports": device_ports,
            "intensities": INTENSITIES,
            "schedule_intensities": nmap_sched.INTENSITIES_SCHEDULE,
            "can_mutate": _can_mutate(user),
            "msg": request.query_params.get("msg") or "",
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


@router.get("/integrations/new/nmap", response_class=HTMLResponse)
async def nmap_new_form(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    existing = reg.list_integrations(session, type_filter=reg.TYPE_NMAP)
    if existing:
        return _redirect(f"/integrations/{existing[0].id}", msg="exists")
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="integrations_nmap_form.html",
        context={
            "title": "Add LAN Discovery",
            "user": user,
            "mode": "create",
            "integration": None,
            "form": {
                "name": "LAN Discovery",
                "cidrs": "192.168.1.0/24",
                "excludes": "",
                "skip_dns": True,
                "use_syn": False,
                "vuln_enabled": False,
                "notes": "",
                "enabled": True,
            },
            "error": request.query_params.get("error") or "",
            "detail": request.query_params.get("detail") or "",
        },
    )


@router.post("/integrations/new/nmap")
async def nmap_create(
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    name: str = Form("LAN Discovery"),
    cidrs: str = Form(...),
    excludes: str = Form(""),
    skip_dns: Optional[str] = Form("on"),
    use_syn: Optional[str] = Form(None),
    vuln_enabled: Optional[str] = Form(None),
    notes: str = Form(""),
    enabled: Optional[str] = Form("on"),
):
    try:
        row = nmap_cfg.create_nmap(
            session,
            name=name,
            cidrs=nmap_cfg.parse_cidrs_textarea(cidrs),
            excludes=nmap_cfg.parse_cidrs_textarea(excludes),
            skip_dns=skip_dns in ("on", "1", "true"),
            use_syn=use_syn in ("on", "1", "true"),
            vuln_enabled=vuln_enabled in ("on", "1", "true"),
            notes=notes,
            enabled=enabled in ("on", "1", "true") if enabled is not None else True,
        )
        nmap_cfg.refresh_status(session, row)
        _audit(session, user, "integration_created", details=f"nmap id={row.id}")
        return _redirect(f"/integrations/{row.id}", msg="created")
    except ValueError as e:
        return _redirect(
            "/integrations/new/nmap", error="invalid", detail=str(e)[:200]
        )
    except Exception as e:
        logger.exception("create nmap failed")
        return _redirect(
            "/integrations/new/nmap", error="save_failed", detail=str(e)[:200]
        )


@router.post("/integrations/{integration_id}/nmap/scan")
async def nmap_scan_now(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    intensity: str = Form(INTENSITY_DISCOVERY),
    targets: str = Form(""),
    vuln_scripts: Optional[str] = Form(None),
):
    integration = _require_nmap(session, integration_id)
    cfg = nmap_cfg.parse_nmap_config(integration)
    intensity = (intensity or INTENSITY_DISCOVERY).strip().lower()
    if intensity not in INTENSITIES:
        intensity = INTENSITY_DISCOVERY
    target_list = nmap_cfg.parse_cidrs_textarea(targets) or list(cfg.get("cidrs") or [])
    want_vuln = (
        vuln_scripts in ("on", "1", "true")
        and intensity == INTENSITY_DEEP
        and bool(cfg.get("vuln_enabled"))
    )
    try:
        job, run = enqueue_nmap_scan(
            session,
            integration_id=integration.id,
            intensity=intensity,
            targets=target_list,
            user_id=user.id,
            vuln_scripts=want_vuln,
        )
        _audit(
            session,
            user,
            "nmap_scan_queued",
            details=f"job={job.id} run={run.id} intensity={intensity}",
        )
        return _redirect(
            f"/integrations/{integration_id}",
            tab="runs",
            msg="scan_queued",
            detail=f"job #{job.id}",
        )
    except Exception as e:
        logger.exception("nmap scan enqueue failed")
        return _redirect(
            f"/integrations/{integration_id}",
            tab="overview",
            error="scan_failed",
            detail=str(e)[:200],
        )


@router.post("/integrations/{integration_id}/nmap/device/{device_id}/scan")
async def nmap_device_deep_scan(
    integration_id: int,
    device_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    vuln_scripts: Optional[str] = Form(None),
):
    integration = _require_nmap(session, integration_id)
    device = session.get(NmapDevice, device_id)
    if not device or device.integration_id != integration.id:
        raise HTTPException(404, "Device not found")
    cfg = nmap_cfg.parse_nmap_config(integration)
    want_vuln = vuln_scripts in ("on", "1", "true") and bool(cfg.get("vuln_enabled"))
    try:
        job, run = enqueue_nmap_scan(
            session,
            integration_id=integration.id,
            intensity=INTENSITY_DEEP,
            targets=[device.ip_address],
            user_id=user.id,
            vuln_scripts=want_vuln,
        )
        _audit(
            session,
            user,
            "nmap_host_deep_queued",
            details=f"device={device_id} ip={device.ip_address} job={job.id}",
        )
        return _redirect(
            f"/integrations/{integration_id}",
            tab="devices",
            device=str(device_id),
            msg="scan_queued",
            detail=f"deep job #{job.id}",
        )
    except Exception as e:
        return _redirect(
            f"/integrations/{integration_id}",
            tab="devices",
            device=str(device_id),
            error="scan_failed",
            detail=str(e)[:200],
        )


@router.post("/integrations/{integration_id}/nmap/device/{device_id}/ignore")
async def nmap_device_ignore(
    integration_id: int,
    device_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    integration = _require_nmap(session, integration_id)
    device = session.get(NmapDevice, device_id)
    if not device or device.integration_id != integration.id:
        raise HTTPException(404)
    nmap_cfg.set_device_state(session, device, "ignored")
    _audit(session, user, "nmap_device_ignored", details=f"device={device_id}")
    return _redirect(
        f"/integrations/{integration_id}", tab="devices", msg="device_ignored"
    )


@router.post("/integrations/{integration_id}/nmap/device/{device_id}/unignore")
async def nmap_device_unignore(
    integration_id: int,
    device_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    integration = _require_nmap(session, integration_id)
    device = session.get(NmapDevice, device_id)
    if not device or device.integration_id != integration.id:
        raise HTTPException(404)
    nmap_cfg.set_device_state(session, device, "known")
    _audit(session, user, "nmap_device_unignored", details=f"device={device_id}")
    return _redirect(
        f"/integrations/{integration_id}", tab="devices", msg="device_restored"
    )


@router.post("/integrations/{integration_id}/nmap/device/{device_id}/link")
async def nmap_device_link(
    integration_id: int,
    device_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    server_id: int = Form(...),
):
    integration = _require_nmap(session, integration_id)
    device = session.get(NmapDevice, device_id)
    if not device or device.integration_id != integration.id:
        raise HTTPException(404)
    server = session.get(Server, server_id)
    if not server:
        return _redirect(
            f"/integrations/{integration_id}",
            tab="devices",
            device=str(device_id),
            error="invalid",
            detail="server not found",
        )
    nmap_cfg.link_device(session, device, server_id)
    _audit(
        session,
        user,
        "nmap_device_linked",
        details=f"device={device_id} server={server_id}",
        server_id=server_id,
    )
    return _redirect(
        f"/integrations/{integration_id}",
        tab="devices",
        device=str(device_id),
        msg="device_linked",
    )


@router.post("/integrations/{integration_id}/nmap/device/{device_id}/unlink")
async def nmap_device_unlink(
    integration_id: int,
    device_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    integration = _require_nmap(session, integration_id)
    device = session.get(NmapDevice, device_id)
    if not device or device.integration_id != integration.id:
        raise HTTPException(404)
    nmap_cfg.unlink_device(session, device)
    _audit(session, user, "nmap_device_unlinked", details=f"device={device_id}")
    return _redirect(
        f"/integrations/{integration_id}",
        tab="devices",
        device=str(device_id),
        msg="device_unlinked",
    )


@router.post("/integrations/{integration_id}/nmap/schedules")
async def nmap_schedule_create(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    name: str = Form(...),
    intensity: str = Form("discovery"),
    cron: str = Form(""),
    interval_hours: Optional[str] = Form(""),
    enabled: Optional[str] = Form(None),
):
    _require_nmap(session, integration_id)
    try:
        ih = int(interval_hours) if (interval_hours or "").strip() else None
    except ValueError:
        ih = None
    try:
        row = nmap_sched.create_schedule(
            session,
            integration_id=integration_id,
            name=name,
            intensity=intensity,
            cron=(cron or "").strip() or None,
            interval_hours=ih,
            enabled=enabled in ("on", "1", "true"),
        )
        _resync_schedules()
        _audit(
            session,
            user,
            "nmap_schedule_created",
            details=f"id={row.id} intensity={row.intensity}",
        )
        return _redirect(
            f"/integrations/{integration_id}", tab="schedules", msg="schedule_saved"
        )
    except ValueError as e:
        return _redirect(
            f"/integrations/{integration_id}",
            tab="schedules",
            error="invalid",
            detail=str(e)[:200],
        )


@router.post("/integrations/{integration_id}/nmap/schedules/{schedule_id}/toggle")
async def nmap_schedule_toggle(
    integration_id: int,
    schedule_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    _require_nmap(session, integration_id)
    row = session.get(NmapScanSchedule, schedule_id)
    if not row or row.integration_id != integration_id:
        raise HTTPException(404)
    nmap_sched.update_schedule(session, row, enabled=not row.enabled)
    _resync_schedules()
    _audit(
        session,
        user,
        "nmap_schedule_toggled",
        details=f"id={schedule_id} enabled={row.enabled}",
    )
    return _redirect(
        f"/integrations/{integration_id}", tab="schedules", msg="schedule_saved"
    )


@router.post("/integrations/{integration_id}/nmap/schedules/{schedule_id}/delete")
async def nmap_schedule_delete(
    integration_id: int,
    schedule_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    _require_nmap(session, integration_id)
    row = session.get(NmapScanSchedule, schedule_id)
    if not row or row.integration_id != integration_id:
        raise HTTPException(404)
    nmap_sched.delete_schedule(session, row)
    _resync_schedules()
    _audit(session, user, "nmap_schedule_deleted", details=f"id={schedule_id}")
    return _redirect(
        f"/integrations/{integration_id}", tab="schedules", msg="schedule_deleted"
    )


@router.post("/integrations/{integration_id}/nmap/schedules/{schedule_id}/run")
async def nmap_schedule_run_now(
    integration_id: int,
    schedule_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
):
    _require_nmap(session, integration_id)
    row = session.get(NmapScanSchedule, schedule_id)
    if not row or row.integration_id != integration_id:
        raise HTTPException(404)
    nmap_sched.fire_schedule(schedule_id)
    _audit(session, user, "nmap_schedule_run", details=f"id={schedule_id}")
    return _redirect(
        f"/integrations/{integration_id}", tab="runs", msg="scan_queued"
    )

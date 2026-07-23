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
from ..models import (
    Integration,
    NmapDevice,
    NmapScanSchedule,
    NmapScriptResult,
    Server,
    User,
)
from ..security.auth import get_current_user, get_operator_user
from ..services.integrations import registry as reg
from ..services.nmap import config as nmap_cfg
from ..services.nmap import schedules as nmap_sched
from ..services.nmap.argv import INTENSITIES, INTENSITY_DEEP, INTENSITY_DISCOVERY
from ..services.nmap.options import (
    DEFAULT_TIMING,
    DEFAULT_TOP_PORTS,
    PORT_MODE_LABELS,
    PORT_MODES,
    SCRIPT_PRESET_LABELS,
    SCRIPT_PRESETS,
    form_scan_options,
    normalize_script_preset,
)
from ..services.nmap.paths import vuln_pack_status
from ..services.nmap.runtime import worker_online
from ..services.nmap.scan import enqueue_nmap_scan
from ..services.nmap.script_classify import (
    classify_scripts,
    ports_with_findings,
    script_summary_counts,
)
from ..services.nmap.vuln_update import enqueue_vuln_db_update
from .integrations_common import router, _audit, _redirect, _can_mutate

logger = logging.getLogger(__name__)


def _require_nmap(session: Session, integration_id: int) -> Integration:
    row = reg.get_integration(session, integration_id)
    if not row or row.type != reg.TYPE_NMAP:
        raise HTTPException(404, "LAN Discovery integration not found")
    return row


def _device_return_tab(raw: str | None) -> str:
    """Stay on Network or Devices after device form posts (modal UX)."""
    t = (raw or "").strip().lower()
    if t in ("network", "devices"):
        return t
    return "devices"


def _device_return_to(raw: str | None) -> str:
    """Optional cross-surface return: hosts → Catalog Hosts map."""
    t = (raw or "").strip().lower()
    if t in ("hosts", "hosts_map", "physical"):
        return "hosts"
    return ""


def _device_redirect(
    integration_id: int,
    device_id: int,
    *,
    return_tab: str | None = None,
    return_to: str | None = None,
    close: bool = False,
    **params,
):
    """Redirect after a device action.

    *close=True* omits ``device=`` so the edit modal does not reopen (Save and close).
    *focus* (optional in params) highlights the card after return.
    *return_to=hosts* after close → ``/dns/physical`` (Hosts map chip edit path).
    """
    dest = _device_return_to(return_to)
    if close and dest == "hosts":
        # Preserve focus-style flash id for Hosts if needed later; land on map
        return _redirect("/dns/physical", msg=params.get("msg") or "device_mapped")
    tab = _device_return_tab(return_tab)
    kw: dict = {"tab": tab, **params}
    if dest:
        kw["return"] = dest
    if close:
        # Highlight the edited host without reopening the modal
        kw.setdefault("focus", str(device_id))
    else:
        kw["device"] = str(device_id)
    return _redirect(f"/integrations/{integration_id}", **kw)

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
    device_rows = [nmap_cfg.device_list_item(d) for d in devices]
    # Lightweight stats for devices/network chrome
    device_stats = {
        "total": len(device_rows),
        "new": sum(1 for i in device_rows if i["row"].state == "new"),
        "linked": sum(1 for i in device_rows if i["row"].state == "linked"),
        "ignored": sum(1 for i in device_rows if i["row"].state == "ignored"),
        "open_ports": sum(i["open_ports"] for i in device_rows),
    }
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
    device_scripts: list[NmapScriptResult] = []
    if device_id:
        try:
            did = int(device_id)
            device = session.get(NmapDevice, did)
            if device and device.integration_id == integration.id:
                if device.ports_json:
                    try:
                        device_ports = json.loads(device.ports_json)
                        if not isinstance(device_ports, list):
                            device_ports = []
                    except Exception:
                        device_ports = []
                # open ports first, then closed/filtered
                def _port_key(p):
                    st = str((p or {}).get("state") or "").lower()
                    rank = 0 if st == "open" else 1
                    return (rank, int((p or {}).get("port") or 0))

                device_ports = sorted(device_ports, key=_port_key)
                device_scripts = list(
                    session.exec(
                        select(NmapScriptResult)
                        .where(NmapScriptResult.device_id == device.id)
                        .order_by(NmapScriptResult.id.desc())
                        .limit(40)
                    ).all()
                )
            else:
                device = None
        except ValueError:
            device = None

    device_scripts_classified = (
        classify_scripts(device_scripts, ports=device_ports) if device_scripts else []
    )
    device_script_counts = (
        script_summary_counts(device_scripts_classified)
        if device_scripts_classified
        else None
    )
    from ..services.nmap.device_classify import (
        KIND_CHOICES,
        MAP_ROLE_GATEWAY,
        MAP_ROLE_LABELS,
        profile_dict_from_device,
    )

    device_profile = None
    if device is not None:
        device_profile = profile_dict_from_device(device)
    kind_choices = list(KIND_CHOICES)
    return_to = _device_return_to(request.query_params.get("return"))
    # Annotate ports with finding/error counts for row highlight + anchors
    if device_ports and device_scripts_classified:
        device_ports = ports_with_findings(device_ports, device_scripts_classified)
    elif device_ports:
        device_ports = ports_with_findings(device_ports, [])

    # Schedule edit form (?tab=schedules&schedule=ID) or add modal (?tab=schedules&new=1)
    edit_schedule = None
    edit_schedule_opts: dict = {}
    schedule_new = False
    sid_raw = (request.query_params.get("schedule") or "").strip()
    if tab == "schedules" and sid_raw:
        try:
            sid = int(sid_raw)
            es = session.get(NmapScanSchedule, sid)
            if es and es.integration_id == integration.id:
                edit_schedule = es
                edit_schedule_opts = nmap_sched.parse_schedule_options(es)
        except ValueError:
            edit_schedule = None
    if tab == "schedules" and not edit_schedule:
        schedule_new = (request.query_params.get("new") or "").strip() == "1"

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
            "device_stats": device_stats,
            "state_filter": state_filter or "",
            "runs": runs,
            "schedules": schedules,
            "edit_schedule": edit_schedule,
            "edit_schedule_opts": edit_schedule_opts,
            "schedule_new": schedule_new,
            "network": network,
            "servers": servers,
            "device": device,
            "device_ports": device_ports,
            "device_scripts": device_scripts,
            "device_scripts_classified": device_scripts_classified,
            "device_script_counts": device_script_counts,
            "device_profile": device_profile,
            "kind_choices": kind_choices,
            "return_to": return_to,
            "map_role_labels": MAP_ROLE_LABELS,
            "map_role_gateway": MAP_ROLE_GATEWAY,
            "intensities": INTENSITIES,
            "schedule_intensities": nmap_sched.INTENSITIES_SCHEDULE,
            "script_presets": SCRIPT_PRESETS,
            "script_preset_labels": SCRIPT_PRESET_LABELS,
            "port_modes": PORT_MODES,
            "port_mode_labels": PORT_MODE_LABELS,
            "default_timing": DEFAULT_TIMING,
            "default_top_ports": DEFAULT_TOP_PORTS,
            "default_targets": ", ".join(cfg.get("cidrs") or []),
            "schedule_options": {
                s.id: nmap_sched.parse_schedule_options(s) for s in schedules
            }
            if schedules
            else {},
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
                "excludes_port_scans": "",
                "excludes_deep": "",
                "skip_dns": False,
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
    excludes_port_scans: str = Form(""),
    excludes_deep: str = Form(""),
    skip_dns: Optional[str] = Form(None),
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
            excludes_port_scans=nmap_cfg.parse_cidrs_textarea(excludes_port_scans),
            excludes_deep=nmap_cfg.parse_cidrs_textarea(excludes_deep),
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
    script_preset: Optional[str] = Form(None),
    timing: Optional[str] = Form(None),
    top_ports: Optional[str] = Form(None),
    include_udp: Optional[str] = Form(None),
    port_list: Optional[str] = Form(None),
    port_mode: Optional[str] = Form(None),
):
    integration = _require_nmap(session, integration_id)
    cfg = nmap_cfg.parse_nmap_config(integration)
    intensity = (intensity or INTENSITY_DISCOVERY).strip().lower()
    if intensity not in INTENSITIES:
        intensity = INTENSITY_DISCOVERY
    target_list = nmap_cfg.parse_cidrs_textarea(targets) or list(cfg.get("cidrs") or [])
    legacy_vuln = vuln_scripts in ("on", "1", "true")
    preset = normalize_script_preset(
        script_preset, vuln_scripts_fallback=legacy_vuln
    )
    if intensity != INTENSITY_DEEP or not bool(cfg.get("vuln_enabled")):
        preset = "none"
    opts = form_scan_options(
        script_preset=preset,
        vuln_scripts=preset != "none",
        timing=timing,
        top_ports=top_ports,
        include_udp=include_udp in ("on", "1", "true"),
        port_list=port_list,
        port_mode=port_mode,
    )
    try:
        job, run = enqueue_nmap_scan(
            session,
            integration_id=integration.id,
            intensity=intensity,
            targets=target_list,
            user_id=user.id,
            scan_options=opts,
        )
        _audit(
            session,
            user,
            "nmap_scan_queued",
            details=(
                f"job={job.id} run={run.id} intensity={intensity} "
                f"preset={opts.get('script_preset')}"
            ),
        )
        # Jobs page shows live log_tail (same pattern as OS updates)
        return _redirect("/jobs", job_type=job.job_type, active_only="1")
    except Exception as e:
        logger.exception("nmap scan enqueue failed")
        return _redirect(
            f"/integrations/{integration_id}",
            tab="overview",
            error="scan_failed",
            detail=str(e)[:200],
        )


@router.post("/integrations/{integration_id}/nmap/vuln-db-update")
async def nmap_vuln_db_update(
    integration_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    include_vulscan: Optional[str] = Form(None),
    include_exploitdb: Optional[str] = Form(None),
):
    """Queue vulnerability pack download on the nmap worker (progress on Jobs)."""
    _require_nmap(session, integration_id)
    try:
        job = enqueue_vuln_db_update(
            session,
            user_id=user.id,
            include_vulscan=include_vulscan in ("on", "1", "true"),
            include_exploitdb=include_exploitdb in ("on", "1", "true"),
        )
        _audit(
            session,
            user,
            "nmap_vuln_db_update_queued",
            details=f"job={job.id}",
        )
        return _redirect("/jobs", job_type="nmap_vuln_db_update", active_only="1")
    except Exception as e:
        logger.exception("vuln db update enqueue failed")
        return _redirect(
            f"/integrations/{integration_id}",
            tab="overview",
            error="vuln_update_failed",
            detail=str(e)[:200],
        )


@router.post("/integrations/{integration_id}/nmap/device/{device_id}/scan")
async def nmap_device_deep_scan(
    integration_id: int,
    device_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    vuln_scripts: Optional[str] = Form(None),
    script_preset: Optional[str] = Form(None),
    timing: Optional[str] = Form(None),
    include_udp: Optional[str] = Form(None),
    port_list: Optional[str] = Form(None),
    return_tab: str = Form(""),
):
    integration = _require_nmap(session, integration_id)
    device = session.get(NmapDevice, device_id)
    if not device or device.integration_id != integration.id:
        raise HTTPException(404, "Device not found")
    cfg = nmap_cfg.parse_nmap_config(integration)
    legacy_vuln = vuln_scripts in ("on", "1", "true")
    preset = normalize_script_preset(
        script_preset, vuln_scripts_fallback=legacy_vuln
    )
    if not bool(cfg.get("vuln_enabled")):
        preset = "none"
    opts = form_scan_options(
        script_preset=preset,
        vuln_scripts=preset != "none",
        timing=timing,
        include_udp=include_udp in ("on", "1", "true"),
        port_list=port_list,
    )
    try:
        job, run = enqueue_nmap_scan(
            session,
            integration_id=integration.id,
            intensity=INTENSITY_DEEP,
            targets=[device.ip_address],
            user_id=user.id,
            scan_options=opts,
        )
        _audit(
            session,
            user,
            "nmap_host_deep_queued",
            details=(
                f"device={device_id} ip={device.ip_address} job={job.id} "
                f"preset={opts.get('script_preset')}"
            ),
        )
        return _redirect("/jobs", job_type="nmap_host_deep", active_only="1")
    except Exception as e:
        return _device_redirect(
            integration_id,
            device_id,
            return_tab=return_tab,
            error="scan_failed",
            detail=str(e)[:200],
        )


@router.post("/integrations/{integration_id}/nmap/device/{device_id}/name")
async def nmap_device_set_name(
    integration_id: int,
    device_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    display_name: str = Form(""),
    kind_override: str = Form(""),
    map_role: str = Form(""),
    return_tab: str = Form(""),
    return_to: str = Form(""),
):
    """Map identity: name, device type override, optional gateway role.

    Accepts legacy name-only posts (kind/map_role empty → auto/none).
    """
    integration = _require_nmap(session, integration_id)
    device = session.get(NmapDevice, device_id)
    if not device or device.integration_id != integration.id:
        raise HTTPException(404, "Device not found")
    nmap_cfg.set_device_map_identity(
        session,
        device,
        display_name=display_name,
        kind_override=kind_override,
        map_role=map_role,
        sync_network_gateway=True,
    )
    _audit(
        session,
        user,
        "nmap_device_mapped",
        details=(
            f"device={device_id} name={(device.display_name or '')[:64]!r} "
            f"kind={(device.kind_override or 'auto')!r} "
            f"role={(device.map_role or '')!r}"
        ),
    )
    # Save and close: drop modal (or return to Hosts map)
    return _device_redirect(
        integration_id,
        device_id,
        return_tab=return_tab,
        return_to=return_to,
        close=True,
        msg="device_mapped",
    )


@router.post("/integrations/{integration_id}/nmap/device/{device_id}/ignore")
async def nmap_device_ignore(
    integration_id: int,
    device_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    return_tab: str = Form(""),
    return_to: str = Form(""),
):
    integration = _require_nmap(session, integration_id)
    device = session.get(NmapDevice, device_id)
    if not device or device.integration_id != integration.id:
        raise HTTPException(404)
    nmap_cfg.set_device_state(session, device, "ignored")
    _audit(session, user, "nmap_device_ignored", details=f"device={device_id}")
    return _device_redirect(
        integration_id,
        device_id,
        return_tab=return_tab,
        return_to=return_to,
        close=True,
        msg="device_ignored",
    )


@router.post("/integrations/{integration_id}/nmap/device/{device_id}/unignore")
async def nmap_device_unignore(
    integration_id: int,
    device_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    return_tab: str = Form(""),
    return_to: str = Form(""),
):
    integration = _require_nmap(session, integration_id)
    device = session.get(NmapDevice, device_id)
    if not device or device.integration_id != integration.id:
        raise HTTPException(404)
    nmap_cfg.mark_device_known(session, device)
    _audit(session, user, "nmap_device_unignored", details=f"device={device_id}")
    return _device_redirect(
        integration_id,
        device_id,
        return_tab=return_tab,
        return_to=return_to,
        close=True,
        msg="device_restored",
    )


@router.post("/integrations/{integration_id}/nmap/device/{device_id}/mark-known")
async def nmap_device_mark_known(
    integration_id: int,
    device_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    return_tab: str = Form(""),
    return_to: str = Form(""),
):
    """Mark as known / reviewed — clears the *new* inbox filter."""
    integration = _require_nmap(session, integration_id)
    device = session.get(NmapDevice, device_id)
    if not device or device.integration_id != integration.id:
        raise HTTPException(404, "Device not found")
    nmap_cfg.mark_device_known(session, device)
    _audit(session, user, "nmap_device_known", details=f"device={device_id}")
    return _device_redirect(
        integration_id,
        device_id,
        return_tab=return_tab,
        return_to=return_to,
        close=True,
        msg="device_known",
    )


@router.post("/integrations/{integration_id}/nmap/device/{device_id}/mark-new")
async def nmap_device_mark_new(
    integration_id: int,
    device_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    return_tab: str = Form(""),
    return_to: str = Form(""),
):
    """Re-flag as new (revisit). Unlink first if linked."""
    integration = _require_nmap(session, integration_id)
    device = session.get(NmapDevice, device_id)
    if not device or device.integration_id != integration.id:
        raise HTTPException(404, "Device not found")
    try:
        nmap_cfg.mark_device_new(session, device)
    except ValueError as e:
        return _device_redirect(
            integration_id,
            device_id,
            return_tab=return_tab,
            return_to=return_to,
            error="mark_new_failed",
            detail=str(e)[:200],
        )
    _audit(session, user, "nmap_device_mark_new", details=f"device={device_id}")
    return _device_redirect(
        integration_id,
        device_id,
        return_tab=return_tab,
        return_to=return_to,
        close=True,
        msg="device_new",
    )


@router.post("/integrations/{integration_id}/nmap/device/{device_id}/link")
async def nmap_device_link(
    integration_id: int,
    device_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    server_id: int = Form(...),
    return_tab: str = Form(""),
    return_to: str = Form(""),
):
    integration = _require_nmap(session, integration_id)
    device = session.get(NmapDevice, device_id)
    if not device or device.integration_id != integration.id:
        raise HTTPException(404)
    server = session.get(Server, server_id)
    if not server:
        return _device_redirect(
            integration_id,
            device_id,
            return_tab=return_tab,
            return_to=return_to,
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
    return _device_redirect(
        integration_id,
        device_id,
        return_tab=return_tab,
        return_to=return_to,
        close=True,
        msg="device_linked",
    )


@router.post("/integrations/{integration_id}/nmap/device/{device_id}/unlink")
async def nmap_device_unlink(
    integration_id: int,
    device_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    return_tab: str = Form(""),
    return_to: str = Form(""),
):
    integration = _require_nmap(session, integration_id)
    device = session.get(NmapDevice, device_id)
    if not device or device.integration_id != integration.id:
        raise HTTPException(404)
    nmap_cfg.unlink_device(session, device)
    _audit(session, user, "nmap_device_unlinked", details=f"device={device_id}")
    return _device_redirect(
        integration_id,
        device_id,
        return_tab=return_tab,
        return_to=return_to,
        close=True,
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
    vuln_scripts: Optional[str] = Form(None),
    script_preset: Optional[str] = Form(None),
    use_syn: Optional[str] = Form(""),
    timing: Optional[str] = Form(None),
    top_ports: Optional[str] = Form(None),
    include_udp: Optional[str] = Form(None),
    port_list: Optional[str] = Form(None),
):
    integration = _require_nmap(session, integration_id)
    try:
        ih = int(interval_hours) if (interval_hours or "").strip() else None
    except ValueError:
        ih = None
    use_syn_opt, _ = nmap_sched.parse_use_syn_form(use_syn)
    legacy_vuln = vuln_scripts in ("on", "1", "true")
    preset = normalize_script_preset(
        script_preset, vuln_scripts_fallback=legacy_vuln
    )
    try:
        t_raw = (timing or "").strip()
        timing_i = int(t_raw) if t_raw else DEFAULT_TIMING
    except ValueError:
        timing_i = DEFAULT_TIMING
    try:
        tp_raw = (top_ports or "").strip()
        top_ports_i = int(tp_raw) if tp_raw else DEFAULT_TOP_PORTS
    except ValueError:
        top_ports_i = DEFAULT_TOP_PORTS
    try:
        row = nmap_sched.create_schedule(
            session,
            integration_id=integration.id,
            name=name,
            intensity=intensity,
            cron=(cron or "").strip() or None,
            interval_hours=ih,
            enabled=enabled in ("on", "1", "true"),
            vuln_scripts=preset != "none",
            script_preset=preset,
            use_syn=use_syn_opt,
            timing=timing_i,
            top_ports=top_ports_i,
            include_udp=include_udp in ("on", "1", "true"),
            port_list=(port_list or "").strip() or None,
        )
        _resync_schedules()
        opts = nmap_sched.parse_schedule_options(row)
        _audit(
            session,
            user,
            "nmap_schedule_created",
            details=(
                f"id={row.id} intensity={row.intensity} "
                f"preset={opts.get('script_preset')} use_syn={opts.get('use_syn')}"
            ),
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


@router.post("/integrations/{integration_id}/nmap/schedules/{schedule_id}/edit")
async def nmap_schedule_edit(
    integration_id: int,
    schedule_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_operator_user),
    name: str = Form(...),
    intensity: str = Form("discovery"),
    cron: str = Form(""),
    interval_hours: Optional[str] = Form(""),
    enabled: Optional[str] = Form(None),
    vuln_scripts: Optional[str] = Form(None),
    script_preset: Optional[str] = Form(None),
    use_syn: Optional[str] = Form(""),
    timing: Optional[str] = Form(None),
    top_ports: Optional[str] = Form(None),
    include_udp: Optional[str] = Form(None),
    port_list: Optional[str] = Form(None),
):
    """Update an existing nmap scan schedule (name, cadence, curated options)."""
    _require_nmap(session, integration_id)
    row = session.get(NmapScanSchedule, schedule_id)
    if not row or row.integration_id != integration_id:
        raise HTTPException(404, "Schedule not found")
    try:
        ih_raw = (interval_hours or "").strip()
        ih = int(ih_raw) if ih_raw else None
    except ValueError:
        ih = None
    use_syn_opt, clear_syn = nmap_sched.parse_use_syn_form(use_syn)
    cron_s = (cron or "").strip()
    legacy_vuln = vuln_scripts in ("on", "1", "true")
    preset = normalize_script_preset(
        script_preset, vuln_scripts_fallback=legacy_vuln
    )
    try:
        t_raw = (timing or "").strip()
        timing_i = int(t_raw) if t_raw else DEFAULT_TIMING
    except ValueError:
        timing_i = DEFAULT_TIMING
    try:
        tp_raw = (top_ports or "").strip()
        top_ports_i = int(tp_raw) if tp_raw else DEFAULT_TOP_PORTS
    except ValueError:
        top_ports_i = DEFAULT_TOP_PORTS
    try:
        # When one of cron/interval is set, clear the other so edits stick.
        nmap_sched.update_schedule(
            session,
            row,
            name=name,
            intensity=intensity,
            cron=cron_s if cron_s else None,
            clear_cron=not cron_s,
            interval_hours=ih if ih else None,
            clear_interval=not ih,
            enabled=enabled in ("on", "1", "true"),
            script_preset=preset,
            use_syn=use_syn_opt if not clear_syn else None,
            clear_use_syn=clear_syn,
            timing=timing_i,
            top_ports=top_ports_i,
            include_udp=include_udp in ("on", "1", "true"),
            port_list=(port_list or "").strip() or None,
            clear_port_list=not (port_list or "").strip(),
        )
        _resync_schedules()
        opts = nmap_sched.parse_schedule_options(row)
        _audit(
            session,
            user,
            "nmap_schedule_updated",
            details=(
                f"id={schedule_id} intensity={row.intensity} "
                f"preset={opts.get('script_preset')} use_syn={opts.get('use_syn')}"
            ),
        )
        return _redirect(
            f"/integrations/{integration_id}",
            tab="schedules",
            msg="schedule_saved",
            detail=f"updated #{schedule_id}",
        )
    except ValueError as e:
        return _redirect(
            f"/integrations/{integration_id}",
            tab="schedules",
            schedule=str(schedule_id),
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
    # Run-now should fire even if schedule is currently disabled
    was_enabled = row.enabled
    if not was_enabled:
        row.enabled = True
        session.add(row)
        session.commit()
    try:
        nmap_sched.fire_schedule(schedule_id)
    finally:
        if not was_enabled:
            row2 = session.get(NmapScanSchedule, schedule_id)
            if row2:
                row2.enabled = False
                session.add(row2)
                session.commit()
    _audit(session, user, "nmap_schedule_run", details=f"id={schedule_id}")
    return _redirect("/jobs", active_only="1")

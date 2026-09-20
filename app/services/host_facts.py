"""DB-backed host OS/hardware snapshot (not live SSH on every page).

Scheduler refreshes about every 15 minutes. System Info default-reads this
row; Refresh now runs a ``host_facts`` job.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any, Optional, Set

from sqlmodel import Session

from ..database import engine
from ..models import Server

logger = logging.getLogger(__name__)

SCHEDULER_STALE_SEC = 900  # 15 minutes
_refreshing: Set[int] = set()
_refresh_lock = threading.Lock()


def parse_os_release_blob(text: str) -> tuple[Optional[str], Optional[str]]:
    """Return (id, pretty) from `ID=` / `PRETTY=` lines or os-release dump."""
    os_id = None
    pretty = None
    for raw in (text or "").splitlines():
        line = raw.strip().strip('"').strip("'")
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip().upper()
        val = val.strip().strip('"').strip("'")
        if key in ("ID", "OS_ID") and val:
            os_id = val.lower()
        elif key in ("PRETTY_NAME", "PRETTY", "NAME") and val and key != "NAME":
            pretty = val
        elif key == "NAME" and not pretty and val:
            pretty = val
    return os_id, pretty


def pick_hardware(
    *,
    device_tree: str | None = None,
    dmi_product: str | None = None,
    dmi_vendor: str | None = None,
    ha_chassis: str | None = None,
    ha_machine: str | None = None,
) -> Optional[str]:
    dt = (device_tree or "").strip()
    if dt:
        return dt[:120]
    chassis = (ha_chassis or "").strip()
    machine = (ha_machine or "").strip()
    if chassis and machine and chassis.lower() not in machine.lower():
        return f"{chassis} ({machine})"[:120]
    if chassis:
        return chassis[:120]
    if machine:
        return machine[:120]
    prod = (dmi_product or "").strip()
    vend = (dmi_vendor or "").strip()
    if prod and vend and vend.lower() not in prod.lower():
        return f"{vend} {prod}"[:120]
    return (prod or vend or None)


def family_from_ids(os_id: str | None, pretty: str | None, profile: str | None) -> Optional[str]:
    blob = f"{os_id or ''} {pretty or ''} {profile or ''}".lower()
    if "haos" in blob or "hassos" in blob or "home assistant os" in blob or (
        "home assistant" in blob and "os" in blob
    ):
        return "haos"
    oid = (os_id or "").lower()
    if oid in ("ubuntu",):
        return "ubuntu"
    if oid in ("raspbian", "raspberrypi", "raspios"):
        return "raspbian"
    if oid in ("debian",):
        return "debian"
    pl = (pretty or "").lower()
    if "ubuntu" in pl:
        return "ubuntu"
    if "raspberry" in pl:
        return "raspbian"
    if "debian" in pl:
        return "debian"
    return oid or None


def snapshot_from_server(server: Server) -> dict[str, Any]:
    raw = getattr(server, "host_facts_json", None)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data.setdefault("status", getattr(server, "host_facts_status", None) or "ok")
                data.setdefault("at", getattr(server, "host_facts_at", None))
                return data
        except Exception:
            pass
    return {
        "status": getattr(server, "host_facts_status", None) or "never",
        "error": getattr(server, "host_facts_error", None) or "No host facts yet",
        "hostname": server.hostname,
        "at": getattr(server, "host_facts_at", None),
    }


def apply_snapshot(session: Session, server: Server, info: dict[str, Any]) -> None:
    """Write high-level columns + JSON. Updates os_type when detection is confident."""
    os_id = (info.get("os_id") or "") or None
    pretty = (info.get("os_pretty") or info.get("os_version") or "") or None
    hardware = (info.get("hardware") or "") or None
    arch = (info.get("arch") or "") or None
    family = family_from_ids(os_id, pretty, info.get("profile"))
    server.os_pretty = (pretty or "")[:160] or None
    server.os_id = (os_id or family or "")[:32] or None
    server.hardware = (hardware or "")[:160] or None
    server.arch = (arch or "")[:32] or None
    current = (server.os_type or "").lower()
    if family == "haos":
        server.os_type = "haos"
    elif family == "ubuntu" and current in ("debian", "linux", "", "ubuntu"):
        server.os_type = "ubuntu"
    elif family == "raspbian" and current in ("debian", "linux", "", "raspbian"):
        server.os_type = "raspbian"
    payload = dict(info)
    payload["v"] = 1
    server.host_facts_json = json.dumps(payload, default=str, separators=(",", ":"))
    server.host_facts_at = datetime.utcnow()
    err = (info.get("error") or "")[:500]
    server.host_facts_status = "error" if err and not pretty and not hardware else "ok"
    server.host_facts_error = err or None
    session.add(server)
    session.commit()


def is_stale(server: Server, max_age_sec: int = SCHEDULER_STALE_SEC) -> bool:
    status = getattr(server, "host_facts_status", None) or "never"
    if status in ("never", "error", "stale"):
        return True
    if status == "refreshing":
        return False
    at = getattr(server, "host_facts_at", None)
    if not at:
        return True
    try:
        return (datetime.utcnow() - at).total_seconds() > max_age_sec
    except Exception:
        return True


def refresh_server_facts(server_id: int, *, force: bool = True) -> dict[str, Any]:
    from . import diagnostics as diag_svc

    with _refresh_lock:
        if server_id in _refreshing and not force:
            return {"status": "refreshing"}
        _refreshing.add(server_id)
    try:
        with Session(engine) as session:
            server = session.get(Server, server_id)
            if not server:
                return {"error": "server not found"}
            server.host_facts_status = "refreshing"
            session.add(server)
            session.commit()
            info = diag_svc.run_diagnostics(server, force=True)
            info = _enrich_from_diagnostics(info)
            apply_snapshot(session, server, info)
            return snapshot_from_server(server)
    except Exception as e:
        logger.warning("host_facts refresh failed for %s: %s", server_id, e)
        try:
            with Session(engine) as session:
                server = session.get(Server, server_id)
                if server:
                    server.host_facts_status = "error"
                    server.host_facts_error = str(e)[:500]
                    session.add(server)
                    session.commit()
        except Exception:
            pass
        return {"error": str(e)[:200], "status": "error"}
    finally:
        with _refresh_lock:
            _refreshing.discard(server_id)


def _enrich_from_diagnostics(info: dict[str, Any]) -> dict[str, Any]:
    pretty = info.get("os_pretty") or info.get("os_version")
    os_id = info.get("os_id")
    if pretty and not os_id:
        guessed, _ = parse_os_release_blob(f"PRETTY_NAME={pretty}")
        os_id = guessed
    host = ((info.get("ha") or {}) if isinstance(info.get("ha"), dict) else {}).get("host") or {}
    comps = ((info.get("ha") or {}) if isinstance(info.get("ha"), dict) else {}).get("components") or {}
    core = comps.get("core") or {}
    hardware = info.get("hardware") or pick_hardware(
        device_tree=info.get("device_tree"),
        dmi_product=info.get("dmi_product"),
        dmi_vendor=info.get("dmi_vendor"),
        ha_chassis=host.get("chassis"),
        ha_machine=core.get("machine") or host.get("machine"),
    )
    info["os_pretty"] = pretty
    info["os_id"] = os_id
    info["hardware"] = hardware
    return info


def request_refresh(server_id: int, *, force: bool = False) -> None:
    t = threading.Thread(
        target=refresh_server_facts,
        args=(server_id,),
        kwargs={"force": force},
        daemon=True,
        name=f"host-facts-{server_id}",
    )
    t.start()

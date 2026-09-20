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


_BYTE_U = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}


def parse_human_bytes(raw: str | None) -> Optional[int]:
    """Parse df-style sizes (15G, 512M, 1024) to bytes."""
    if raw is None:
        return None
    s = str(raw).strip().upper().replace(",", "")
    if not s or s in {"N/A", "?", "—", "-", "NONE"}:
        return None
    if s.endswith("B") and len(s) > 1:
        s = s[:-1]
    s = s.replace("IB", "").replace("I", "")
    mult = 1
    if s and s[-1] in _BYTE_U:
        mult = _BYTE_U[s[-1]]
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except (TypeError, ValueError):
        return None


def parse_meminfo_kb(text: str) -> tuple[Optional[int], Optional[int]]:
    """Return (total_bytes, used_bytes) from /proc/meminfo snippet."""
    total_kb = None
    avail_kb = None
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        key = parts[0].rstrip(":").lower()
        try:
            val = int(parts[1])
        except ValueError:
            continue
        if key == "memtotal":
            total_kb = val
        elif key == "memavailable":
            avail_kb = val
    if total_kb is None:
        return None, None
    total = total_kb * 1024
    used = None
    if avail_kb is not None:
        used = max(0, (total_kb - avail_kb) * 1024)
    return total, used


def parse_loadavg(text: str) -> Optional[float]:
    tok = (text or "").strip().split()
    if not tok:
        return None
    try:
        return round(float(tok[0]), 2)
    except ValueError:
        return None


def parse_nproc(text: str) -> Optional[int]:
    tok = (text or "").strip().split()
    if not tok:
        return None
    try:
        n = int(float(tok[0]))
        return n if n > 0 else None
    except ValueError:
        return None


def disk_bytes_from_summary(summary: dict | None) -> tuple[Optional[int], Optional[int]]:
    """Root (or first main) drive total/used bytes."""
    if not isinstance(summary, dict):
        return None, None
    root = summary.get("root") or {}
    total = parse_human_bytes(root.get("size") or summary.get("total_size"))
    used = parse_human_bytes(root.get("used") or summary.get("total_used"))
    return total, used


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
    data: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            pass
    at = getattr(server, "host_facts_at", None)
    data["status"] = getattr(server, "host_facts_status", None) or data.get("status") or "never"
    data["hostname"] = server.hostname
    data["hardware"] = getattr(server, "hardware", None) or data.get("hardware")
    data["arch"] = getattr(server, "arch", None) or data.get("arch")
    data["os_pretty"] = getattr(server, "os_pretty", None) or data.get("os_pretty") or data.get("os_version")
    data["os_id"] = getattr(server, "os_id", None) or data.get("os_id")
    data["os_version"] = data.get("os_pretty") or data.get("os_version")
    data["at"] = at.isoformat() + "Z" if at and not isinstance(at, str) else (at or data.get("at"))
    data["error"] = getattr(server, "host_facts_error", None) or data.get("error")
    data["cpu_cores"] = getattr(server, "cpu_cores", None)
    data["cpu_load"] = getattr(server, "cpu_load", None)
    data["memory_total_bytes"] = getattr(server, "memory_total_bytes", None)
    data["memory_used_bytes"] = getattr(server, "memory_used_bytes", None)
    data["disk_total_bytes"] = getattr(server, "disk_total_bytes", None)
    data["disk_used_bytes"] = getattr(server, "disk_used_bytes", None)
    if data["status"] == "never" and not data.get("os_pretty") and not data.get("hardware"):
        data["error"] = data.get("error") or "No host facts yet"
    return data


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
    cores = info.get("cpu_cores")
    load = info.get("cpu_load")
    mem_t, mem_u = info.get("memory_total_bytes"), info.get("memory_used_bytes")
    disk_t, disk_u = disk_bytes_from_summary(info.get("summary"))
    if info.get("disk_total_bytes") is not None:
        disk_t = info.get("disk_total_bytes")
    if info.get("disk_used_bytes") is not None:
        disk_u = info.get("disk_used_bytes")
    try:
        server.cpu_cores = int(cores) if cores is not None else None
    except (TypeError, ValueError):
        server.cpu_cores = None
    try:
        server.cpu_load = float(load) if load is not None else None
    except (TypeError, ValueError):
        server.cpu_load = None
    server.memory_total_bytes = int(mem_t) if mem_t is not None else None
    server.memory_used_bytes = int(mem_u) if mem_u is not None else None
    server.disk_total_bytes = int(disk_t) if disk_t is not None else None
    server.disk_used_bytes = int(disk_u) if disk_u is not None else None
    payload = dict(info)
    payload["v"] = 1
    payload["cpu_cores"] = server.cpu_cores
    payload["cpu_load"] = server.cpu_load
    payload["memory_total_bytes"] = server.memory_total_bytes
    payload["memory_used_bytes"] = server.memory_used_bytes
    payload["disk_total_bytes"] = server.disk_total_bytes
    payload["disk_used_bytes"] = server.disk_used_bytes
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

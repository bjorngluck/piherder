"""Diagnostics service (ping, DNS, uname, df, listening ports, etc.).

HAOS hosts also pull ``ha core|os|supervisor info`` and ``ha host info``
(disk free/used/total) for the System Info modal.
"""
import socket
import time
from typing import Optional
from ..models import Server
from .ssh import get_ssh_client, run_command

# Very small in-memory TTL cache to avoid hammering slow/unreachable hosts on every page load.
# Keyed by server.id. Helps with web responsiveness.
_diagnostics_cache: dict[int, tuple[dict, float]] = {}
_CACHE_TTL = 180  # seconds (3 minutes)


def ping(host: str, timeout=2) -> bool:
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, 22))
        return True
    except Exception:
        return False


def parse_df_h_output(out: str) -> list[dict]:
    """Parse classic ``df -h`` / ``df -hP`` table (BusyBox and GNU)."""
    drives: list[dict] = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("filesystem") or low.startswith("file system"):
            continue
        parts = line.split()
        # Filesystem Size Used Avail Use% Mounted on
        if len(parts) < 6:
            continue
        # Mount point may contain spaces (rare) — join from index 5
        # BusyBox: Use% is e.g. 12%  GNU same
        pcent = parts[4]
        if not (pcent.endswith("%") or pcent.replace(".", "", 1).isdigit()):
            # unexpected shape
            continue
        drives.append(
            {
                "filesystem": parts[0],
                "size": parts[1],
                "used": parts[2],
                "avail": parts[3],
                "pcent": pcent,
                "target": " ".join(parts[5:]),
            }
        )
    return drives


def _collect_df_drives(client) -> list[dict]:
    """GNU ``df --output`` when available; else plain ``df -h`` (HA SSH add-on)."""
    # GNU coreutils
    status, out, _ = run_command(
        client,
        "df -h --output=source,size,used,avail,pcent,target 2>/dev/null | tail -n +2",
        timeout=12,
    )
    drives: list[dict] = []
    if status == 0 and (out or "").strip():
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) >= 6:
                drives.append(
                    {
                        "filesystem": parts[0],
                        "size": parts[1],
                        "used": parts[2],
                        "avail": parts[3],
                        "pcent": parts[4],
                        "target": " ".join(parts[5:]),
                    }
                )
        if drives:
            return drives
    # BusyBox / Alpine (HA Terminal & SSH add-on)
    for cmd in (
        "df -hP 2>/dev/null",
        "df -h 2>/dev/null",
    ):
        status, out, _ = run_command(client, cmd, timeout=12)
        if status == 0 and (out or "").strip():
            drives = parse_df_h_output(out)
            if drives:
                return drives
    return []


def run_diagnostics(server: Server, force: bool = False) -> dict:
    """Run SSH-based diagnostics. Uses short TTL cache unless force=True.

    The cache prevents repeated expensive connects on page views / list refreshes
    which was causing perceived unresponsiveness.
    """
    key = server.id
    now = time.time()
    if not force and key in _diagnostics_cache:
        cached, ts = _diagnostics_cache[key]
        if now - ts < _CACHE_TTL:
            return cached

    info = {
        "hostname": server.hostname,
        "ping_ok": ping(server.hostname),
        "kernel": None,
        "os_version": None,
        "reboot_pending": False,
        "drives": [],
        "profile": None,  # "haos" when HA path used
        "ha": None,
        "error": None,
        "fetched_at": int(now),
    }
    try:
        client = get_ssh_client(server)

        # Kernel version
        status, out, _ = run_command(client, "uname -r", timeout=8)
        if status == 0:
            info["kernel"] = out.strip()

        # OS version (pretty name)
        status, out, _ = run_command(client, ". /etc/os-release 2>/dev/null && echo \"${PRETTY_NAME:-$NAME}\" | tr -d '\"' || uname -o", timeout=8)
        if status == 0 and out.strip():
            info["os_version"] = out.strip()

        # Reboot pending?
        status, out, _ = run_command(client, 'test -f /var/run/reboot-required && echo "yes" || echo "no"', timeout=4)
        info["reboot_pending"] = out.strip().lower() == "yes"

        # Disk usage — GNU df --output first; busybox/Alpine (HA SSH add-on) needs plain df -h
        info["drives"] = _collect_df_drives(client)

        # HAOS enrichment: versions + Supervisor disk metrics via ha CLI
        # Note: SSH add-on os-release is often Alpine — detect via ha CLI, not ID=hassos.
        try:
            from . import haos as haos_svc

            use_haos = haos_svc.is_haos_server(server)
            if not use_haos:
                try:
                    identity = haos_svc.probe_haos_identity(client)
                    use_haos = bool(identity.get("is_haos"))
                except Exception:
                    use_haos = False
            if use_haos:
                info["profile"] = "haos"
                panel = haos_svc.gather_system_panel(client)
                info["ha"] = panel
                host = (panel or {}).get("host") or {}
                # Prefer ha host kernel/OS strings when present (not Alpine add-on)
                if host.get("kernel"):
                    info["kernel"] = str(host["kernel"])
                if host.get("operating_system"):
                    info["os_version"] = str(host["operating_system"])
                # Prefer Supervisor disk numbers for summary when df is sparse/noisy
                if host.get("disk_total_h") or host.get("disk_free_gb") is not None:
                    pcent = host.get("disk_pcent")
                    pcent_s = f"{pcent}%" if pcent is not None else ""
                    info["ha_disk"] = {
                        "size": host.get("disk_total_h") or "?",
                        "used": host.get("disk_used_h") or "?",
                        "avail": host.get("disk_free_h") or "?",
                        "pcent": pcent_s,
                        "source": "ha host info",
                        "chassis": host.get("chassis"),
                        "disk_life_time": host.get("disk_life_time"),
                    }
                # Prefer Supervisor usage breakdown over noisy overlay mounts
                usage_drives = host.get("usage_drives") or []
                if usage_drives:
                    info["drives"] = usage_drives
                    info["drives_source"] = "ha host disks usage"
                elif info.get("ha_disk") and not info.get("drives"):
                    hd = info["ha_disk"]
                    info["drives"] = [
                        {
                            "filesystem": "ha-host",
                            "size": hd.get("size"),
                            "used": hd.get("used"),
                            "avail": hd.get("avail"),
                            "pcent": hd.get("pcent") or "",
                            "target": "/",
                        }
                    ]
                    info["drives_source"] = "ha host info"
        except Exception as e:
            info["ha"] = {"error": str(e)[:200]}

        client.close()
    except Exception as e:
        info["error"] = str(e)[:200]

    # Attach a usable space summary (root + main volumes)
    try:
        info["summary"] = summarize_usable_space(info.get("drives") or [])
    except Exception:
        info["summary"] = None

    # If HA disk metrics exist and root df is missing, surface them as summary.root-like
    try:
        if info.get("ha_disk") and not (info.get("summary") or {}).get("root"):
            hd = info["ha_disk"]
            fake_root = {
                "filesystem": "ha-host",
                "size": hd.get("size"),
                "used": hd.get("used"),
                "avail": hd.get("avail"),
                "pcent": hd.get("pcent") or "",
                "target": "/",
            }
            summary = info.get("summary") or {}
            summary["root"] = fake_root
            if not summary.get("main_drives"):
                summary["main_drives"] = [fake_root]
            summary["total_size"] = hd.get("size")
            summary["total_used"] = hd.get("used")
            summary["total_avail"] = hd.get("avail")
            summary["source"] = "ha host info"
            info["summary"] = summary
    except Exception:
        pass

    _diagnostics_cache[key] = (info, now)
    return info


def clear_diagnostics_cache(server_id: Optional[int] = None):
    if server_id is None:
        _diagnostics_cache.clear()
    else:
        _diagnostics_cache.pop(server_id, None)


def summarize_usable_space(drives: list[dict]) -> dict:
    """Produce a compact usable-space summary.

    Focus on the root filesystem and any user/home-like mounts.
    Exclude noise like tmpfs, /run, /dev, overlay, boot/efi etc.
    Returns totals + highlighted root entry.
    """
    if not drives:
        return {"root": None, "total_size": "n/a", "total_used": "n/a", "total_avail": "n/a", "main_drives": []}

    def _is_main(d):
        tgt = (d.get("target") or "").lower()
        fs = (d.get("filesystem") or "").lower()
        if any(x in tgt for x in ("/dev", "/run", "/sys", "/proc", "/snap", "/boot/efi", "tmpfs", "overlay", "cgroup")):
            return False
        if "tmpfs" in fs or "devtmpfs" in fs:
            return False
        return True

    main = [d for d in drives if _is_main(d)]

    # Prefer exact root
    root = next((d for d in main if d.get("target") == "/"), None) or next((d for d in main if d.get("target", "").rstrip("/") == ""), None)

    # Simple aggregate of main drives (may double count bind mounts in rare cases, but good enough for overview)
    def _parse_h(v):
        # very rough; the values are already human like "15G". We just display them.
        return v or "0"

    total_used = "?"
    total_size = "?"
    total_avail = "?"

    if main:
        # Best effort: use the root numbers if present, else sum rough (skip for human strings)
        if root:
            total_size = root.get("size", "?")
            total_used = root.get("used", "?")
            total_avail = root.get("avail", "?")
        else:
            total_size = main[0].get("size", "?") if main else "?"

    return {
        "root": root,
        "main_drives": main[:6],  # limit noise
        "total_size": total_size,
        "total_used": total_used,
        "total_avail": total_avail,
    }

"""Diagnostics service (ping, DNS, uname, df, listening ports, etc.)."""
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

        # Disk usage - human readable
        status, out, _ = run_command(client, "df -h --output=source,size,used,avail,pcent,target | tail -n +2", timeout=12)
        if status == 0:
            drives = []
            for line in out.strip().splitlines():
                parts = line.split()
                if len(parts) >= 6:
                    drives.append({
                        "filesystem": parts[0],
                        "size": parts[1],
                        "used": parts[2],
                        "avail": parts[3],
                        "pcent": parts[4],
                        "target": " ".join(parts[5:]),
                    })
            info["drives"] = drives

        client.close()
    except Exception as e:
        info["error"] = str(e)[:200]

    # Attach a usable space summary (root + main volumes)
    try:
        info["summary"] = summarize_usable_space(info.get("drives") or [])
    except Exception:
        info["summary"] = None

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

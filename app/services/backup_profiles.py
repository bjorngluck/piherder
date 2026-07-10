"""
Backup profiles, sources, and destination helpers.

Extracted from backup.py to keep core run_backup focused on execution.
Includes:
- Global + per-server backup defaults
- Source list management (add/remove, modern dict format)
- Destination folder computation
- Last backup time markers
- Size helpers

Public API re-exported from backup.py for compatibility.
Exact behavior preserved from original monolithic version.
"""

import json
import time
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
import subprocess

import logging

from ..models import Server
from ..config import settings
from .app_settings import format_datetime_in_app_tz

logger = logging.getLogger(__name__)


GLOBAL_BACKUP_DEFAULTS_FILE = Path(settings.BACKUP_ROOT) / ".global_backup_defaults.json"


def get_global_backup_defaults() -> dict:
    """Stub: return global defaults from file or empty."""
    try:
        if GLOBAL_BACKUP_DEFAULTS_FILE.exists():
            return json.loads(GLOBAL_BACKUP_DEFAULTS_FILE.read_text())
    except Exception:
        pass
    return {}


def save_global_backup_defaults(config: dict):
    """Stub: persist global defaults to file."""
    try:
        GLOBAL_BACKUP_DEFAULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        GLOBAL_BACKUP_DEFAULTS_FILE.write_text(json.dumps(config, indent=2))
    except Exception:
        pass


def get_backup_profiles_db(server: Server) -> List[Dict]:
    """UI-only: build profile list from Server row — no filesystem, no SSH."""
    sources = server.get_backup_sources()
    if not sources:
        sources = [
            {"source": p, "dest_name": None, "enabled": True}
            for p in server.get_backup_paths()
        ]

    root = server.backup_dest_root or settings.BACKUP_ROOT
    folder = server.backup_folder_name or server.hostname.replace("/", "_")
    last = server.last_backup_at

    profiles = []
    for item in sources:
        src = item["source"]
        dest_name = item.get("dest_name") or Path(src).name or "root"
        profiles.append({
            "source": src,
            "dest_name": dest_name,
            "destination": f"{root}/{folder}/{dest_name}",
            "enabled": item.get("enabled", True),
            "last_backup": last,
            "last_backup_str": format_datetime_in_app_tz(last) if last else "Never",
            "folder_name": dest_name,
        })
    return profiles


def global_backup_defaults_from_server(server: Server) -> dict:
    """DB/config only — no JSON file read on web."""
    return {
        "dest_root": server.backup_dest_root or settings.BACKUP_ROOT,
        "folder_name": server.backup_folder_name or server.hostname.replace("/", "_"),
        "sources": [p.get("source") for p in server.get_backup_sources()] or server.get_backup_paths(),
    }


def get_backup_profiles(server: Server, skip_fs: bool = False) -> List[Dict]:
    """Return rich backup profile info for UI."""
    start = time.time()

    sources = server.get_backup_sources()
    if not sources:
        g = get_global_backup_defaults()
        gsrc = g.get("sources", []) if isinstance(g, dict) else g
        if gsrc:
            sources = [{"source": s, "dest_name": None, "enabled": True} for s in gsrc]

    profiles = []

    for item in sources:
        src = item["source"]
        dest_name = item.get("dest_name") or Path(src).name or "root"
        enabled = item.get("enabled", True)
        dest = str(get_backup_root_for_server(server) / dest_name)
        last = None if skip_fs else get_last_backup_time_for_dest(server.hostname, dest_name)
        profiles.append({
            "source": src,
            "dest_name": dest_name,
            "destination": dest,
            "enabled": enabled,
            "last_backup": last,
            "last_backup_str": format_datetime_in_app_tz(last) if last else "Never",
            "folder_name": dest_name
        })

    took = time.time() - start
    if took > 0.8:
        logger.warning(f"[get_backup_profiles] Slow for {server.hostname}: {took:.2f}s")
    else:
        logger.debug(f"[get_backup_profiles] {server.hostname} took {took:.2f}s")

    return profiles


def get_last_backup_time_for_dest(hostname: str, dest_name: str) -> Optional[datetime]:
    """Check .last_backup for a specific dest subfolder.
    mtime is stored as unix time; we treat it as UTC for consistent display under selected TZ.
    """
    start = time.time()

    dest = Path(get_backup_root_for_server(hostname)) / dest_name
    marker = dest / ".last_backup"
    result = None
    if marker.exists():
        try:
            ts = marker.stat().st_mtime
            result = datetime.utcfromtimestamp(ts)
        except Exception:
            pass

    took = time.time() - start
    if took > 0.5:
        logger.warning(f"[get_last_backup_time] Slow FS check for {hostname}/{dest_name}: {took:.2f}s")

    return result


def get_backup_root_for_server(server_or_hostname) -> Path:
    """Return the root path for backups.
    Accepts Server object (for per-host overrides) or hostname str.
    """
    if isinstance(server_or_hostname, Server):
        s = server_or_hostname
        g = get_global_backup_defaults()
        root = s.backup_dest_root or g.get("dest_root") or settings.BACKUP_ROOT
        folder = s.backup_folder_name or g.get("folder_name") or s.hostname.replace("/", "_")
        return Path(root) / folder
    else:
        hostname = server_or_hostname
        g = get_global_backup_defaults()
        folder = g.get("folder_name") or hostname.replace("/", "_")
        root = g.get("dest_root") or settings.BACKUP_ROOT
        return Path(root) / folder


def get_destination_for_source(hostname: str, source: str) -> str:
    """Compute the destination folder name (matches original rsync behavior)."""
    folder_name = Path(source).name or "root"
    root = get_backup_root_for_server(hostname)
    return str(root / folder_name)


def get_last_backup_time(hostname: str, source: str) -> Optional[datetime]:
    """Read the .last_backup marker written by the backup script (most accurate, matches legacy scripts)."""
    dest = Path(get_destination_for_source(hostname, source))
    marker = dest / ".last_backup"
    if marker.exists():
        try:
            mtime = marker.stat().st_mtime
            return datetime.fromtimestamp(mtime)
        except Exception:
            return None
    return None


def get_dir_size(path: Path) -> int:
    """Fast size via du — avoid rglob walk on large backup trees."""
    try:
        proc = subprocess.run(
            ["du", "-sb", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return int(proc.stdout.split()[0])
    except Exception:
        pass
    return 0


def human_size(num_bytes: int) -> str:
    """Convert a byte count to a human-readable string (KB/MB/GB etc.)."""
    if num_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    unit_index = 0
    size = float(num_bytes)
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    return f"{size:.1f} {units[unit_index]}"


def add_backup_source(server: Server, source: str, dest_name: Optional[str] = None, session=None) -> bool:
    """Add a new flexible backup source. Supports custom dest folder name.

    Returns True on success. Raises ValueError if path policy rejects the source.
    """
    if not source or not source.strip():
        return False
    sources = server.get_backup_sources()
    source = source.strip()
    from .backup_path_policy import validate_backup_path, parse_rules

    ok, reason = validate_backup_path(source, parse_rules(getattr(server, "backup_path_rules", None)))
    if not ok:
        raise ValueError(reason or "Path not allowed by backup policy")
    # check for duplicate source
    if any(s["source"] == source for s in sources):
        return False
    sources.append({
        "source": source,
        "dest_name": dest_name.strip() if dest_name else None,
        "enabled": True
    })
    server.set_backup_sources(sources)
    if hasattr(server, 'backup_paths'):
        server.backup_paths = json.dumps(sources)
    if session:
        session.add(server)
        session.commit()
    return True


def remove_backup_source(server: Server, source: str, session=None) -> bool:
    sources = server.get_backup_sources()
    original_len = len(sources)
    sources = [s for s in sources if s["source"] != source]
    if len(sources) == original_len:
        return False
    if hasattr(server, 'backup_paths'):
        server.backup_paths = json.dumps(sources)
    if session:
        session.add(server)
        session.commit()
    return True


# Legacy compat shims (used by older code)
def add_backup_path(server: Server, new_path: str, session) -> bool:
    return add_backup_source(server, new_path, None, session)


def remove_backup_path(server: Server, path_to_remove: str, session) -> bool:
    return remove_backup_source(server, path_to_remove, session)

"""Server backup restore wizard — reverse rsync from local dest → remote source.

Safety:
- Default dry_run=True
- Path policy checked on remote target
- Requires existing local backup destination with content
"""
from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import settings
from ..models import Server
from . import backup_profiles
from .backup_path_policy import validate_backup_path, parse_rules
from .ssh import get_private_key_plain, temp_key_file, LEGACY_SSH_OPTS_STR

logger = logging.getLogger(__name__)


def list_restore_candidates(server: Server) -> List[Dict[str, Any]]:
    """List backup destinations that have local data and can be restored.

    Avoids recursive ``du`` on the Backups page (was multi-second on large trees).
    Top-level entry count is enough to know a dest has content.
    """
    profiles = backup_profiles.get_backup_profiles(server, skip_fs=False)
    out: List[Dict[str, Any]] = []
    for p in profiles:
        dest = Path(p.get("destination") or "")
        exists = dest.is_dir()
        size = 0
        file_count_hint = 0
        if exists:
            try:
                # cheap: top-level only (no recursive du — page-load latency)
                file_count_hint = sum(1 for _ in dest.iterdir())
            except Exception:
                file_count_hint = 0
        out.append({
            "source": p.get("source"),
            "dest_name": p.get("dest_name"),
            "destination": str(dest),
            "enabled": p.get("enabled", True),
            "exists": exists and file_count_hint > 0,
            "size_bytes": size,
            "entry_count": file_count_hint,
            "last_backup_str": p.get("last_backup_str") or "—",
        })
    return out


def restore_backup_source(
    server: Server,
    source: str,
    *,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Reverse rsync: local destination → remote source path.

    Returns dict with rc, dry_run, source, destination, output, error.
    """
    source = (source or "").strip()
    if not source:
        return {"error": "source path required", "rc": 1, "dry_run": dry_run}

    rules = parse_rules(getattr(server, "backup_path_rules", None))
    ok, reason = validate_backup_path(source, rules)
    if not ok:
        return {
            "error": f"Path policy blocked restore target: {reason}",
            "rc": 1,
            "dry_run": dry_run,
            "source": source,
        }

    # Find matching profile / destination
    dest_path: Optional[Path] = None
    for p in backup_profiles.get_backup_profiles_db(server):
        if (p.get("source") or "").rstrip("/") == source.rstrip("/"):
            dest_path = Path(p["destination"])
            break
    if dest_path is None:
        # fallback: compute dest
        dest_name = Path(source).name or "root"
        dest_path = backup_profiles.get_backup_root_for_server(server) / dest_name

    if not dest_path.is_dir():
        return {
            "error": f"No local backup at {dest_path}",
            "rc": 1,
            "dry_run": dry_run,
            "source": source,
            "destination": str(dest_path),
        }

    try:
        key_plain = get_private_key_plain(server)
    except Exception as e:
        return {
            "error": f"SSH key: {e}",
            "rc": 1,
            "dry_run": dry_run,
            "source": source,
        }
    if not key_plain:
        return {
            "error": "No SSH private key configured for this server",
            "rc": 1,
            "dry_run": dry_run,
            "source": source,
        }

    host = server.hostname
    port = int(server.ssh_port or 22)
    user = server.ssh_username or "root"
    remote = f"{user}@{host}:{source.rstrip('/')}/"
    local = str(dest_path).rstrip("/") + "/"

    # Prefer sudo rsync on remote (same as backup path) for non-root
    rsync_path = "rsync"
    if user not in ("root",) and "haos" not in (server.os_type or "").lower():
        rsync_path = "sudo -n rsync"

    with temp_key_file(key_plain) as key_path:
        ssh_cmd = f"ssh -i {shlex.quote(key_path)} -p {port} {LEGACY_SSH_OPTS_STR}"
        cmd = [
            "rsync",
            "-a",
            "--info=stats2",
            "-e",
            ssh_cmd,
            "--rsync-path",
            rsync_path,
        ]
        if dry_run:
            cmd.append("--dry-run")
        cmd.extend([local, remote])

        logger.info(
            "[restore] %s → %s (dry_run=%s)", local, remote, dry_run
        )
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            return {
                "rc": proc.returncode,
                "dry_run": dry_run,
                "source": source,
                "destination": str(dest_path),
                "remote": remote,
                "output": out[-4000:],
                "error": None if proc.returncode == 0 else f"rsync exit {proc.returncode}",
                "summary": (
                    f"{'Dry-run restore' if dry_run else 'Restore'} "
                    f"{'ok' if proc.returncode == 0 else 'failed'} · {source}"
                ),
            }
        except subprocess.TimeoutExpired:
            return {
                "error": "Restore timed out",
                "rc": 124,
                "dry_run": dry_run,
                "source": source,
                "destination": str(dest_path),
            }
        except Exception as e:
            return {
                "error": str(e)[:300],
                "rc": 1,
                "dry_run": dry_run,
                "source": source,
                "destination": str(dest_path),
            }

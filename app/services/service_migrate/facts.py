"""Optional SSH probes for migrate preflight (arch, disk, writable jail)."""
from __future__ import annotations

import logging
import shlex
from typing import Any, Optional

from ...models import Server
from ..ssh import expand_remote_path, get_ssh_client, run_command

logger = logging.getLogger(__name__)


def docker_base_abs(server: Server) -> str:
    user = (getattr(server, "ssh_username", None) or "pi").strip() or "pi"
    raw = (getattr(server, "docker_base_dir", None) or "~/docker").strip() or "~/docker"
    return expand_remote_path(raw, user)


def probe_host_facts(server: Server) -> dict[str, Any]:
    """Best-effort uname/df/writable. Never raises to the wizard."""
    out: dict[str, Any] = {
        "arch": None,
        "disk_free_bytes": None,
        "docker_base_writable": None,
        "docker_base": None,
        "error": None,
    }
    client = None
    try:
        client = get_ssh_client(server)
        base = docker_base_abs(server)
        out["docker_base"] = base
        st, stdout, _ = run_command(client, "uname -m", timeout=8)
        if st == 0:
            out["arch"] = (stdout or "").strip().split()[0] or None
        quoted = shlex.quote(base)
        st, stdout, _ = run_command(
            client,
            f"df -Pk {quoted} 2>/dev/null | tail -n 1",
            timeout=8,
        )
        if st == 0:
            parts = (stdout or "").split()
            # Filesystem 1024-blocks Used Available Capacity Mounted
            if len(parts) >= 4 and parts[3].isdigit():
                out["disk_free_bytes"] = int(parts[3]) * 1024
        st, stdout, _ = run_command(
            client,
            f"test -d {quoted} && test -w {quoted} && echo yes || echo no",
            timeout=8,
        )
        if st == 0:
            out["docker_base_writable"] = (stdout or "").strip().lower() == "yes"
    except Exception as e:
        out["error"] = str(e)[:200]
        logger.debug("migrate probe_host_facts failed: %s", e)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    return out


def herder_free_bytes() -> Optional[int]:
    import shutil

    from ...config import settings

    root = getattr(settings, "BACKUP_ROOT", None) or "/backups"
    try:
        return int(shutil.disk_usage(root).free)
    except Exception:
        return None

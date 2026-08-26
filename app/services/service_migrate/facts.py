"""Optional SSH probes for migrate preflight (arch, disk, writable jail)."""
from __future__ import annotations

import json
import logging
import shlex
from typing import Any, Optional

from ...models import Server
from ..ssh import expand_remote_path, get_ssh_client, run_command

logger = logging.getLogger(__name__)


def inspect_project_mounts(server: Server, project_row: dict[str, Any]) -> dict[str, Any]:
    """Fill mounts_detail from ``docker inspect`` (full Source paths, not docker ps)."""
    from ..docker_management import _parse_inspect_mount, normalize_container_ref

    containers = [
        c
        for c in (project_row.get("containers") or [])
        if isinstance(c, dict) and not c.get("placeholder")
    ]
    refs: list[str] = []
    for c in containers:
        for key in ("id_full", "id", "name"):
            ref = str(c.get(key) or "").strip()
            if ref:
                refs.append(ref)
                break
    if not refs:
        return project_row
    client = get_ssh_client(server)
    try:
        quoted = " ".join(shlex.quote(r) for r in refs[:40])
        st, out, _err = run_command(
            client, f"docker inspect {quoted} 2>/dev/null || true", timeout=60
        )
        if st != 0 or not (out or "").strip():
            return project_row
        try:
            data = json.loads(out)
        except Exception:
            return project_row
        if not isinstance(data, list):
            return project_row
        by_id: dict[str, list] = {}
        by_name: dict[str, list] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            raw = item.get("Mounts") or []
            if not isinstance(raw, list):
                raw = []
            structured = [
                _parse_inspect_mount(m) for m in raw if isinstance(m, dict)
            ]
            structured = [m for m in structured if m.get("source") or m.get("destination")]
            cid = str(item.get("Id") or "").strip()
            if cid.startswith("sha256:"):
                cid = cid[7:]
            if cid:
                by_id[cid] = structured
                by_id[cid[:12]] = structured
            nm = item.get("Name") or ""
            if isinstance(nm, str) and nm.strip():
                n = normalize_container_ref(nm)
                by_name[n] = structured
                by_name[nm.lstrip("/")] = structured
        for c in containers:
            full = str(c.get("id_full") or c.get("id") or "").strip()
            if full.startswith("sha256:"):
                full = full[7:]
            name = normalize_container_ref(c.get("name") or "")
            mounts = (
                by_id.get(full)
                or by_id.get(full[:12] if full else "")
                or by_name.get(name)
            )
            if mounts:
                c["mounts_detail"] = mounts
        return project_row
    finally:
        try:
            client.close()
        except Exception:
            pass


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

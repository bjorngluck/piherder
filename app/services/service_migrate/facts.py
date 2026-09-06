"""Optional SSH probes for migrate preflight (arch, disk, writable jail)."""
from __future__ import annotations

import json
import logging
import os
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
        runtime_by_id: dict[str, dict[str, Any]] = {}
        runtime_by_name: dict[str, dict[str, Any]] = {}

        def _store_runtime(key: str, into: dict[str, dict[str, Any]], rt: dict[str, Any]) -> None:
            if key:
                into[key] = rt

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
            hc = item.get("HostConfig") if isinstance(item.get("HostConfig"), dict) else {}
            cfg = item.get("Config") if isinstance(item.get("Config"), dict) else {}
            exposed_raw = cfg.get("ExposedPorts") or {}
            exposed = (
                [str(k) for k in exposed_raw.keys() if str(k).strip()]
                if isinstance(exposed_raw, dict)
                else []
            )
            rt = {
                "network_mode": str(hc.get("NetworkMode") or "").strip(),
                "privileged": bool(hc.get("Privileged")),
                "exposed_ports": exposed,
            }
            cid = str(item.get("Id") or "").strip()
            if cid.startswith("sha256:"):
                cid = cid[7:]
            if cid:
                by_id[cid] = structured
                by_id[cid[:12]] = structured
                _store_runtime(cid, runtime_by_id, rt)
                _store_runtime(cid[:12], runtime_by_id, rt)
            nm = item.get("Name") or ""
            if isinstance(nm, str) and nm.strip():
                n = normalize_container_ref(nm)
                by_name[n] = structured
                by_name[nm.lstrip("/")] = structured
                _store_runtime(n, runtime_by_name, rt)
                _store_runtime(nm.lstrip("/"), runtime_by_name, rt)
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
            rt = (
                runtime_by_id.get(full)
                or runtime_by_id.get(full[:12] if full else "")
                or runtime_by_name.get(name)
            )
            if rt:
                if rt.get("network_mode"):
                    c["network_mode"] = rt["network_mode"]
                if rt.get("exposed_ports"):
                    c["exposed_ports"] = rt["exposed_ports"]
                if rt.get("privileged"):
                    c["privileged"] = True
        return project_row
    finally:
        try:
            client.close()
        except Exception:
            pass


def list_project_tree(server: Server, project_path: str, *, limit: int = 60) -> list[str]:
    """Relative paths under the compose project dir (what the tree rsync copies)."""
    path = (project_path or "").rstrip("/")
    if not path or path == "/" or ".." in path:
        return []
    q = shlex.quote(path)
    client = get_ssh_client(server)
    try:
        st, out, _err = run_command(
            client,
            f"find {q} -mindepth 1 -maxdepth 4 \\( -type f -o -type d \\) "
            f"-printf '%P\\n' 2>/dev/null | head -n {int(limit)}",
            timeout=20,
        )
        if st != 0 or not (out or "").strip():
            return []
        return [ln.strip() for ln in out.splitlines() if ln.strip()][:limit]
    except Exception:
        return []
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


def _parse_listen_local(local: str, proto: str) -> Optional[tuple[str, str]]:
    addr = (local or "").strip()
    if not addr or addr in ("*", "Local"):
        return None
    if addr.startswith("[") and "]:" in addr:
        port = addr.rsplit("]:", 1)[-1]
    elif ":" in addr:
        port = addr.rsplit(":", 1)[-1]
    else:
        return None
    if not port.isdigit():
        return None
    return (port, proto)


def _parse_ss_listen(stdout: str, proto: str) -> list[tuple[str, str]]:
    used: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in (stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        # ss: LISTEN Recv-Q Send-Q Local Peer   OR  netstat: Proto Recv-Q Send-Q Local
        local = parts[3]
        if parts[0] not in ("LISTEN", "UNCONN", "udp", "tcp", "tcp6", "udp6") and ":" in parts[0]:
            local = parts[0]
        parsed = _parse_listen_local(local, proto)
        if parsed and parsed not in seen:
            seen.add(parsed)
            used.append(parsed)
    return used


def _parse_ps_ports(stdout: str) -> list[tuple[str, str]]:
    from ..dns_fabric.ports import parse_published_ports

    used: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in (stdout or "").splitlines():
        for pr in parse_published_ports(line):
            host = str(pr.get("host") or "").strip()
            proto = str(pr.get("proto") or "tcp").strip() or "tcp"
            key = (host, proto)
            if host and key not in seen:
                seen.add(key)
                used.append(key)
    return used


def probe_dest_occupancy(
    server: Server, dest_name: str, dest_path: str
) -> dict[str, Any]:
    """Live dest leftover: project dir, compose containers, published ports.

    Cached Docker inventory is not used. Recheck SSHs dest and reports what
    is actually there after a failed move / operator cleanup.
    """
    out: dict[str, Any] = {
        "path": dest_path,
        "nonempty": False,
        "files": [],
        "containers": [],
        "ports": [],
        "project_ports": [],
        "listen_ports": [],
        "error": None,
    }
    path = os.path.normpath((dest_path or "").strip())
    name = (dest_name or "").strip()
    if not path or path == "/" or ".." in path.split("/") or not name:
        out["error"] = "invalid dest path"
        return out
    client = None
    try:
        client = get_ssh_client(server)
        qpath = shlex.quote(path)
        st, stdout, _err = run_command(
            client,
            f"if [ -d {qpath} ]; then find {qpath} -mindepth 1 -maxdepth 2 "
            f"-printf '%P\\n' 2>/dev/null | head -n 20; fi",
            timeout=15,
        )
        if st == 0:
            files = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip()]
            out["files"] = files
            out["nonempty"] = bool(files)
        qname = shlex.quote(name)
        st, stdout, _err = run_command(
            client,
            "docker ps -a --filter "
            f"label=com.docker.compose.project={qname} "
            "--format '{{.Names}} {{.State}}' 2>/dev/null | head -n 20",
            timeout=20,
        )
        if st == 0:
            out["containers"] = [
                ln.strip() for ln in (stdout or "").splitlines() if ln.strip()
            ]
        st, stdout, _err = run_command(
            client,
            "docker ps --format '{{.Ports}}' 2>/dev/null | head -n 80",
            timeout=20,
        )
        if st == 0:
            out["ports"] = _parse_ps_ports(stdout or "")
        st, stdout, _err = run_command(
            client,
            "docker ps --filter "
            f"label=com.docker.compose.project={qname} "
            "--format '{{.Ports}}' 2>/dev/null | head -n 40",
            timeout=20,
        )
        if st == 0:
            out["project_ports"] = _parse_ps_ports(stdout or "")
        listen: list[tuple[str, str]] = []
        st, stdout, _err = run_command(
            client,
            "ss -lntH 2>/dev/null || netstat -lnt 2>/dev/null || true",
            timeout=10,
        )
        if st == 0:
            listen.extend(_parse_ss_listen(stdout or "", "tcp"))
        st, stdout, _err = run_command(
            client,
            "ss -lnuH 2>/dev/null || netstat -lnu 2>/dev/null || true",
            timeout=10,
        )
        if st == 0:
            listen.extend(_parse_ss_listen(stdout or "", "udp"))
        seen_l: set[tuple[str, str]] = set()
        uniq: list[tuple[str, str]] = []
        for item in listen:
            if item not in seen_l:
                seen_l.add(item)
                uniq.append(item)
        out["listen_ports"] = uniq
    except Exception as e:
        out["error"] = str(e)[:200]
        logger.debug("migrate probe_dest_occupancy failed: %s", e)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    return out


def remove_dest_project_ghosts(server: Server, dest_name: str) -> dict[str, Any]:
    """Remove leftover dest compose containers/networks for a retry.

    Failed dest ``up`` leaves ``created``/exited containers (e.g. ``openwebui``)
    after the operator emptied the dest folder. Move deletes those ghosts
    before dest ``up -d`` so retry is not blocked on ``docker rm``.
    """
    from .host_lock import compose_project_name

    name = compose_project_name(dest_name)
    qname = shlex.quote(name)
    out: dict[str, Any] = {"ok": True, "output": "", "error": None}
    client = None
    try:
        client = get_ssh_client(server)
        st, stdout, err = run_command(
            client,
            "ids=$(docker ps -aq --filter "
            f"label=com.docker.compose.project={qname} 2>/dev/null); "
            'if [ -n "$ids" ]; then docker rm -f $ids; echo removed_containers:$ids; '
            "else echo no_containers; fi; "
            "nids=$(docker network ls -q --filter "
            f"label=com.docker.compose.project={qname} 2>/dev/null); "
            'if [ -n "$nids" ]; then docker network rm $nids 2>/dev/null || true; '
            "echo removed_networks:$nids; fi",
            timeout=60,
        )
        out["ok"] = st == 0
        out["output"] = (stdout or "")[:800]
        if st != 0:
            out["error"] = ((err or stdout or "docker rm failed")[:400])
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)[:200]
        logger.debug("migrate remove_dest_project_ghosts failed: %s", e)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    return out


def refresh_host_inventory(server_id: Optional[int]) -> bool:
    """Replace PiHerder's dest/source Docker snapshot with a live L1 refresh."""
    if not server_id:
        return False
    try:
        from .. import docker_inventory as inventory_svc
        from .. import docker_management as docker_svc

        try:
            docker_svc._CACHE.clear()
        except Exception:
            pass
        return bool(inventory_svc.refresh_server_inventory(int(server_id), force=True))
    except Exception as e:
        logger.debug("migrate inventory refresh failed server=%s: %s", server_id, e)
        return False


def herder_free_bytes() -> Optional[int]:
    import shutil

    from ...config import settings

    root = getattr(settings, "BACKUP_ROOT", None) or "/backups"
    try:
        return int(shutil.disk_usage(root).free)
    except Exception:
        return None

"""
DB-backed Docker inventory snapshots.

Render stack UI from the last successful snapshot; refresh over SSH in the
background (same pattern as OS/container update counts).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional, Set

from sqlmodel import Session

from ..database import engine
from ..models import Server

logger = logging.getLogger(__name__)

# How long a snapshot is considered fresh enough to skip auto-refresh
DEFAULT_STALE_SEC = 120
# Periodic scheduler cadence (see scheduler registration)
SCHEDULER_STALE_SEC = 600

# Single-flight: server ids currently refreshing in this process
_refreshing: Set[int] = set()
_refresh_lock = threading.Lock()


def parse_inventory(server: Server) -> Optional[Dict[str, Any]]:
    """Return parsed inventory payload or None."""
    raw = getattr(server, "docker_inventory_json", None)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("v"):
            return data
    except Exception:
        pass
    return None


def inventory_meta(server: Server) -> Dict[str, Any]:
    """Lightweight meta for templates (dest cards, banners)."""
    inv = parse_inventory(server)
    meta = (inv or {}).get("meta") or {}
    status = getattr(server, "docker_inventory_status", None) or "never"
    return {
        "status": status,
        "at": getattr(server, "docker_inventory_at", None),
        "error": getattr(server, "docker_inventory_error", None),
        "project_count": meta.get("project_count"),
        "container_count": meta.get("container_count"),
        "duration_ms": meta.get("duration_ms"),
        "has_snapshot": inv is not None,
    }


def is_stale(server: Server, max_age_sec: int = DEFAULT_STALE_SEC) -> bool:
    """True if we should kick a background refresh."""
    status = getattr(server, "docker_inventory_status", None) or "never"
    if status == "never":
        return True
    if status == "error":
        return True
    if status == "stale":
        return True  # mutation invalidated; keep last JSON for display
    if status == "refreshing":
        return False  # already in flight (or stuck — force handles that)
    at = getattr(server, "docker_inventory_at", None)
    if not at:
        return True
    try:
        age = (datetime.utcnow() - at).total_seconds()
    except Exception:
        return True
    return age > max_age_sec


def is_refresh_stuck(server: Server, max_refresh_sec: int = 180) -> bool:
    """Treat long-running 'refreshing' as stuck so we can re-kick."""
    if (getattr(server, "docker_inventory_status", None) or "") != "refreshing":
        return False
    # Use inventory_at if present, else allow re-kick (no timestamp for started)
    # We store no dedicated started_at; if status is refreshing and process lock is empty, stuck.
    sid = server.id
    if sid is None:
        return True
    with _refresh_lock:
        if sid not in _refreshing:
            return True
    return False


def mark_stale(session: Session, server: Server) -> None:
    """Invalidate freshness without clearing the last good snapshot.

    Keeps ``docker_inventory_json`` + ``docker_inventory_at`` so the UI can still
    show the last known list with a correct timestamp while a refresh is queued.
    """
    if getattr(server, "docker_inventory_status", None) == "refreshing":
        return
    if server.docker_inventory_json:
        server.docker_inventory_status = "stale"
    else:
        server.docker_inventory_status = "never"
    session.add(server)
    session.commit()


def build_inventory_l1(server: Server) -> Dict[str, Any]:
    """SSH: L1 containers (no mount du) + light compose list + nest + update flags."""
    from . import docker_management as docker_svc

    t0 = time.time()
    containers = docker_svc.list_containers(server, enrich_mounts=False)
    # Surface docker ps hard failures early
    if containers and containers[0].get("name") == "error":
        raise RuntimeError(containers[0].get("status") or "docker ps failed")

    projects = docker_svc.list_compose_projects(server, light=True)
    projects, orphan_containers = docker_svc.nest_containers_under_projects(
        projects, containers
    )
    projects, orphan_containers = docker_svc.annotate_update_flags(
        projects, orphan_containers, server
    )
    duration_ms = int((time.time() - t0) * 1000)
    # Strip heavy fields that bloat JSON / aren't needed for list UI
    slim_projects = [_slim_project(p) for p in projects]
    slim_orphans = [_slim_container(c) for c in orphan_containers]
    real_containers = [
        c
        for p in slim_projects
        for c in (p.get("containers") or [])
        if not c.get("placeholder")
    ]
    return {
        "v": 1,
        "projects": slim_projects,
        "orphan_containers": slim_orphans,
        "meta": {
            "container_count": len(real_containers) + len(slim_orphans),
            "project_count": len(slim_projects),
            "duration_ms": duration_ms,
        },
    }


def _slim_container(c: Dict[str, Any]) -> Dict[str, Any]:
    """Keep fields docker_stack.html needs; drop mount sizes etc."""
    keep = (
        "id",
        "id_full",
        "name",
        "image",
        "version",
        "status",
        "state",
        "running",
        "ports",
        "ports_display",
        "created",
        "command",
        "mounts",
        "mounts_list",
        "size",
        "local_volumes",
        "compose_project",
        "compose_service",
        "compose_workdir",
        "placeholder",
        "has_pending_update",
    )
    return {k: c.get(k) for k in keep if k in c or k in ("name", "running")}


def _slim_project(p: Dict[str, Any]) -> Dict[str, Any]:
    row = {
        "name": p.get("name"),
        "path": p.get("path"),
        "compose_file": p.get("compose_file"),
        "versions": p.get("versions") or [],
        "services": p.get("services") or [],
        "build_services": p.get("build_services") or [],
        "has_build": bool(p.get("has_build")),
        "dockerfile_path": p.get("dockerfile_path"),
        "has_pending_update": bool(p.get("has_pending_update")),
        "update_container_count": p.get("update_container_count") or 0,
        "running_count": p.get("running_count") or 0,
        "container_count": p.get("container_count") or 0,
        "containers": [_slim_container(c) for c in (p.get("containers") or [])],
    }
    return row


def save_inventory(
    session: Session,
    server: Server,
    payload: Dict[str, Any],
    *,
    status: str = "ok",
    error: Optional[str] = None,
) -> None:
    server.docker_inventory_json = json.dumps(payload, separators=(",", ":"))
    server.docker_inventory_at = datetime.utcnow()
    server.docker_inventory_status = status
    server.docker_inventory_error = (error or "")[:500] or None
    session.add(server)
    session.commit()


def set_status(
    session: Session,
    server: Server,
    status: str,
    error: Optional[str] = None,
) -> None:
    server.docker_inventory_status = status
    if error is not None:
        server.docker_inventory_error = (error or "")[:500] or None
    session.add(server)
    session.commit()


def refresh_server_inventory(server_id: int, *, force: bool = False) -> bool:
    """Run L1 inventory over SSH and persist. Returns True on success.

    Safe to call from BackgroundTasks or scheduler. Single-flight per server.
    ``force`` is reserved for callers that already decided to refresh (feature
    gate bypass); it does not stack concurrent runs for the same server.
    """
    with _refresh_lock:
        if server_id in _refreshing:
            return False
        _refreshing.add(server_id)

    try:
        with Session(engine) as session:
            server = session.get(Server, server_id)
            if not server:
                return False
            # Feature gate: only hosts with container/docker feature (or forced)
            if not force and not server.container_patch_enabled:
                # Still allow if we already have inventory (was enabled before)
                if not server.docker_inventory_json:
                    return False
            set_status(session, server, "refreshing")
            # Detach plain values for SSH (Server stays attached)
            try:
                # Clear short in-process caches so refresh is real
                from . import docker_management as docker_svc

                try:
                    docker_svc._CACHE.clear()
                except Exception:
                    pass
                payload = build_inventory_l1(server)
                save_inventory(session, server, payload, status="ok", error=None)
                logger.info(
                    "docker inventory refreshed server=%s projects=%s containers=%s ms=%s",
                    server_id,
                    payload.get("meta", {}).get("project_count"),
                    payload.get("meta", {}).get("container_count"),
                    payload.get("meta", {}).get("duration_ms"),
                )
                return True
            except Exception as e:
                logger.warning("docker inventory refresh failed server=%s: %s", server_id, e)
                # Keep previous snapshot if any
                server = session.get(Server, server_id)
                if server:
                    if server.docker_inventory_json:
                        server.docker_inventory_status = "error"
                        server.docker_inventory_error = str(e)[:500]
                        session.add(server)
                        session.commit()
                    else:
                        set_status(session, server, "error", str(e)[:500])
                return False
    finally:
        with _refresh_lock:
            _refreshing.discard(server_id)


def try_begin_refresh(server_id: int) -> bool:
    """Reserve single-flight slot without running. Used by request path before BackgroundTasks."""
    with _refresh_lock:
        if server_id in _refreshing:
            return False
        _refreshing.add(server_id)
        return True


def end_refresh_slot(server_id: int) -> None:
    with _refresh_lock:
        _refreshing.discard(server_id)


def request_refresh(
    background_tasks,
    server_id: int,
    *,
    force: bool = False,
    server: Optional[Server] = None,
    session: Optional[Session] = None,
) -> bool:
    """Kick background refresh if needed. Returns True if a task was scheduled.

    ``background_tasks`` may be FastAPI BackgroundTasks or None (sync run — tests only).
    """
    if server is not None and not force:
        if not is_stale(server) and not is_refresh_stuck(server):
            return False
        if (
            (getattr(server, "docker_inventory_status", None) or "") == "refreshing"
            and not is_refresh_stuck(server)
        ):
            return False

    def _run():
        try:
            refresh_server_inventory(server_id, force=force)
        except Exception:
            logger.exception("inventory background refresh crashed server=%s", server_id)

    if background_tasks is None:
        return refresh_server_inventory(server_id, force=force)

    # Optimistic status so UI can show "Refreshing…" before the task starts
    if session is not None and server is not None:
        try:
            if (getattr(server, "docker_inventory_status", None) or "") != "refreshing":
                set_status(session, server, "refreshing")
        except Exception:
            pass

    background_tasks.add_task(_run)
    return True


def invalidate_after_mutation(session: Session, server: Server, background_tasks=None) -> None:
    """Call after docker mutations: clear in-mem cache, mark stale, optional BG refresh."""
    try:
        from . import docker_management as docker_svc

        docker_svc._CACHE.clear()
    except Exception:
        pass
    try:
        mark_stale(session, server)
    except Exception:
        pass
    if background_tasks is not None and server.id is not None:
        request_refresh(background_tasks, server.id, force=True, server=server)

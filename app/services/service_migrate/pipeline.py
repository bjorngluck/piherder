"""Stop-first migrate job: copy then dest up (v1.4 M3 + dest start)."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from sqlmodel import Session

from ...config import settings
from ...models import Server
from .. import docker_inventory as inventory_svc
from .. import docker_management as docker_svc
from .copy import copy_named_volume, rsync_herder_to_host, rsync_host_to_herder
from .cutover import CutoverError, retarget_dns_npm
from .facts import docker_base_abs
from .host_lock import compose_project_name
from .preflight import named_volume_id, run_preflight
from .rebind import rebind_control_plane
from .validate import ValidateError, validate_migrate

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]


class MigrateError(Exception):
    pass


def staging_root(job_id: int) -> Path:
    root = Path(getattr(settings, "BACKUP_ROOT", None) or "/backups") / "_migrate" / str(int(job_id))
    return root


def _log(log: Optional[LogFn], msg: str) -> None:
    if log:
        log(msg)
    else:
        logger.info("[migrate] %s", msg)


def run_copy_and_start(
    session: Session,
    *,
    source: Server,
    dest: Server,
    project: str,
    job_id: int,
    source_facts: Optional[dict[str, Any]] = None,
    dest_facts: Optional[dict[str, Any]] = None,
    herder_free: Optional[int] = None,
    log: Optional[LogFn] = None,
    pull_fn=None,
    push_fn=None,
    vol_fn=None,
    stop_fn=None,
    up_fn=None,
    cutover_fn=None,
    rebind_fn=None,
    validate_fn=None,
    leftover: str = "stopped",
    devices_ack: bool = False,
    down_fn=None,
) -> dict[str, Any]:
    """Preflight → stop → copy → dest up → DNS/NPM → rebind → validate."""
    name = compose_project_name(project)
    pf = run_preflight(
        session,
        source=source,
        dest=dest,
        project=name,
        source_facts=source_facts,
        dest_facts=dest_facts,
        herder_free=herder_free,
    )
    if not pf.get("ok"):
        msgs = "; ".join(b.get("message") or b.get("id") for b in pf.get("blocks") or [])
        raise MigrateError(f"preflight blocked: {msgs}")
    if any(w.get("id") == "devices" for w in (pf.get("warns") or [])) and not devices_ack:
        raise MigrateError(
            "Hardware-looking mounts: lock the project or acknowledge the warning"
        )

    pull = pull_fn or rsync_host_to_herder
    push = push_fn or rsync_herder_to_host
    vol = vol_fn or copy_named_volume
    stop = stop_fn or (lambda srv, path: docker_svc.compose_action(srv, path, "stop"))
    up = up_fn or (
        lambda srv, path: docker_svc.redeploy_project(srv, path, pull=True)
    )

    src_base = docker_base_abs(source).rstrip("/")
    dst_base = docker_base_abs(dest).rstrip("/")
    src_proj = f"{src_base}/{name}"
    dst_proj = f"{dst_base}/{name}"
    stage = staging_root(job_id)
    stage.mkdir(parents=True, exist_ok=True)
    try:
        stage.chmod(0o700)
    except Exception:
        pass
    proj_stage = stage / "project"
    proj_stage.mkdir(exist_ok=True)

    _log(log, f"Stopping {name} on {source.name}…")
    stopped = stop(source, src_proj)
    if isinstance(stopped, dict) and not stopped.get("success", True):
        raise MigrateError(stopped.get("error") or "compose stop failed")

    _log(log, f"Copy project tree {src_proj} → staging")
    pull(source, src_proj, proj_stage, log=log)

    dataset = pf.get("dataset") or {}
    seen_vol: set[str] = set()
    for it in dataset.get("items") or []:
        kind = it.get("kind")
        src = (it.get("source") or "").strip()
        vol_name = (it.get("volume") or "").strip() or named_volume_id(source=src) or ""
        if kind == "named":
            vol_name = vol_name or src
            if not vol_name or vol_name in seen_vol:
                continue
            seen_vol.add(vol_name)
            _log(log, f"Copy named volume {vol_name}")
            vol(source, dest, vol_name, stage, log=log)
        elif kind == "bind_absolute" and src.startswith(src_base + "/"):
            rel = src[len(src_base) :]
            extra = stage / "binds" / rel.lstrip("/")
            extra.mkdir(parents=True, exist_ok=True)
            _log(log, f"Copy bind {src}")
            pull(source, src, extra, log=log)
            dest_bind = dst_base + rel
            push(dest, extra, dest_bind, log=log)

    _log(log, f"Push project tree → {dst_proj}")
    push(dest, proj_stage, dst_proj, log=log)

    _log(log, f"Starting {name} on {dest.name} (compose up -d)…")
    started = up(dest, dst_proj)
    if isinstance(started, dict) and not started.get("success", True):
        raise MigrateError(started.get("error") or started.get("output") or "dest up failed")

    dns_fn = cutover_fn or retarget_dns_npm
    _log(log, "Retargeting DNS / NPM…")
    try:
        dns_out = dns_fn(session, source=source, dest=dest, project=name, log=log)
    except CutoverError as e:
        raise MigrateError(str(e)) from e

    rb_fn = rebind_fn or rebind_control_plane
    _log(log, "Rebinding control-plane rows…")
    rebind_out = rb_fn(session, source=source, dest=dest, project=name, log=log)

    val_fn = validate_fn or validate_migrate
    _log(log, "Validating TLS / Kuma…")
    try:
        val_out = val_fn(session, source=source, dest=dest, project=name, log=log)
    except ValidateError as e:
        raise MigrateError(str(e)) from e

    leftover_mode = (leftover or "stopped").strip().lower()
    if leftover_mode == "down":
        down = down_fn or (lambda srv, path: docker_svc.compose_action(srv, path, "down"))
        _log(log, f"compose down on source {source.name} (volumes kept)")
        stopped = down(source, src_proj)
        if isinstance(stopped, dict) and not stopped.get("success", True):
            raise MigrateError(stopped.get("error") or "source compose down failed")

    try:
        inventory_svc.mark_stale(session, source)
        inventory_svc.mark_stale(session, dest)
    except Exception:
        pass

    _log(log, "Migrate complete.")
    return {
        "ok": True,
        "project": name,
        "source_id": source.id,
        "dest_id": dest.id,
        "staging": str(stage),
        "dns": dns_out if isinstance(dns_out, dict) else {"ok": True},
        "rebind": rebind_out if isinstance(rebind_out, dict) else {"ok": True},
        "validate": val_out if isinstance(val_out, dict) else {"ok": True},
        "leftover": leftover_mode,
    }


def wipe_staging(job_id: int) -> None:
    root = staging_root(job_id)
    if root.is_dir() and "_migrate" in str(root):
        shutil.rmtree(root, ignore_errors=True)
